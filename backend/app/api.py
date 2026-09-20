"""FlowSense AI REST API. Run: uvicorn app.api:app --port 8000 (from backend/)."""
import json
import math
import threading
from contextlib import asynccontextmanager
from typing import Any, Literal

import numpy as np
import pandas as pd
from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from . import config as C, data, engine, forecasting, graph, recommendations, simulation
from . import auth  # after config: config loads .env
from .user_api import router as user_router
from .corridor_api import router as corridor_router
from .detection import detector_meta, state_of

@asynccontextmanager
async def lifespan(_app):
    threading.Thread(target=_warm, daemon=True).start()
    yield


app = FastAPI(title="FlowSense AI", lifespan=lifespan, version=C.MODEL_VERSION,
              description="AI Traffic Intelligence & Intervention Simulator — advisory / simulation only")
PUBLIC_PATHS = {"/api/health", "/api/auth/login", "/api/auth/logout", "/api/auth/me"}
PUBLIC_PREFIXES = ("/api/user/",)   # the rider portal API: separate router, no operator data (see user_api.py)


@app.middleware("http")
async def require_session(request: Request, call_next):
    """One central gate: every /api/* route except health, auth and the rider API needs a valid operator session cookie."""
    path = request.url.path
    if request.method != "OPTIONS" and path.startswith("/api/") and path not in PUBLIC_PATHS and not path.startswith(PUBLIC_PREFIXES):
        if auth.verify_token(request.cookies.get(auth.COOKIE)) is None:
            return JSONResponse({"detail": "Not authenticated"}, status_code=401)
    return await call_next(request)


app.include_router(user_router)
app.include_router(corridor_router)

# credentials=True lets a separately hosted UI send the session cookie (explicit origins only, never "*")
app.add_middleware(CORSMiddleware, allow_origins=C.CORS_ALLOWED_ORIGINS, allow_credentials=True,
                   allow_methods=["GET", "POST"], allow_headers=["*"])


@app.exception_handler(RequestValidationError)
async def validation_error(_request: Request, exc: RequestValidationError):
    """FastAPI echoes submitted values in 422 errors by default — that would echo a password. Drop them."""
    return JSONResponse({"detail": [{k: v for k, v in e.items() if k not in ("input", "ctx", "url")} for e in exc.errors()]},
                        status_code=422)


def clean(o: Any) -> Any:
    """numpy / NaN -> JSON-safe python."""
    if isinstance(o, dict):
        return {str(k): clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [clean(v) for v in o]
    if isinstance(o, np.ndarray):
        return clean(o.tolist())
    if isinstance(o, (np.bool_,)):
        return bool(o)
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (float, np.floating)):
        return None if not math.isfinite(float(o)) else float(o)
    if isinstance(o, pd.Timestamp):
        return str(o)
    return o


def _snap(t):
    try:
        return engine.get(t)
    except ValueError as e:
        raise HTTPException(422, str(e))
    except FileNotFoundError as e:
        raise HTTPException(503, f"artifact missing: {e}")


def _seg(snap, segment_id: str):
    if segment_id not in snap.idx:
        raise HTTPException(404, f"unknown segment {segment_id}")
    return segment_id


TimeQ = Query(None, description="Simulation time (ISO). Floored to the 5-minute grid. Default: demo time.",
              max_length=32)


# ---------------------------------------------------------------- models
class Health(BaseModel):
    status: str
    models_available: bool
    database: bool


class Meta(BaseModel):
    product: str
    data_range: list[str]
    train_period: list[str]
    validation_period: list[str]
    demo_time: str
    model_version: str
    detector: dict
    state_thresholds: dict
    horizons_min: list[int]
    peak_hours: list[int]
    labels: dict


class TrafficSnapshot(BaseModel):
    time: str
    mode: Literal["LIVE_SIMULATION"] = "LIVE_SIMULATION"
    in_training_period: bool
    context: dict
    summary: dict
    segments: list[dict]


class SimulationRequest(BaseModel):
    time: str | None = Field(None, max_length=32)
    incident_segment: str | None = Field(None, pattern=r"^R\d{4}$")
    capacity_reduction: float = Field(0.0, ge=0.0, le=C.MAX_CAPACITY_REDUCTION)
    candidate_id: str | None = Field(None, pattern=r"^PLAN\d{4}$")


