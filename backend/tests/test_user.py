"""Public rider API: access boundaries, providers (mocked HTTP), traffic-aware routing, diversion, validation, rate limits."""
import json

import httpx
import numpy as np
import pytest
from fastapi.testclient import TestClient

from app import config as C, engine, providers, user_api, user_traffic as UT
from app.api import app

# Simulated incident at 13:30 (step 6): severe on R0360 (17.426, 78.521) with heavy neighbours. Free-flow area: the south of the grid.
HEAVY_ROUTE = [[78.4983 + (78.5591 - 78.4983) * t / 40, 17.4399 + (17.4058 - 17.4399) * t / 40] for t in range(41)]   # [lon, lat] through the incident area
CLEAR_ROUTE = [[78.44 + 0.1 * t / 40, 17.32] for t in range(41)]                                                     # along the calm south edge
ORIGIN, DEST = {"lat": 17.4399, "lon": 78.4983}, {"lat": 17.4058, "lon": 78.5591}


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    for c in (providers.geocode_cache, providers.route_cache, providers.places_cache):
        c.clear()
    user_api.limiter.hits.clear()
    monkeypatch.setattr(providers, "MIN_GEOCODE_INTERVAL", 0)
    return None


@pytest.fixture()
def client():
    return TestClient(app)          # no cookie: a public rider


def mock_providers(monkeypatch, handler):
    calls = []

    def wrapped(request: httpx.Request):
        calls.append(request)
        return handler(request)
    monkeypatch.setattr(providers, "client", lambda: httpx.Client(transport=httpx.MockTransport(wrapped)))
    return calls


def osrm_body(*routes):
    return httpx.Response(200, json={"code": "Ok", "routes": [{"distance": d * 1000, "duration": m * 60, "geometry": {"type": "LineString", "coordinates": g}}
                                                               for d, m, g in routes]})


# ------------------------------------------------------------------ boundaries
def test_public_rider_endpoints_need_no_session_but_operator_endpoints_still_do(client):
    for path in ("/api/user/config", "/api/user/traffic", "/api/user/alerts"):
        assert client.get(path).status_code == 200, path
    for path in ("/api/meta", "/api/network", "/api/traffic/current", "/api/incidents", "/api/evaluation/forecast", "/api/forecast-map", "/api/candidates"):
        assert client.get(path).status_code == 401, path
    assert client.post("/api/simulation/run", json={"candidate_id": "PLAN0365"}).status_code == 401


def test_rider_responses_hold_no_operator_internals_or_secrets(client, monkeypatch):
    monkeypatch.setattr(C, "OPENROUTESERVICE_API_KEY", "sk-super-secret-123")
    texts = [client.get(p).text for p in ("/api/user/config", "/api/user/traffic", "/api/user/alerts")]
    blob = " ".join(texts).lower()
    assert "sk-super-secret-123" not in blob and "api_key" not in blob and "password" not in blob
    for internal in ("anomaly", "model_version", "residual", "threshold", "structural_bottleneck", "peak_capacity", "incident_type", "recommendation"):
        assert internal not in blob, internal


def test_traffic_omits_the_normal_grid_and_uses_rider_levels(client):
    d = client.get("/api/user/traffic", params={"step": 6}).json()
    assert d["mode"] == "simulation" and d["time"].endswith("13:30:00")
    assert 0 < len(d["zones"]) < 40                                                     # only the affected segments, never all 436
    assert {z["traffic_level"] for z in d["zones"]} <= {"moderate", "heavy", "severe"}
    assert set(d["zones"][0]) == {"segment_id", "traffic_level", "status", "speed_kmh", "congestion", "updated_at", "path"}


def test_traffic_changes_with_the_replay_step_and_step_is_bounded(client):
    early, late = (client.get("/api/user/traffic", params={"step": s}).json() for s in (0, 6))
    assert early["time"].endswith("13:00:00") and late["time"].endswith("13:30:00")
    assert early["counts"] != late["counts"]                                             # the dataset replay moves: the incident forms
    assert client.get("/api/user/traffic", params={"step": 24}).status_code == 422
    assert client.get("/api/user/traffic", params={"step": -1}).status_code == 422
    assert client.get("/api/user/traffic", params={"t": "2026-01-01 00:00:00"}).json()["time"].endswith("13:30:00")   # riders cannot pick a timestamp


def test_alerts_use_rider_language_and_never_claim_an_accident(client):
    a = client.get("/api/user/alerts", params={"step": 6}).json()["alerts"]
    assert a and a[0]["title"] == "Traffic disruption detected"
    assert "accident" not in json.dumps(a).lower() and a[0]["confidence"] in ("low", "medium", "high")


