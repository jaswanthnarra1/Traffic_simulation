"""API tests (FastAPI TestClient against the real artifacts)."""
import pytest
from fastapi.testclient import TestClient

from app.api import app

T = "2026-01-16T13:30:00"


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        assert c.post("/api/auth/login", json={"login_id": "tgpolice", "password": "tgpolice"}).status_code == 200
        yield c


def test_health_and_meta(client):
    h = client.get("/api/health").json()
    assert h["status"] == "ok" and h["models_available"] and h["database"]
    m = client.get("/api/meta").json()
    assert m["horizons_min"] == [15, 30, 45, 60]


def test_network_hides_organizer_bottleneck_label(client):
    g = client.get("/api/network").json()
    assert len(g["geojson"]["features"]) == 436 and "Synthetic" in g["disclaimer"]
    seg = client.get("/api/network/segments").json()[0]
    assert "structural_bottleneck" not in seg and "peak_capacity_factor" not in seg


def test_traffic_current(client):
    r = client.get("/api/traffic/current", params={"t": T}).json()
    assert r["mode"] == "LIVE_SIMULATION" and len(r["segments"]) == 436
    assert r["summary"]["anomalous_segments"] >= 1


def test_segment_detail_and_errors(client):
    r = client.get("/api/traffic/segment/R0360", params={"t": T}).json()
    assert r["diagnosis"]["detected"] and r["evidence"]
    assert client.get("/api/traffic/segment/R9999", params={"t": T}).status_code == 404
    assert client.get("/api/traffic/current", params={"t": "2030-01-01"}).status_code == 422
    assert client.get("/api/traffic/current", params={"t": "not-a-time"}).status_code == 422


def test_forecast_live_vs_backtest(client):
    live = client.get("/api/forecast/R0360", params={"t": T}).json()
    assert live["mode"] == "LIVE" and "actual_future" not in live
    assert [p["horizon_min"] for p in live["forecast"]["speed"]["forecast"]] == [0, 15, 30, 45, 60]
    bt = client.get("/api/forecast/R0360", params={"t": T, "mode": "backtest"}).json()
    assert bt["mode"] == "BACKTEST" and "actual_future" in bt


def test_detection_incidents_propagation(client):
    d = client.get("/api/detection", params={"t": T}).json()
    assert any(s["segment_id"] == "R0360" for s in d["segments"])
    inc = client.get("/api/incidents", params={"t": T}).json()
    assert inc["events"] and inc["events"][0]["segment_id"] == "R0360"
    pr = client.get("/api/propagation/R0360", params={"t": T, "horizon": 15}).json()
    assert pr["neighbors"] and all(n["estimated_time_to_impact_min"] <= 15 for n in pr["neighbors"])


def test_recommendations(client):
    r = client.get("/api/recommendations/R0360", params={"t": T}).json()
    a = r["advisory"]
    assert a["recommendation"] and a["reasons"] and a["evidence"] and a["limitations"]
    assert r["search"]["neighbor_candidates"] >= 1
    assert any(o["action"] == "Prepare diversion" and o["legal_turns_verified"] for o in r["operational_options"])


def test_simulation_run_and_validation(client):
    r = client.post("/api/simulation/run", json={"time": T, "incident_segment": "R0360", "capacity_reduction": 0.5,
                                                 "candidate_id": "PLAN0365"})
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["label"].startswith("SIMULATION ESTIMATE") and len(b["per_segment"]) == 436
    assert client.post("/api/simulation/run", json={"time": T, "capacity_reduction": 2}).status_code == 422
    assert client.post("/api/simulation/run", json={"time": T, "candidate_id": "DROP TABLE"}).status_code == 422
    assert client.post("/api/simulation/run", json={"time": T, "candidate_id": "PLAN9999"}).status_code == 404


def test_evaluation_and_scenarios(client):
    for k in ("forecast", "detection", "bottleneck", "propagation", "simulation", "robustness"):
        assert client.get(f"/api/evaluation/{k}").status_code == 200
    assert client.get("/api/evaluation/../../etc").status_code == 404
    sc = client.get("/api/scenarios").json()
    assert len(sc["scenarios"]) == 30 and "not independent validation" in sc["note"]
    one = client.get(f"/api/scenarios/{sc['scenarios'][0]['scenario_id']}").json()
    assert "diagnosis" in one


def test_forecast_map_matches_segment_forecast(client):
    r = client.get("/api/forecast-map", params={"t": T, "horizon": 30}).json()
    assert r["mode"] == "FORECAST" and r["horizon_min"] == 30 and len(r["segments"]) == 436
    assert {x["state"] for x in r["segments"]} <= {"NORMAL", "MODERATE", "HEAVY", "CRITICAL"}
    row = next(x for x in r["segments"] if x["segment_id"] == "R0360")
    live = client.get("/api/forecast/R0360", params={"t": T}).json()
    p30 = next(p for p in live["forecast"]["speed"]["forecast"] if p["horizon_min"] == 30)
    assert abs(row["speed_kmh"] - p30["p50"]) < 0.01          # same numbers as the per-segment forecast
    assert client.get("/api/forecast-map", params={"t": T, "horizon": 25}).status_code == 422