class SimulationResponse(BaseModel):
    time: str
    label: str
    baseline_description: str
    counterfactual_description: str
    kpis: dict
    segments_improved: int
    segments_worsened: int
    worst_side_effects: list
    largest_improvements: list
    convergence: dict
    focus: list | None = None
    per_segment: list


# ---------------------------------------------------------------- routes
class LoginRequest(BaseModel):
    login_id: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


class SessionUser(BaseModel):
    login_id: str
    display_name: str
    expires_at: int


@app.post("/api/auth/login", response_model=SessionUser)
def login(req: LoginRequest, response: Response):
    if not auth.check_credentials(req.login_id, req.password):
        raise HTTPException(401, "Invalid login ID or password.")  # same message whichever field is wrong
    token = auth.issue_token(req.login_id)
    response.set_cookie(auth.COOKIE, token, max_age=auth.TTL_S, httponly=True, samesite="lax",
                        secure=auth.COOKIE_SECURE, path="/")
    return auth.user(auth.verify_token(token))


@app.post("/api/auth/logout")
def logout(request: Request, response: Response):
    auth.revoke(request.cookies.get(auth.COOKIE))
    response.delete_cookie(auth.COOKIE, path="/")
    return {"authenticated": False}


@app.get("/api/auth/me", response_model=SessionUser)
def me(request: Request):
    payload = auth.verify_token(request.cookies.get(auth.COOKIE))
    if payload is None:
        raise HTTPException(401, "Not authenticated")
    return auth.user(payload)


@app.get("/api/health", response_model=Health)
def health():
    return Health(status="ok", models_available=forecasting.models_available(), database=C.DB_PATH.exists())


@app.get("/api/meta", response_model=Meta)
def meta():
    lo, hi = data.data_range()
    return Meta(product="FlowSense AI", data_range=[str(lo), str(hi)], train_period=[C.TRAIN_START, C.TRAIN_END],
                validation_period=[C.VAL_START, C.VAL_END], demo_time=C.DEMO_TIME, model_version=C.MODEL_VERSION,
                detector=detector_meta(), state_thresholds={k: v for k, v in C.STATE_THRESHOLDS},
                horizons_min=list(C.HORIZONS), peak_hours=list(C.PEAK_HOURS),
                labels={"LIVE_SIMULATION": "computed from data <= simulation time only",
                        "BACKTEST": "shows recorded future values for evaluation — never used as model input",
                        "VALIDATION": "metrics on held-out organizer data",
                        "SYNTHETIC_ROBUSTNESS": "metrics under self-injected corruption",
                        "SIMULATION_ESTIMATE": "output of our network model, no ground truth"})


@app.get("/api/network")
def network_geo():
    nodes = data.table("nodes")
    return clean({"geojson": graph.geojson(), "summary": graph.summary(),
                  "nodes": nodes.to_dict("records"),
                  "bounds": [[nodes.lat.min(), nodes.lon.min()], [nodes.lat.max(), nodes.lon.max()]],
                  "disclaimer": "Synthetic 12x10 simulation network placed on Hyderabad-area coordinates — not real roads."})


@app.get("/api/network/segments")
def segments():
    net = data.network().drop(columns=["structural_bottleneck", "peak_capacity_factor"])  # label + its proxy stay out of the UI
    return clean(net.to_dict("records"))


@app.get("/api/traffic/current", response_model=TrafficSnapshot)
def traffic_current(t: str | None = TimeQ):
    s = _snap(t)
    rows = s.rows.replace({np.nan: None})
    return clean({"time": str(s.t), "in_training_period": s.t <= pd.Timestamp(C.TRAIN_END), "context": s.context,
                  "summary": {"states": s.rows.state.value_counts().to_dict(), "anomalous_segments": int(s.rows.anomalous.sum()),
                              "mean_data_quality": float(np.mean(s.quality)), "network_mean_speed_ratio": float(np.nanmean(s.c["sr"][:, s.ti])),
                              "segments_without_data": int(np.isnan(s.c["sr"][:, s.ti]).sum())},
                  "segments": rows.to_dict("records")})