# ------------------------------------------------------------------ geocoding
NOMINATIM = [{"osm_type": "node", "osm_id": 7, "lat": "17.4025", "lon": "78.5612", "name": "Uppal", "type": "suburb",
              "display_name": "Uppal, Medchal-Malkajgiri, Telangana, India"}, {"lat": "bad"}]


def test_geocode_maps_results_biases_to_service_area_and_caches(client, monkeypatch):
    calls = mock_providers(monkeypatch, lambda r: httpx.Response(200, json=NOMINATIM))
    d = client.get("/api/user/geocode", params={"q": "  Uppal "}).json()
    assert d["results"] == [{"id": "n7", "label": "Uppal", "sublabel": "Medchal-Malkajgiri, Telangana, India", "lat": 17.4025, "lon": 78.5612, "kind": "suburb"}]
    q = dict(calls[0].url.params)
    assert q["bounded"] == "1" and q["countrycodes"] == "in" and "78.1" in q["viewbox"]
    assert "FlowSenseAI" in calls[0].headers["user-agent"]                                # Nominatim usage policy
    client.get("/api/user/geocode", params={"q": "uppal"})
    assert len(calls) == 1                                                               # second identical search served from cache


@pytest.mark.parametrize("status,msg", [(429, "busy"), (503, "temporarily unavailable")])
def test_geocode_provider_failures_become_friendly_messages(client, monkeypatch, status, msg):
    mock_providers(monkeypatch, lambda r: httpx.Response(status, text="boom <stack trace>"))
    r = client.get("/api/user/geocode", params={"q": "uppal"})
    assert r.status_code == 503 and msg in r.json()["detail"] and "stack" not in r.text


def test_geocode_input_validation_and_timeout(client, monkeypatch):
    assert client.get("/api/user/geocode", params={"q": "a"}).status_code == 422
    assert client.get("/api/user/geocode", params={"q": "x" * 101}).status_code == 422
    assert client.get("/api/user/geocode").status_code == 422

    def slow(request):
        raise httpx.ReadTimeout("slow", request=request)
    mock_providers(monkeypatch, slow)
    r = client.get("/api/user/geocode", params={"q": "uppal"})
    assert r.status_code == 504 and "taking too long" in r.json()["detail"]


# ------------------------------------------------------------------ routing + FlowSense traffic
def test_route_through_the_simulated_incident_gets_delay_a_severe_level_and_an_alert(client, monkeypatch):
    mock_providers(monkeypatch, lambda r: osrm_body((8.5, 11, HEAVY_ROUTE)))
    d = client.post("/api/user/route-options", json={"origin": ORIGIN, "destination": DEST, "step": 6}).json()
    r = d["routes"][0]
    assert d["mode"] == "simulation" and "simulated" in d["traffic_note"].lower()
    assert r["traffic_level"] == "severe" and r["delay_min"] >= 3 and r["duration_min"] == r["free_flow_min"] + r["delay_min"]
    assert {s["level"] for s in r["stretches"]} >= {"free_flow", "severe"} and r["simulation_coverage_pct"] > 80
    assert any(a["title"] == "Severe congestion ahead" and a["route_id"] == r["id"] for a in d["alerts"])
    assert d["diversion"] is None                                                       # nothing to divert to: never invented


def test_same_route_before_the_incident_forms_is_free_flow(client, monkeypatch):
    mock_providers(monkeypatch, lambda r: osrm_body((8.5, 11, HEAVY_ROUTE)))
    r = client.post("/api/user/route-options", json={"origin": ORIGIN, "destination": DEST, "step": 0}).json()["routes"][0]
    assert r["delay_min"] < 3 and r["traffic_level"] != "severe"                          # 13:00: the incident has not started (dataset replay)


def test_diversion_recommended_only_with_an_estimated_saving(client, monkeypatch):
    mock_providers(monkeypatch, lambda r: osrm_body((8.5, 11, HEAVY_ROUTE), (12.0, 12, CLEAR_ROUTE)))
    d = client.post("/api/user/route-options", json={"origin": ORIGIN, "destination": DEST, "step": 6}).json()
    best, dv = d["routes"][0], d["diversion"]
    assert best["recommended"] and best["ranking"] == 1 and best["label"] == "Recommended route" and not best["default"]
    assert [r["ranking"] for r in d["routes"]] == [1, 2]
    assert dv["recommended"] and dv["alternative_route_id"] == best["id"] and dv["estimated_saving_min"] >= 2
    assert dv["current_eta_min"] > dv["alternative_eta_min"] and dv["confidence"] in ("Low", "Medium")
    assert d["routes"][1]["default"] and d["routes"][1]["traffic_level"] == "severe"


def test_a_small_saving_does_not_trigger_a_diversion(client, monkeypatch):
    mock_providers(monkeypatch, lambda r: osrm_body((8.5, 11, HEAVY_ROUTE), (12.0, 16, CLEAR_ROUTE)))       # alternative ~as slow as the delayed route
    d = client.post("/api/user/route-options", json={"origin": ORIGIN, "destination": DEST, "step": 6}).json()
    assert d["diversion"] is None


