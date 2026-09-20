"""Corridor / flyover analysis: segmentation, scoring, bottlenecks, candidate optimisation, honesty about missing data, API and auth."""
import httpx
import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app import auth, corridor as K, providers
from app.api import app

LINE = lambda a, b, n=41: [[a[1] + (b[1] - a[1]) * t / (n - 1), a[0] + (b[0] - a[0]) * t / (n - 1)] for t in range(n)]      # [lon, lat]
BUSY = LINE((17.4399, 78.4983), (17.4058, 78.5591))            # through the busiest part of the simulated grid
CALM = LINE((17.30, 78.44), (17.30, 78.54))                    # along the quiet south edge
OUTSIDE = LINE((17.60, 78.30), (17.62, 78.36))                 # inside the service area, far from the simulated grid


def osrm(coords, roads=True):
    steps = [{"name": "Test Road", "distance": 9000, "intersections": [{"location": coords[i], "bearings": [0, 90, 180]} for i in range(0, len(coords), 4)]}]
    return httpx.Response(200, json={"code": "Ok", "routes": [{"distance": 9000, "duration": 900, "geometry": {"type": "LineString", "coordinates": coords},
                                                                "legs": [{"steps": steps}] if roads else []}]})


@pytest.fixture(autouse=True)
def fresh():
    providers.route_cache.clear()
    K._CACHE.clear()


def route(monkeypatch, coords, **kw):
    monkeypatch.setattr(providers, "client", lambda: httpx.Client(transport=httpx.MockTransport(lambda r: osrm(coords, **kw))))


@pytest.fixture()
def client():
    c = TestClient(app)
    c.cookies.set(auth.COOKIE, auth.issue_token(auth.LOGIN_ID))
    return c


def body(coords):
    a, b = coords[0], coords[-1]
    return {"origin": {"lat": a[1], "lng": a[0]}, "destination": {"lat": b[1], "lng": b[0]}}


# ---- pure logic ------------------------------------------------------------------------------------------------------------
def test_scores_ignore_missing_components_and_renormalise_instead_of_inventing_values():
    score, used, dropped = K.wmean({"a": 80.0, "b": None, "c": 40.0}, {"a": 0.5, "b": 0.3, "c": 0.5})
    assert score == pytest.approx(60.0) and dropped == ["b"] and sum(used.values()) == pytest.approx(1.0)
    assert K.wmean({"a": None}, {"a": 1})[0] is None


def test_bottleneck_levels_follow_the_configurable_thresholds():
    lv = K.cfg()["bottleneck_levels"]
    assert [K.level_of(x) for x in (lv["MODERATE"] - 1, lv["MODERATE"], lv["HIGH"], lv["CRITICAL"], None)] == ["LOW", "MODERATE", "HIGH", "CRITICAL", "NO_DATA"]


def test_segmentation_follows_the_road_geometry_and_covers_it_exactly():
    r = {"coords": [[78.4, 17.4], [78.4, 17.41], [78.41, 17.41]]}            # an L-shaped road: a straight origin-destination line would cut the corner
    segs = K.segment_route(r)
    assert segs[0]["start_m"] == 0 and segs[-1]["end_m"] == pytest.approx(sum(s["length_m"] for s in segs))
    assert all(a["end"] == b["start"] for a, b in zip(segs, segs[1:]))
    assert any(len(s["coords"]) > 2 for s in segs)                           # the corner vertex is kept
    with pytest.raises(K.CorridorError):
        K.segment_route({"coords": [[78.4, 17.4], [78.4, 17.4001]]})        # too short


def seg(i, score, start=None, length=300.0):
    st = i * length if start is None else start
    return {"index": i, "segment_id": f"S{i + 1:02d}", "start_m": st, "end_m": st + length, "length_m": length, "bottleneck_score": score}


def test_candidate_zones_group_neighbours_and_optimise_start_end():
    scores = [10, 10, 30, 60, 80, 20, 70, 10, 10, 10, 10, 10]                # HIGH at 3, 4, 6 (gap of one at 5 is merged)
    segs = [seg(i, v) for i, v in enumerate(scores)]
    zones = K.find_candidates(segs, 12 * 300)
    assert len(zones) == 1
    z = zones[0]
    assert z["seeds"] == [3, 4, 6] and z["first"] <= 3 and z["last"] >= 6
    assert segs[z["first"]]["bottleneck_score"] >= 25 or z["first"] == 3        # extended only over MODERATE+ neighbours, never over LOW ones
    assert z["start_m"] == segs[z["first"]]["start_m"] - K.cfg()["candidate"]["ramp_margin_m"]        # approach margin on each side
    assert z["end_m"] - z["start_m"] <= K.cfg()["candidate"]["max_length_m"] + 2 * K.cfg()["candidate"]["ramp_margin_m"]