@app.get("/api/traffic/segment/{segment_id}")
def traffic_segment(segment_id: str, t: str | None = TimeQ):
    s = _snap(t)
    _seg(s, segment_id)
    static = data.network().set_index("segment_id").loc[segment_id].drop(["structural_bottleneck", "peak_capacity_factor"])
    return clean({"time": str(s.t), "segment": {"segment_id": segment_id, **static.to_dict()}, "state": s.row(segment_id),
                  "evidence": s.evidence(segment_id), "diagnosis": s.diagnosis(segment_id)})


@app.get("/api/forecast/{segment_id}")
def forecast(segment_id: str, t: str | None = TimeQ, mode: Literal["live", "backtest"] = "live"):
    s = _snap(t)
    _seg(s, segment_id)
    fc = s.forecast_for(segment_id)
    if fc is None:
        raise HTTPException(503, "forecast models unavailable — run scripts/train_models.py")
    out = {"time": str(s.t), "segment_id": segment_id, "mode": mode.upper(), "forecast": fc,
           "uncertainty": "empirical P10-P90 residual band from a held-out calibration window (not a calibrated probability)"}
    if mode == "backtest":  # explicitly requested evaluation view: recorded future values, never model input
        p = engine._panel()
        ti = p.at(s.t)
        si = s.idx[segment_id]
        out["actual_future"] = {m: [{"horizon_min": h, "value": float(p.v[col][si, ti + st]) if ti + st < len(p.times) else None}
                                    for h, st in C.HORIZONS.items()] for m, col in C.METRICS_FC.items()}
    return clean(out)


@app.get("/api/forecast-map")
def forecast_map(t: str | None = TimeQ, horizon: int = Query(30)):
    """Forecast traffic state of every segment at +horizon min, for the map's forecast mode.
    Read-only view of the same P50 speeds the per-segment forecast returns; no model or logic change."""
    if horizon not in C.HORIZONS:
        raise HTTPException(422, f"horizon must be one of {list(C.HORIZONS)}")
    s = _snap(t)
    if s.forecast is None:
        raise HTTPException(503, "forecast models unavailable — run scripts/train_models.py")
    ff = data.network().set_index("segment_id").free_flow_speed_kmh.reindex(s.p.segs).to_numpy()
    speed = s.forecast[("speed", horizon)]["p50"]
    cong = s.forecast[("congestion", horizon)]["p50"]
    ratio = speed / ff
    state = state_of(ratio)
    return clean({"time": str(s.t), "mode": "FORECAST", "horizon_min": horizon,
                  "source": "LightGBM P50 forecast issued from data <= time",
                  "segments": [{"segment_id": seg, "speed_kmh": round(float(speed[i]), 2), "speed_ratio": round(float(ratio[i]), 3),
                                "congestion_index": round(float(cong[i]), 4), "state": str(state[i])}
                               for i, seg in enumerate(s.p.segs)]})


@app.get("/api/detection")
def detection(t: str | None = TimeQ):
    s = _snap(t)
    flagged = s.rows[s.rows.anomalous | (s.rows.state != "NORMAL")]
    out = []
    for seg in flagged.segment_id:
        dg = s.diagnosis(seg)
        out.append({**s.row(seg), "cause": dg["cause"], "detected_incident": dg["detected"],
                    "confidence_label": dg["confidence_label"], "reasons": dg["reasons"]})
    return clean({"time": str(s.t), "threshold": s.thr, "segments": sorted(out, key=lambda r: -r["anomaly_score"])})


@app.get("/api/incidents")
def incidents(t: str | None = TimeQ):
    s = _snap(t)
    return clean({"time": str(s.t), "source": "DETECTED from traffic + context (labels are not read at runtime)",
                  "lookback_min": 180, "events": s.events()})


@app.get("/api/propagation/{segment_id}")
def propagation_route(segment_id: str, t: str | None = TimeQ, horizon: int | None = Query(None, ge=5, le=60)):
    s = _snap(t)
    _seg(s, segment_id)
    return clean({"time": str(s.t), "source": segment_id, "horizon_min": horizon,
                  "source_drop": float(max(0.0, np.nan_to_num(s.c["local_drop"][s.idx[segment_id], s.ti]))),
                  "model": "explicit hop decay x load factor, calibrated on incidents_train (not a GNN)",
                  "neighbors": s.propagation_for(segment_id, horizon)})


@app.get("/api/recommendations/{segment_id}")
def recommendations_route(segment_id: str, t: str | None = TimeQ):
    s = _snap(t)
    _seg(s, segment_id)
    return clean(recommendations.recommend(str(s.t), segment_id))


