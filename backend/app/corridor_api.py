"""Operator-only corridor / flyover analysis API (protected by the same session middleware as every non-public /api route)."""
import math

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from . import config as C, corridor, infrastructure

router = APIRouter(prefix="/api")
MAX_KM = 40


class LatLng(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)


class AnalyzeRequest(BaseModel):
    origin: LatLng
    destination: LatLng
    time: str | None = Field(None, max_length=32, description="Replay moment (default: the demo time)")


class PredictRequest(BaseModel):
    segment_ids: list[str] = Field(min_length=1, max_length=50)
    time: str | None = Field(None, max_length=32)


def _run(body: AnalyzeRequest) -> dict:
    a = C.SERVICE_AREA
    for name, p in (("origin", body.origin), ("destination", body.destination)):
        if not (a["south"] <= p.lat <= a["north"] and a["west"] <= p.lng <= a["east"]):
            raise HTTPException(422, f"The {name} is outside the supported area (Hyderabad).")
    d = math.hypot((body.origin.lat - body.destination.lat) * 110.5, (body.origin.lng - body.destination.lng) * 104.0)
    if d < 0.1:
        raise HTTPException(422, "Origin and destination are too close together to analyse a corridor.")
    if d > MAX_KM:
        raise HTTPException(422, f"Origin and destination are {d:.0f} km apart; corridors are limited to {MAX_KM} km.")
    try:
        return corridor.analyze((body.origin.lat, body.origin.lng), (body.destination.lat, body.destination.lng), body.time)
    except corridor.CorridorError as e:
        raise HTTPException(e.status, e.message)


def _get(cid: str) -> dict:
    r = corridor.get_cached(cid)
    if r is None:
        raise HTTPException(404, "Corridor analysis not found. Run the analysis again (results are kept in memory for the last 50 runs).")
    return r


@router.post("/corridor/analyze")
def analyze(body: AnalyzeRequest):
    return _run(body)


@router.get("/corridor/{cid}")
def get_corridor(cid: str):
    return _get(cid)


@router.get("/corridor/{cid}/segments")
def get_segments(cid: str):
    return {"segments": _get(cid)["segments"], "field_sources": _get(cid)["field_sources"]}


@router.get("/corridor/{cid}/bottlenecks")
def get_bottlenecks(cid: str):
    r = _get(cid)
    ids = set(r["bottlenecks"]["segments"])
    return {**r["bottlenecks"], "details": [s for s in r["segments"] if s["segment_id"] in ids]}


@router.get("/corridor/{cid}/candidates")
def get_candidates(cid: str):
    r = _get(cid)
    return {"candidates": r["candidates"], "disclaimer": r["disclaimer"], "confidence": r["confidence"]}


@router.post("/flyover/analyze")
def flyover(body: AnalyzeRequest):
    r = _run(body)
    return {"corridor_id": r["id"], "mode": r["mode"], "data_notice": r["data_notice"], "corridor": {k: v for k, v in r["corridor"].items() if k != "coords"},
            "candidates": r["candidates"], "recommended_analysis": r["recommended_analysis"], "confidence": r["confidence"], "disclaimer": r["disclaimer"]}


@router.get("/flyover/model-info")
def model_info():
    return corridor.model_info()


@router.post("/traffic/predict")
def predict(body: PredictRequest):
    try:
        return corridor.predict(body.segment_ids, body.time)
    except corridor.CorridorError as e:
        raise HTTPException(e.status, e.message)


class SimulateRequest(BaseModel):
    corridor_id: str = Field(min_length=4, max_length=32)
    intervention: str = Field(min_length=3, max_length=40)


@router.post("/infrastructure/analyze")
def infrastructure_analyze(body: AnalyzeRequest):
    """Corridor analysis + intervention comparison + recommendation. Traffic from the corridor engine, land/infrastructure from the InfrastructureDataProvider."""
    corr = _run(body)
    try:
        return infrastructure.analyze(corr)
    except corridor.CorridorError as e:
        raise HTTPException(e.status, e.message)


@router.post("/infrastructure/simulate")
def infrastructure_simulate(body: SimulateRequest):
    try:
        return infrastructure.simulate(body.corridor_id, body.intervention)
    except corridor.CorridorError as e:
        raise HTTPException(e.status, e.message)