def test_no_high_segments_means_no_candidates_and_missing_data_breaks_a_zone():
    assert K.find_candidates([seg(i, 20) for i in range(8)], 2400) == []
    segs = [seg(i, v) for i, v in enumerate([80, 80, None, None, 80, 80])]
    assert len(K.find_candidates(segs, 1800)) == 2                            # a no-data gap is never bridged


def test_a_long_bottleneck_is_split_into_candidates_no_longer_than_the_maximum():
    segs = [seg(i, 85) for i in range(20)]                                    # 6 km of critical road
    zones = K.find_candidates(segs, 6000)
    assert len(zones) >= 3 and all(z["end_m"] - z["start_m"] <= K.cfg()["candidate"]["max_length_m"] + 300 for z in zones)


# ---- end to end on real data -----------------------------------------------------------------------------------------------
def test_busy_corridor_yields_ranked_candidates_with_explanations_alternatives_and_confidence(monkeypatch):
    route(monkeypatch, BUSY)
    r = K.analyze((17.4399, 78.4983), (17.4058, 78.5591))
    assert r["mode"] == "simulation" and "SIMULATION" in r["data_notice"] and r["provider"]["live"] is False
    assert r["candidates"], "the busiest part of the grid should contain at least one HIGH bottleneck"
    c = r["candidates"][0]
    assert 0 <= c["suitability"]["score"] <= 100 and c["start"] != c["end"] and c["estimated_length_m"] > 0
    assert set(c["bottleneck_segments"]) <= set(c["affected_segments"])
    assert c["explanation"] and all(e["text"] and e["factor"] for e in c["explanation"])
    assert "feasibility" in c["suitability"]["excluded"] and c["suitability"]["excluded"]["pedestrian"] == "data_required"
    assert {o["name"] for o in c["alternatives"]["options"]} >= {"Signal optimization", "Junction redesign", "Bus priority", "Underpass"}
    assert next(o for o in c["alternatives"]["options"] if o["name"] == "Bus priority")["supported_by_data"] is None       # cannot be assessed, not guessed
    conf = r["confidence"]
    assert 0 <= conf["score"] <= 100 and "not a probability" in conf["meaning"] and "pedestrian_activity" in conf["missing_fields"]
    assert [s["stage"] for s in r["stages"]][0] == "Generating route" and r["disclaimer"]["layers"][1].startswith("Engineering feasibility")
    ok = [s for s in r["segments"] if s["data_status"] == "MATCHED"]
    assert ok and all(s["unavailable"]["pedestrian_activity"] == "data_required" and s["unavailable"]["road_width"] == "data_required" for s in ok)
    assert [s["road_name"] for s in r["segments"]] == ["Test Road"] * len(r["segments"])


def test_start_and_end_lie_on_the_route_inside_the_corridor(monkeypatch):
    route(monkeypatch, BUSY)
    r = K.analyze((17.4399, 78.4983), (17.4058, 78.5591))
    lons = [c[0] for c in BUSY]
    for c in r["candidates"]:
        for p in (c["start"], c["end"]):
            assert min(lons) - 1e-4 <= p[1] <= max(lons) + 1e-4
        assert c["start_m"] < c["end_m"] <= r["corridor"]["distance_km"] * 1000 + 1


def test_calm_corridor_has_no_candidates_and_says_so(monkeypatch):
    route(monkeypatch, CALM)
    quiet = {**K.cfg(), "bottleneck_levels": {"MODERATE": 90, "HIGH": 95, "CRITICAL": 99}}       # thresholds are configuration: a corridor with no bottleneck at these levels
    monkeypatch.setattr(K, "cfg", lambda: quiet)
    r = K.analyze((17.30, 78.44), (17.30, 78.54))
    assert r["candidates"] == [] and r["recommended_analysis"] is None and r["bottlenecks"]["segments"] == []


def test_route_outside_the_simulated_area_has_no_data_and_low_confidence(monkeypatch):
    route(monkeypatch, OUTSIDE)
    r = K.analyze((17.60, 78.30), (17.62, 78.36))
    assert r["corridor"]["coverage_pct"] == 0 and r["corridor"]["traffic_status"] == "NO_DATA" and r["candidates"] == []
    assert all(s["bottleneck_score"] is None and s["features"] is None for s in r["segments"])
    assert r["confidence"]["components"]["coverage"] == 0