def test_no_diversion_when_the_default_route_is_already_best(client, monkeypatch):
    mock_providers(monkeypatch, lambda r: osrm_body((12.0, 15, CLEAR_ROUTE), (8.5, 30, HEAVY_ROUTE)))
    d = client.post("/api/user/route-options", json={"origin": ORIGIN, "destination": DEST, "step": 6}).json()
    assert d["routes"][0]["default"] and d["diversion"] is None


def test_route_provider_errors_and_invalid_input(client, monkeypatch):
    mock_providers(monkeypatch, lambda r: httpx.Response(200, json={"code": "NoRoute", "routes": []}))
    r = client.post("/api/user/route-options", json={"origin": ORIGIN, "destination": DEST})
    assert r.status_code == 404 and r.json()["detail"] == "No route could be found between these places."
    for body, code in [({"origin": {"lat": 40.7, "lon": -74}, "destination": DEST}, 422),            # outside the service area
                       ({"origin": ORIGIN, "destination": ORIGIN}, 422),                              # same place
                       ({"origin": {"lat": 200, "lon": 0}, "destination": DEST}, 422),
                       ({"origin": ORIGIN}, 422), ({}, 422), ({"origin": ORIGIN, "destination": DEST, "step": 99}, 422)]:
        assert client.post("/api/user/route-options", json=body).status_code == code, body
    mock_providers(monkeypatch, lambda r: httpx.Response(502, text="Traceback ..."))
    r = client.post("/api/user/route-options", json={"origin": ORIGIN, "destination": DEST})
    assert r.status_code == 503 and "Traceback" not in r.text


def test_openrouteservice_adapter_sends_documented_alternative_route_parameters(client, monkeypatch):
    monkeypatch.setattr(C, "ROUTING_PROVIDER", "openrouteservice")
    monkeypatch.setattr(C, "OPENROUTESERVICE_API_KEY", "test-key")

    def ors(request):
        feats = [{"geometry": {"coordinates": g}, "properties": {"summary": {"distance": d * 1000, "duration": m * 60}}} for d, m, g in [(8.5, 11, HEAVY_ROUTE), (12, 15, CLEAR_ROUTE)]]
        return httpx.Response(200, json={"features": feats})
    calls = mock_providers(monkeypatch, ors)
    d = client.post("/api/user/route-options", json={"origin": ORIGIN, "destination": DEST, "step": 6}).json()
    req = calls[0]
    body = json.loads(req.content)
    assert req.method == "POST" and req.url.path == "/v2/directions/driving-car/geojson" and req.headers["authorization"] == "test-key"
    assert body["alternative_routes"] == {"target_count": 3, "weight_factor": 1.6, "share_factor": 0.6}
    assert body["coordinates"][0] == [ORIGIN["lon"], ORIGIN["lat"]] and d["routing_provider"] == "openrouteservice" and len(d["routes"]) == 2
    assert "test-key" not in json.dumps(d)                                               # the key never reaches the rider


def test_openrouteservice_key_problems_do_not_leak_details(client, monkeypatch):
    monkeypatch.setattr(C, "ROUTING_PROVIDER", "openrouteservice")
    mock_providers(monkeypatch, lambda r: httpx.Response(403, json={"error": "Access to this API has been disallowed"}))
    r = client.post("/api/user/route-options", json={"origin": ORIGIN, "destination": DEST})
    assert r.status_code == 503 and "not configured" in r.json()["detail"] and "disallowed" not in r.text


# ------------------------------------------------------------------ nearby places
OVERPASS = {"elements": [
    {"type": "node", "id": 1, "lat": 17.4310, "lon": 78.5010, "tags": {"amenity": "hospital", "name": "City Hospital", "addr:street": "MG Road", "addr:housenumber": "5",
                                                                        "opening_hours": "24/7", "phone": "+91 40 1234 5678"}},
    {"type": "way", "id": 2, "center": {"lat": 17.4330, "lon": 78.5020}, "tags": {"amenity": "hospital"}},                        # unnamed: name stays None
    {"type": "node", "id": 3, "lat": 17.4350, "lon": 78.5030, "tags": {"amenity": "fuel", "name": "Fuel Point", "website": "https://example.org"}},
    {"type": "node", "id": 4, "lat": 17.4340, "lon": 78.5040, "tags": {"amenity": "bank"}},                                          # not requested: ignored
    {"type": "node", "id": 1, "lat": 17.4310, "lon": 78.5010, "tags": {"amenity": "hospital"}}]}                                     # duplicate id