@app.get("/api/candidates")
def candidates():
    """The organizer's planning candidates (the only interventions the system may propose)."""
    return clean(data.table("planning_candidates").sort_values("candidate_id").to_dict("records"))


@app.post("/api/simulation/run", response_model=SimulationResponse)
def simulation_run(req: SimulationRequest):
    s = _snap(req.time)
    if req.incident_segment:
        _seg(s, req.incident_segment)
    cand = None
    if req.candidate_id:
        pc = data.table("planning_candidates").set_index("candidate_id")
        if req.candidate_id not in pc.index:
            raise HTTPException(404, f"unknown candidate {req.candidate_id}")
        cand = pc.loc[req.candidate_id]
    obs = np.nan_to_num(s.p.v["flow_vph"][:, s.ti].astype(float))
    ref = {req.incident_segment: 1 - req.capacity_reduction} if req.incident_segment and req.capacity_reduction else {}
    try:
        base = simulation.scenario(s.t.hour, obs, ref)
        if cand is not None:
            cf = simulation.scenario(s.t.hour, obs, ref, cap_delta={cand.target_segment: float(cand.capacity_delta_vph)})
            desc = f"{req.candidate_id} ({cand.intervention_type}, +{int(cand.capacity_delta_vph)} veh/h on {cand.target_segment})"
        elif ref:
            cf = simulation.scenario(s.t.hour, obs, ref, cap_mult={req.incident_segment: 1.0})
            desc = f"incident on {req.incident_segment} cleared"
        else:
            raise HTTPException(422, "provide candidate_id and/or incident_segment with capacity_reduction > 0")
    except (ValueError, RuntimeError) as e:
        raise HTTPException(422, str(e))
    focus = [x for x in [req.incident_segment, cand.target_segment if cand is not None else None] if x]
    cmp_ = simulation.compare(base, cf, focus or None)
    bdesc = (f"current network at {s.t}" + (f" with {req.capacity_reduction:.0%} capacity loss on {req.incident_segment}" if ref else ""))
    return clean({"time": str(s.t), "label": "SIMULATION ESTIMATE — not a verified outcome",
                  "baseline_description": bdesc, "counterfactual_description": desc, **cmp_})


@app.get("/api/evaluation/{kind}")
def evaluation(kind: Literal["forecast", "detection", "robustness", "bottleneck", "propagation", "simulation"]):
    path = C.METRICS / f"{kind}_metrics.json"
    if not path.exists():
        raise HTTPException(503, f"{kind} metrics not generated — run scripts/evaluate_models.py / run_robustness.py")
    return json.loads(path.read_text())


@app.get("/api/scenarios")
def scenarios():
    sc = data.table("scenario_examples")
    cand = set(data.table("planning_candidates").target_segment)
    rows = [{"scenario_id": r.scenario_id, "target_segment": r.target_segment, "start_time": str(r.start_time),
             "end_time": str(r.end_time), "demo_time": str((pd.Timestamp(r.start_time).ceil("5min") + pd.Timedelta(minutes=10))),
             "organizer_incident_type": r.incident_type, "organizer_severity": int(r.severity),
             "direct_candidate": r.target_segment in cand} for r in sc.itertuples()]
    return {"note": "Organizer worked examples (duplicates of incidents_train rows) — demo templates, not independent validation. "
                    "Their labels are shown for reference only and are never fed to the detector.",
            "scenarios": rows}


@app.get("/api/scenarios/{scenario_id}")
def scenario_detail(scenario_id: str):
    match = [r for r in scenarios()["scenarios"] if r["scenario_id"] == scenario_id]
    if not match:
        raise HTTPException(404, f"unknown scenario {scenario_id}")
    sc = match[0]
    s = _snap(sc["demo_time"])
    seg = sc["target_segment"]
    return clean({**sc, "detected_state": s.row(seg), "diagnosis": s.diagnosis(seg)})


def _warm():
    """Pre-compute the demo snapshot and its top recommendation so the first page load is fast."""
    try:
        s = engine.get()
        top = s.rows.sort_values("anomaly_score", ascending=False).segment_id.iloc[0]
        recommendations.recommend(str(s.t), top)
    except Exception as e:  # warming is best-effort; endpoints report real errors
        print(f"warm-up skipped: {e}")


