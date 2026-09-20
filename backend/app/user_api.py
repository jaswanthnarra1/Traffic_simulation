"""Public rider API: /api/user/*. No operator session needed, and none of it exposes operator data.

Boundaries:
- Only these routes are public (api.py's middleware allows the /api/user/ prefix; every other /api/* route still needs the operator session).
- Riders never choose a timestamp: they pick a replay `step`, and the server maps it to the simulation clock. That bounds how many
  distinct snapshots a public caller can force the server to compute.
- Responses use rider language and flat objects: no anomaly scores, model versions, evaluation data or internal labels.
- Location goes in POST bodies, not URLs, so precise coordinates stay out of access logs.
"""
import threading
import time
from collections import defaultdict, deque

import pandas as pd
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from . import config as C, engine, providers, user_explain as UX, user_traffic as UT

router = APIRouter(prefix="/api/user", tags=["user"])


# ------------------------------------------------------------------ rate limiting
class RateLimiter:
    """Sliding-window limiter per (bucket, client IP).
    ponytail: in-process memory — correct for one server process; use Redis or a gateway for several."""

    def __init__(self):
        self.hits, self.lock = defaultdict(deque), threading.Lock()

    def allow(self, bucket: str, ip: str, limit: int, window: float) -> bool:
        now = time.monotonic()
        with self.lock:
            q = self.hits[(bucket, ip)]
            while q and q[0] <= now - window:
                q.popleft()
            if len(q) >= limit:
                return False
            q.append(now)
            return True


limiter = RateLimiter()
LIMITS = {"traffic": 60, "alerts": 60, "geocode": 40, "route": 20, "nearby": 20, "config": 60, "explain": 40}   # requests per minute per IP


def throttle(request: Request, bucket: str):
    ip = request.client.host if request.client else "unknown"
    if not limiter.allow(bucket, ip, LIMITS[bucket], 60.0):
        raise HTTPException(429, "You're going a bit fast. Please wait a moment and try again.")


# ------------------------------------------------------------------ simulation clock (server-owned)
def sim_time(step: int) -> str:
    start = pd.Timestamp(C.DEMO_TIME) - pd.Timedelta(minutes=C.USER_REPLAY_BACK_MIN)
    return str(start + pd.Timedelta(minutes=5 * step))


def snapshot(step: int):
    try:
        return engine.get(sim_time(step))
    except FileNotFoundError:
        raise HTTPException(503, "Traffic information is unavailable right now.")


StepQ = Query(C.USER_DEFAULT_STEP, ge=0, lt=C.USER_REPLAY_STEPS, description="Replay step (5 simulated minutes each)")


# ------------------------------------------------------------------ models
class Point(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)


def in_service_area(p: Point) -> bool:
    a = C.SERVICE_AREA
    return a["south"] <= p.lat <= a["north"] and a["west"] <= p.lon <= a["east"]


class RouteRequest(BaseModel):
    origin: Point
    destination: Point
    step: int = Field(C.USER_DEFAULT_STEP, ge=0, lt=C.USER_REPLAY_STEPS)


