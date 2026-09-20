"""AI infrastructure recommendation: intervention comparison, simulated land layer, provider seam, impact model, simulation endpoint, honesty labels."""
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app import auth, corridor as K, infrastructure as I, providers
from app.api import app

LINE = lambda a, b, n=41: [[a[1] + (b[1] - a[1]) * t / (n - 1), a[0] + (b[0] - a[0]) * t / (n - 1)] for t in range(n)]      # [lon, lat]
BUSY = LINE((17.4399, 78.4983), (17.4058, 78.5591))
BUSY2 = LINE((17.4375, 78.4483), (17.4795, 78.5590))          # Ameerpet -> Uppal area
CALM = LINE((17.30, 78.44), (17.30, 78.54))


def osrm(coords, extra_route=False):
    steps = [{"name": "Test Road", "distance": 20000, "intersections": [{"location": coords[i], "bearings": [0, 90, 180]} for i in range(0, len(coords), 4)]}]
    route = {"distance": 9000, "duration": 900, "geometry": {"type": "LineString", "coordinates": coords}, "legs": [{"steps": steps}]}
    return httpx.Response(200, json={"code": "Ok", "routes": [route, route] if extra_route else [route]})


@pytest.fixture(autouse=True)
def fresh():
    providers.route_cache.clear()
    K._CACHE.clear()
    I._CACHE.clear()


def route(monkeypatch, coords, alt=False):
    monkeypatch.setattr(providers, "client", lambda: httpx.Client(transport=httpx.MockTransport(lambda r: osrm(coords, alt))))


@pytest.fixture()
def client():
    c = TestClient(app)
    c.cookies.set(auth.COOKIE, auth.issue_token(auth.LOGIN_ID))
    return c


def body(coords):
    a, b = coords[0], coords[-1]
    return {"origin": {"lat": a[1], "lng": a[0]}, "destination": {"lat": b[1], "lng": b[0]}}


def analyze(monkeypatch, coords, **kw):
    route(monkeypatch, coords, **kw)
    a, b = coords[0], coords[-1]
    return I.analyze(K.analyze((a[1], a[0]), (b[1], b[0])))


# ---- three arbitrary corridors ---------------------------------------------------------------------------------------------
@pytest.mark.parametrize("coords", [BUSY, BUSY2])
def test_corridors_get_a_full_labelled_comparison_and_recommendation(monkeypatch, coords):
    r = analyze(monkeypatch, coords)
    assert r["mode"] == "SIMULATION" and r["labels"]["land"] == "SIMULATED" and r["labels"]["banner"] == "Simulation / Demo Data"
    assert r["disclaimer"].startswith("This AI-generated infrastructure analysis is intended for planning and simulation purposes only")
    assert {i["id"] for i in r["interventions"]} == {"flyover", "road_extension", "road_widening", "junction_redesign", "signal_optimization", "traffic_diversion"}
    live = [i for i in r["interventions"] if i["applicable"]]
    assert live and all(0 <= v <= 100 for i in live for v in i["scores"].values())
    assert [i["scores"]["overall"] for i in live] == sorted((i["scores"]["overall"] for i in live), reverse=True)          # ranked, not "always flyover"
    rec = r["recommendation"]
    assert rec["action"] in {"none", *[i["id"] for i in r["interventions"]]} and rec["disclaimer"] and rec["data_status"] == "SIMULATED"
    assert r["land_evaluation"]["data_status"] == "SIMULATED" and "not government land records" in r["land_evaluation"]["label"]
    assert r["confidence"]["simulated"] == ["land", "infrastructure"] and "not the probability" in r["confidence"]["meaning"]
    json.dumps(r, allow_nan=False)                                                                                         # no NaN / Infinity anywhere


def test_recommended_candidate_lies_on_the_corridor_with_length_and_cost(monkeypatch):
    r = analyze(monkeypatch, BUSY2)
    c = r["recommendation"].get("candidate")
    assert c and c["length_km"] > 0 and c["estimated_cost_cr"] > 0 and c["start"] != c["end"]
    assert r["recommendation"]["why"] and r["recommendation"]["simulated_impact"]["congestion_change_pct"] <= 0


