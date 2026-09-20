"""Rider "why is traffic heavy?": cause mapping, honesty about uncertainty, real-data smoke test, logout revocation, access boundary."""
import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app import auth, user_api, user_explain as X
from app.api import app

EV = lambda metric, value=None, baseline=None, diff=None, note=None: {"metric": metric, "value": value, "baseline": baseline, "diff_pct": diff, "unit": "", "note": note}


class Fake:
    """Just enough of engine.Snapshot for user_explain.explain, with every input under the test's control."""
    def __init__(self, cause, detected=True, conf="HIGH", onset=0.0, level="CRITICAL", rain=0.0, roadwork=False, itype=None, neighbor=0.0, queue=0.0, speed_diff=-58.0):
        self.t, self.idx, self.state = pd.Timestamp("2026-01-16 13:30"), {"S": 0}, np.array([level])
        self.net = pd.DataFrame({"free_flow_speed_kmh": [50.0]}, index=["S"])
        self.forecast = None
        self._dg = {"cause": cause, "detected": detected, "confidence": 0.87, "confidence_label": conf, "onset_step": onset, "reasons": [],
                    "incident_type": itype, "context": {"rain_intensity": rain, "event_present": False, "roadwork_active": roadwork}}
        self._ev = [EV("speed", 20, 48, speed_diff), EV("queue_change_15min", queue), EV("congestion_index", 0.7, 0.2),
                    EV("roadwork_active", float(roadwork), note="lane closure" if roadwork else None), EV("rain_intensity", rain),
                    EV("largest_neighbor_drop", neighbor)]

    def row(self, seg): return {"state": str(self.state[0]), "anomaly_score": 0.576}
    def diagnosis(self, seg): return self._dg
    def evidence(self, seg): return self._ev


def test_congestion_without_evidence_says_cause_unclear():
    r = X.explain(Fake("UNKNOWN", detected=True, conf="LOW"), "S")
    assert r["cause"]["label"] == "Cause unclear" and r["cause"]["certainty"] == "unknown" and r["cause"]["confidence"] is None
    assert "cannot confidently identify" in r["summary"]


def test_sudden_drop_and_queue_growth_is_an_inferred_disruption_never_a_confirmed_accident():
    r = X.explain(Fake("TRANSIENT_INCIDENT", onset=0.3, queue=40, itype={"type": "accident_like", "confidence": "MEDIUM"}), "S")
    assert r["status"] == "disruption_detected" and r["cause"]["certainty"] == "likely"
    assert r["cause"]["type"] == "Accident-like incident" and r["cause"]["type_note"]
    signals = {e["signal"] for e in r["evidence"]}
    assert {"speed_drop", "sudden_onset", "queue_growth"} <= signals
    assert "confirmed" not in r["summary"].lower().replace("not a confirmed", "")
    assert all(e["segment_id"] == "S" and e["timestamp"] for e in r["evidence"])


def test_low_confidence_type_stays_undetermined():
    r = X.explain(Fake("TRANSIENT_INCIDENT", conf="LOW", onset=0.3, itype={"type": "accident_like", "confidence": "LOW"}), "S")
    assert r["cause"]["type"] == "Undetermined" and r["cause"]["certainty"] == "possible" and "accident" not in r["summary"].lower()


def test_active_roadwork_is_explained_as_reported():
    r = X.explain(Fake("ROADWORK_EFFECT", roadwork=True), "S")
    assert r["cause"]["label"] == "Roadwork" and r["cause"]["certainty"] == "reported"
    assert any("roadwork" in e["message"].lower() and "No active" not in e["message"] for e in r["evidence"])


def test_rain_slowdown_is_shown_as_possible_weather_contribution():
    r = X.explain(Fake("WEATHER_EVENT_EFFECT", detected=False, rain=0.6), "S")
    assert "Weather" in r["cause"]["label"] and r["cause"]["certainty"] == "possible"
    assert any(e["signal"] == "weather" for e in r["evidence"])


def test_recurring_bottleneck_is_explained():
    r = X.explain(Fake("RECURRING_BOTTLENECK", detected=False), "S")
    assert r["cause"]["label"] == "Recurring congestion"


def test_free_flow_gets_no_explanation():
    assert X.explain(Fake("NORMAL_VARIATION", detected=False, level="NORMAL"), "S")["status"] == "normal"


def test_real_incident_step_explained_from_backend_signals_without_internals():
    c = TestClient(app)
    seg = user_api.snapshot(6).events()[0]["segment_id"]
    r = c.post("/api/user/explain", json={"segment_id": seg, "step": 6})
    assert r.status_code == 200
    b = r.json()
    assert b["status"] == "disruption_detected" and b["cause"]["confidence"] > 0.5 and b["evidence"] and b["forecast"]["horizons"]
    blob = str(b).lower()
    assert "anomaly" not in blob and "technical" not in blob and "incidents_" not in blob   # no model internals, no label files
    assert c.post("/api/user/explain", json={"lat": 17.30, "lon": 78.20, "step": 6}).json()["status"] == "normal"   # nothing congested nearby
    assert c.post("/api/user/explain", json={"lat": 999, "lon": 0}).status_code == 422


def test_logout_revokes_the_token_even_if_the_cookie_is_kept():
    c = TestClient(app)
    token = auth.issue_token(auth.LOGIN_ID)
    assert auth.verify_token(token)
    c.cookies.set(auth.COOKIE, token)
    assert c.get("/api/incidents").status_code == 200
    c.post("/api/auth/logout")
    assert auth.verify_token(token) is None
    c.cookies.set(auth.COOKIE, token)                      # replaying the old cookie no longer works
    assert c.get("/api/incidents").status_code == 401


def test_rider_cannot_reach_operator_endpoints():
    c = TestClient(app)
    for p in ("/api/incidents", "/api/recommendations", "/api/evaluation", "/api/simulate"):
        assert c.get(p).status_code in (401, 405)