class NearbyRequest(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    categories: list[str] = Field(min_length=1, max_length=len(providers.CATEGORIES))
    radius_m: int = Field(1200, ge=200, le=5000)


def provider_call(fn, *a):
    try:
        return fn(*a)
    except providers.ProviderError as e:
        raise HTTPException(e.status, e.message)


# ------------------------------------------------------------------ routes
@router.get("/config")
def user_config(request: Request):
    """Public, non-secret configuration for the rider app. Never includes API keys."""
    throttle(request, "config")
    a = C.SERVICE_AREA
    return {"mode": "simulation",
            "traffic_source": "FlowSense traffic simulation (organizer dataset replay) — not live city traffic",
            "simulation": {"start": sim_time(0), "step_minutes": 5, "steps": C.USER_REPLAY_STEPS, "default_step": C.USER_DEFAULT_STEP},
            "service_area": a, "center": {"lat": round((a["south"] + a["north"]) / 2, 4), "lon": round((a["west"] + a["east"]) / 2, 4)},
            "providers": {"geocoding": C.GEOCODING_PROVIDER, "routing": C.ROUTING_PROVIDER, "places": "overpass"},
            "categories": [{"id": k, "label": v[0]} for k, v in providers.CATEGORIES.items()]}


@router.get("/traffic")
def user_traffic(request: Request, step: int = StepQ):
    """Simulated traffic as congestion areas. Normal segments are omitted: the rider map is a real road map, not the grid."""
    throttle(request, "traffic")
    s = snapshot(step)
    z = UT.traffic_zones(s)
    return {"mode": "simulation", "step": step, "time": str(s.t), "updated_at": str(s.t), "counts": z["counts"], "zones": z["zones"]}


@router.get("/alerts")
def user_alerts(request: Request, step: int = StepQ):
    throttle(request, "alerts")
    s = snapshot(step)
    return {"mode": "simulation", "step": step, "time": str(s.t), "alerts": UT.area_alerts(s)}


class ExplainRequest(BaseModel):
    lat: float | None = Field(None, ge=-90, le=90)
    lon: float | None = Field(None, ge=-180, le=180)
    segment_id: str | None = Field(None, max_length=32)
    step: int = Field(C.USER_DEFAULT_STEP, ge=0, lt=C.USER_REPLAY_STEPS)


@router.post("/explain")
def user_explain(request: Request, body: ExplainRequest):
    """Why is traffic heavy here? Cause, evidence and forecast from FlowSense's own diagnosis (see user_explain.py)."""
    throttle(request, "explain")
    s = snapshot(body.step)
    seg = body.segment_id if body.segment_id in s.idx else None
    if seg is None and body.lat is not None and body.lon is not None:
        seg = UX.find_segment(s, body.lat, body.lon)
    if seg is None:
        return {"status": "normal", "headline": "No significant traffic problem here", "summary": "Traffic is flowing normally at this location.",
                "cause": None, "evidence": [], "forecast": None, "updated_at": str(s.t)}
    try:
        return UX.explain(s, seg)
    except Exception:
        # never a stack trace, never an invented cause
        return {"status": "unavailable", "headline": "Traffic disruption detected", "summary": "Traffic disruption detected, but the cause could not be determined.",
                "cause": None, "evidence": [], "forecast": None, "updated_at": str(s.t)}


@router.get("/geocode")
def user_geocode(request: Request, q: str = Query(min_length=2, max_length=100)):
    throttle(request, "geocode")
    q = " ".join(q.split())
    if len(q) < 2:
        raise HTTPException(422, "Type at least two characters to search.")
    return {"query": q, "results": provider_call(providers.geocode, q)}


@router.post("/route-options")
def user_route_options(request: Request, body: RouteRequest):
    throttle(request, "route")
    if not (in_service_area(body.origin) and in_service_area(body.destination)):
        raise HTTPException(422, "Directions are available within the Hyderabad area only.")
    if providers.haversine_m(body.origin.lat, body.origin.lon, body.destination.lat, body.destination.lon) < 100:
        raise HTTPException(422, "Your start and destination are almost the same place.")
    routes = provider_call(providers.route_options, (body.origin.lat, body.origin.lon), (body.destination.lat, body.destination.lon))
    out = UT.build_options(routes, snapshot(body.step))
    return {"mode": "simulation", "step": body.step, "time": sim_time(body.step), "routing_provider": C.ROUTING_PROVIDER,
            "traffic_note": "Traffic is simulated by FlowSense and only approximately matched to these roads.", **out}


@router.post("/nearby")
def user_nearby(request: Request, body: NearbyRequest):
    throttle(request, "nearby")
    bad = [c for c in body.categories if c not in providers.CATEGORIES]
    if bad:
        raise HTTPException(422, "Unknown place category.")
    if not in_service_area(Point(lat=body.lat, lon=body.lon)):
        raise HTTPException(422, "Nearby places are available within the Hyderabad area only.")
    cats = list(dict.fromkeys(body.categories))
    return {"places": provider_call(providers.nearby, body.lat, body.lon, cats, body.radius_m), "radius_m": body.radius_m}