def test_a_calm_corridor_recommends_nothing_and_says_so(monkeypatch):
    route(monkeypatch, CALM)
    quiet = {**K.cfg(), "bottleneck_levels": {"MODERATE": 90, "HIGH": 95, "CRITICAL": 99}}
    monkeypatch.setattr(K, "cfg", lambda: quiet)
    r = I.analyze(K.analyze((17.30, 78.44), (17.30, 78.54)))
    assert r["recommendation"]["action"] == "none" and r["interventions"] == [] and r["land_evaluation"] is None
    assert "No infrastructure intervention is indicated" in r["recommendation"]["summary"] or "no infrastructure intervention" in r["recommendation"]["summary"]


# ---- simulated land layer + provider seam -----------------------------------------------------------------------------------
def test_simulated_land_data_is_deterministic_per_zone_and_differs_between_zones(monkeypatch):
    a1, a2 = analyze(monkeypatch, BUSY), None
    I._CACHE.clear()
    a2 = I.analyze(K.analyze((BUSY[0][1], BUSY[0][0]), (BUSY[-1][1], BUSY[-1][0])))
    assert a1["land_evaluation"] == a2["land_evaluation"]
    providers.route_cache.clear(); K._CACHE.clear()
    b = analyze(monkeypatch, BUSY2)
    assert b["land_evaluation"] != a1["land_evaluation"]


def test_the_ui_contract_does_not_depend_on_the_provider(monkeypatch):
    class Gis:
        name, simulated = "gis (test)", False
        def land(self, z):
            return {"data_status": "PROVIDER", "label": "test GIS", "land_availability_pct": 5, "government_land_pct": 1, "private_property_impact_pct": 90,
                    "structures_affected": 60, "row_availability_pct": 10, "utility_conflict": "High", "environmental_constraint": "Medium", "construction_difficulty": 0.6}
        def confidence(self): return {"land": 90, "infrastructure": 88}
    route(monkeypatch, BUSY2)
    corr = K.analyze((BUSY2[0][1], BUSY2[0][0]), (BUSY2[-1][1], BUSY2[-1][0]))
    sim = I.analyze(corr)
    real = I.analyze(corr, Gis())
    assert set(real) == set(sim) and real["provider"]["infrastructure"] == "gis (test)" and real["confidence"]["simulated"] == []
    f = lambda r: next(i for i in r["interventions"] if i["id"] == "flyover")["scores"]
    assert f(real)["land_feasibility"] < f(sim)["land_feasibility"] and f(real)["overall"] < f(sim)["overall"]      # land data really drives the ranking
    assert real["confidence"]["components"]["land"] == 90


def test_unknown_provider_is_a_clear_error(monkeypatch):
    monkeypatch.setenv("INFRASTRUCTURE_PROVIDER", "govt-land-registry")
    with pytest.raises(K.CorridorError, match="not available"):
        I.get_provider()


# ---- impact model ----------------------------------------------------------------------------------------------------------
Z = {"key": "z", "length_km": 2.0, "utilization": 0.9, "speed": 15.0, "ff": 40.0, "volume": 2400, "capacity": 2700, "min_lanes": 3, "signals": 2, "signal_delay_s": 30,
     "junctions": 4, "incidents": 1, "growth_pct": 5, "congestion_index": 70, "delay_min": 3, "segments": 6}
IV = I.cfg()["interventions"]


def test_more_capacity_means_faster_and_no_capacity_means_no_change():
    none = I.impact({**IV["signal_optimization"], "capacity_gain": 0, "signal_removal": 0}, Z)
    assert none["before"] == none["after"] and none["speed_change_pct"] == 0
    small, big = I.impact(IV["signal_optimization"], Z), I.impact(IV["flyover"], Z)
    assert big["after"]["speed_kmh"] > small["after"]["speed_kmh"] > big["before"]["speed_kmh"]
    assert big["after"]["delay_min"] < small["after"]["delay_min"] < big["before"]["delay_min"] and big["emissions_change_pct"] < 0
    assert big["after"]["speed_kmh"] <= Z["ff"]                                       # never faster than free flow


def test_widening_capacity_gain_comes_from_the_real_lane_count():
    two, four = I.impact(IV["road_widening"], {**Z, "min_lanes": 2}), I.impact(IV["road_widening"], {**Z, "min_lanes": 4})
    assert two["capacity_change_pct"] == 50 and four["capacity_change_pct"] == 25         # +1 lane on 4 lanes is +25%, capped for 2 lanes