def test_missing_road_names_and_intersections_are_reported_as_unavailable_not_zero(monkeypatch):
    route(monkeypatch, BUSY, roads=False)
    r = K.analyze((17.4399, 78.4983), (17.4058, 78.5591))
    s = r["segments"][0]
    assert s["road_name"] is None and s["junction_count"] is None and "unavailable" in s["unavailable"]["road_name"]


def test_missing_history_drops_the_growth_component(monkeypatch):
    stats = K.segment_stats().copy()
    stats["growth_pct"] = np.nan
    monkeypatch.setattr(K, "segment_stats", lambda: stats)
    c = K.suitability([{"data_status": "MATCHED", "length_m": 300, "bottleneck_score": 60, "_raw": stats.iloc[0]}])
    assert c["breakdown"]["historical_growth"] is None and "historical_growth" in c["excluded"]


def test_evaluation_reports_only_measured_numbers():
    ev = K.evaluate()
    assert 0 <= ev["roc_auc"] <= 1 and ev["reference_positives"] > 0 and "cannot be validated" in ev["note"]


# ---- API -------------------------------------------------------------------------------------------------------------------
def test_api_requires_the_operator_session():
    c = TestClient(app)
    for m, p in (("post", "/api/corridor/analyze"), ("get", "/api/corridor/abc"), ("post", "/api/flyover/analyze"), ("post", "/api/traffic/predict"), ("get", "/api/flyover/model-info")):
        assert getattr(c, m)(p, **({"json": {}} if m == "post" else {})).status_code == 401


def test_api_flow_and_sub_resources(client, monkeypatch):
    route(monkeypatch, BUSY)
    r = client.post("/api/corridor/analyze", json=body(BUSY))
    assert r.status_code == 200
    j = r.json()
    cid = j["id"]
    assert client.get(f"/api/corridor/{cid}").json()["id"] == cid
    assert len(client.get(f"/api/corridor/{cid}/segments").json()["segments"]) == j["corridor"]["segments"]
    assert client.get(f"/api/corridor/{cid}/bottlenecks").json()["counts"] == j["bottlenecks"]["counts"]
    assert client.get(f"/api/corridor/{cid}/candidates").json()["candidates"] == j["candidates"]
    f = client.post("/api/flyover/analyze", json=body(BUSY)).json()
    assert f["recommended_analysis"]["name"] == j["recommended_analysis"]["name"] and "coords" not in f["corridor"]
    assert client.get("/api/corridor/doesnotexist").status_code == 404
    seg_id = next(s["grid_segment_id"] for s in j["segments"] if s["grid_segment_id"])
    p = client.post("/api/traffic/predict", json={"segment_ids": [seg_id]})
    assert p.status_code == 200 and p.json()["predictions"][0]["forecast"]["horizons"]
    assert "not real-time" in p.json()["note"]
    assert client.post("/api/traffic/predict", json={"segment_ids": ["NOPE"]}).status_code == 422


def test_api_rejects_bad_input_in_plain_language(client, monkeypatch):
    route(monkeypatch, BUSY)
    ok = body(BUSY)
    assert client.post("/api/corridor/analyze", json={**ok, "origin": {"lat": 999, "lng": 78}}).status_code == 422       # invalid coordinate
    assert client.post("/api/corridor/analyze", json={**ok, "origin": {"lat": 28.6, "lng": 77.2}}).json()["detail"].startswith("The origin is outside")
    assert client.post("/api/corridor/analyze", json={**ok, "destination": ok["origin"]}).status_code == 422             # identical points (too close)
    assert client.post("/api/corridor/analyze", json={"origin": ok["origin"]}).status_code == 422
    far = {"origin": {"lat": 17.15, "lng": 78.15}, "destination": {"lat": 17.65, "lng": 78.75}}
    assert "limited to" in client.post("/api/corridor/analyze", json=far).json()["detail"]


def test_api_reports_routing_failures_and_empty_routes_without_a_stack_trace(client, monkeypatch):
    monkeypatch.setattr(providers, "client", lambda: httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"code": "NoRoute"}))))
    r = client.post("/api/corridor/analyze", json=body(BUSY))
    assert r.status_code == 404 and "No route" in r.json()["detail"]
    providers.route_cache.clear()
    monkeypatch.setattr(providers, "client", lambda: httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"code": "Ok", "routes": []}))))
    assert client.post("/api/corridor/analyze", json=body(BUSY)).status_code == 404


def test_unknown_traffic_provider_is_a_clear_error(client, monkeypatch):
    monkeypatch.setenv("CORRIDOR_TRAFFIC_PROVIDER", "live-vendor")
    r = client.post("/api/corridor/analyze", json=body(BUSY))
    assert r.status_code == 503 and "not available" in r.json()["detail"]