def test_nearby_builds_the_expected_query_and_normalises_without_inventing_fields(client, monkeypatch):
    calls = mock_providers(monkeypatch, lambda r: httpx.Response(200, json=OVERPASS))
    d = client.post("/api/user/nearby", json={"lat": 17.4338, "lon": 78.5020, "categories": ["hospital", "fuel"], "radius_m": 1500}).json()
    q = calls[0].content.decode()
    assert "around%3A1500" in q and "amenity%22%3D%22hospital" in q and "amenity%22%3D%22fuel" in q and "parking" not in q
    ps = d["places"]
    assert [p["category"] for p in ps].count("hospital") == 2 and len(ps) == 3 and ps == sorted(ps, key=lambda p: p["distance_m"])
    named = next(p for p in ps if p["name"] == "City Hospital")
    assert named["address"] == "5 MG Road" and named["opening_hours"] == "24/7" and named["phone"].startswith("+91") and named["website"] is None
    assert next(p for p in ps if p["id"] == "w2")["name"] is None and next(p for p in ps if p["id"] == "w2")["address"] is None


def test_nearby_empty_result_falls_back_to_next_mirror_and_reports_failure_kindly(client, monkeypatch):
    calls = mock_providers(monkeypatch, lambda r: httpx.Response(200, json={"elements": []}) if "kumi" in str(r.url) else httpx.Response(504, text="Gateway Timeout"))
    d = client.post("/api/user/nearby", json={"lat": 17.43, "lon": 78.5, "categories": ["metro"]}).json()
    assert d["places"] == [] and len(calls) == 2                                          # first mirror failed, second answered; empty is not an error
    mock_providers(monkeypatch, lambda r: httpx.Response(429, text="rate limited"))
    r = client.post("/api/user/nearby", json={"lat": 17.44, "lon": 78.51, "categories": ["parking"]})
    assert r.status_code == 503 and "busy" in r.json()["detail"]


def test_nearby_validation(client):
    ok = {"lat": 17.43, "lon": 78.5, "categories": ["fuel"]}
    for patch, code in [({"categories": ["casino"]}, 422), ({"categories": []}, 422), ({"radius_m": 999999}, 422), ({"radius_m": 5}, 422),
                        ({"lat": 51.5, "lon": -0.12}, 422), ({"lat": "x"}, 422)]:
        assert client.post("/api/user/nearby", json={**ok, **patch}).status_code == code, patch


# ------------------------------------------------------------------ rate limiting + units
def test_rate_limit_returns_a_friendly_429(client, monkeypatch):
    monkeypatch.setitem(user_api.LIMITS, "geocode", 2)
    mock_providers(monkeypatch, lambda r: httpx.Response(200, json=NOMINATIM))
    codes = [client.get("/api/user/geocode", params={"q": f"place {i}"}).status_code for i in range(3)]
    assert codes == [200, 200, 429]
    assert "a bit fast" in client.get("/api/user/geocode", params={"q": "another"}).json()["detail"]
    assert client.get("/api/user/traffic").status_code == 200                              # limits are per endpoint


def test_geometry_helpers():
    A, B = np.array([[0.0, 0.0]]), np.array([[100.0, 0.0]])
    d = UT.dist_to_segments(np.array([[50.0, 0.0], [50.0, 30.0], [-40.0, 0.0]]), A, B)[:, 0]
    assert d.tolist() == [0.0, 30.0, 40.0]
    pts, pos, ds, cum = UT.resample(np.array([[0.0, 0.0], [1000.0, 0.0]]), 120)
    assert abs(ds.sum() - 1000) < 1e-6 and len(pts) == len(pos) == len(ds) and pos[-1] == 1000


def test_route_level_needs_a_real_stretch_not_a_single_sample():
    ds = np.full(10, 120.0)
    lv = lambda *names: np.array(list(names) + ["free_flow"] * (10 - len(names)))
    assert UT._route_level(lv("severe"), ds) == "free_flow" if 120 < UT.LEVEL_MIN_M else True
    assert UT._route_level(lv("severe", "severe", "severe"), ds) == "severe"
    assert UT._route_level(lv("heavy", "heavy", "heavy"), ds) == "heavy"
    assert UT._route_level(lv("moderate", "moderate", "moderate"), ds) == "moderate"
    assert UT._route_level(lv("severe", "free_flow", "heavy", "heavy"), ds) == "heavy"


def test_ranking_prefers_lower_expected_time_and_penalises_worsening():
    snap = engine.get("2026-01-16 13:30:00")
    d = UT.build_options([{"distance_km": 8.5, "duration_min": 11, "coords": HEAVY_ROUTE}, {"distance_km": 12, "duration_min": 15, "coords": CLEAR_ROUTE}], snap)
    ranks = {r["id"]: r["ranking"] for r in d["routes"]}
    assert ranks == {"r2": 1, "r1": 2}
    assert all("_score" not in r for r in d["routes"])