def test_interventions_that_do_not_apply_are_marked_with_a_reason():
    land = I.SimulationInfrastructureProvider().land(Z)
    ivs = {i["id"]: i for i in I.evaluate_interventions({**Z, "signals": 0, "junctions": 0}, land, 0, 2.0)}
    assert not ivs["signal_optimization"]["applicable"] and "signalised" in ivs["signal_optimization"]["reason_not_applicable"]
    assert not ivs["junction_redesign"]["applicable"] and not ivs["traffic_diversion"]["applicable"] and ivs["flyover"]["applicable"]
    assert I.evaluate_interventions(Z, land, 1, 2.0)[0]["applicable"] and next(i for i in I.evaluate_interventions(Z, land, 1, 2.0) if i["id"] == "traffic_diversion")["applicable"]


def test_cost_efficiency_is_relative_and_the_cheapest_effective_option_scores_highest():
    land = I.SimulationInfrastructureProvider().land(Z)
    ivs = [i for i in I.evaluate_interventions(Z, land, 1, 2.0) if i["applicable"]]
    eff = {i["id"]: i["scores"]["cost_efficiency"] for i in ivs}
    assert max(eff.values()) == 100 and eff["flyover"] < eff["signal_optimization"]


def test_a_mild_zone_carries_a_caution_instead_of_overclaiming(monkeypatch):
    r = analyze(monkeypatch, BUSY2)
    if r["recommendation"]["action"] != "none" and r["recommendation"]["simulated_impact"]:
        b = next(i for i in r["interventions"] if i["id"] == r["recommendation"]["action"])["simulated_impact"]["before"]["congestion_pct"]
        assert (r["recommendation"]["caution"] is not None) == (b < 20)


# ---- API -------------------------------------------------------------------------------------------------------------------
def test_api_requires_the_operator_session():
    c = TestClient(app)
    assert c.post("/api/infrastructure/analyze", json={}).status_code == 401 and c.post("/api/infrastructure/simulate", json={}).status_code == 401


def test_analyze_then_simulate_flow(client, monkeypatch):
    route(monkeypatch, BUSY2)
    a = client.post("/api/infrastructure/analyze", json=body(BUSY2))
    assert a.status_code == 200
    j = a.json()
    assert j["corridor"]["segments"] and j["corridor_id"] == j["corridor"]["id"]
    assert client.post("/api/infrastructure/simulate", json={"corridor_id": j["corridor_id"], "intervention": "flyover"}).status_code == 200
    s = client.post("/api/infrastructure/simulate", json={"corridor_id": j["corridor_id"], "intervention": "flyover"}).json()
    assert s["label"] == "SIMULATED IMPACT" and s["mode"] == "SIMULATION" and s["current_vs_after"]["after"]["speed_kmh"] >= s["current_vs_after"]["before"]["speed_kmh"]
    assert s["planning_horizon"]["demand_growth_pct"] == I.cfg()["simulation"]["horizon_demand_growth_pct"] and "not predictions of actual future outcomes" in s["disclaimer"]
    assert client.post("/api/infrastructure/simulate", json={"corridor_id": j["corridor_id"], "intervention": "hyperloop"}).status_code == 422
    assert client.post("/api/infrastructure/simulate", json={"corridor_id": "deadbeef0000", "intervention": "flyover"}).status_code == 404
    assert client.post("/api/infrastructure/simulate", json={"corridor_id": j["corridor_id"], "intervention": "traffic_diversion"}).status_code == 422       # no alternative route


def test_api_errors_are_plain_language(client, monkeypatch):
    ok = body(BUSY2)
    assert client.post("/api/infrastructure/analyze", json={**ok, "origin": {"lat": 999, "lng": 1}}).status_code == 422
    assert client.post("/api/infrastructure/analyze", json={**ok, "origin": {"lat": 28.6, "lng": 77.2}}).json()["detail"].startswith("The origin is outside")
    monkeypatch.setattr(providers, "client", lambda: httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"code": "NoRoute"}))))
    r = client.post("/api/infrastructure/analyze", json=ok)
    assert r.status_code == 404 and "Traceback" not in r.text
