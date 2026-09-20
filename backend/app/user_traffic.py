"""Rider-facing traffic intelligence: a real route (from a routing provider) + FlowSense's SIMULATED traffic.

Honesty first: FlowSense traffic belongs to the organizer's synthetic 12x10 grid, not to real streets. A real route is
therefore only *approximately* related to it: each stretch of the route takes the traffic state of the worst simulated
segment within SEG_RADIUS_M (else the nearest one within FALLBACK_M). Every rider-facing number is labelled simulated.
Nothing here is random and nothing is invented: no simulated segment nearby -> "no data".

Ranking (transparent, see docs/USER_PORTAL.md):
    eta          = provider duration (no traffic) + simulated delay now
    worsening    = max(0, simulated delay at +30 min - delay now)
    score        = eta + 0.5 * worsening + 0.1 * distance_km            (lower is better)
"""
from functools import lru_cache

import numpy as np

from . import config as C, data, detection

SEG_RADIUS_M = 750        # a route stretch inherits the worst simulated segment within this distance
FALLBACK_M = 1500         # ... else the nearest one within this distance; beyond it: no data
SAMPLE_M = 120            # route sampling step
LEVEL_MIN_M = 300         # a level only counts for the route once it spans this much road
FREE_FLOW_RATIO = 0.85    # speed-ratio at/above which traffic is treated as free flow (no delay)
MIN_RATIO = 0.25          # floor: bounds the delay a single stretch can add
WORSEN_WEIGHT = 0.5
DISTANCE_WEIGHT = 0.1
DIVERSION_MIN_SAVING = 2.0
FORECAST_HORIZONS = (15, 30, 60)

LEVEL_OF_STATE = {"NORMAL": "free_flow", "MODERATE": "moderate", "HEAVY": "heavy", "CRITICAL": "severe", "NO_DATA": "no_data"}
LEVEL_LABEL = {"free_flow": "Free flow", "moderate": "Moderate traffic", "heavy": "Heavy traffic", "severe": "Severe congestion", "no_data": "No data"}
RANK_LABEL = ["Recommended route", "Alternative", "Second alternative", "Third alternative"]

_LAT0, _LON0 = 17.4, 78.45
_KX, _KY = 111320 * np.cos(np.radians(_LAT0)), 110540.0


def project(latlon: np.ndarray) -> np.ndarray:
    """(N, 2) lat/lon -> local metres (x east, y north). Accurate to a few metres across the Hyderabad area."""
    return np.column_stack([(latlon[:, 1] - _LON0) * _KX, (latlon[:, 0] - _LAT0) * _KY])


@lru_cache(maxsize=1)
def _segments():
    """Simulated segments as straight node-to-node lines (metres) + the extent of the simulation area."""
    net = data.network()
    nodes = data.table("nodes").set_index("node_id")
    a = nodes.loc[net.source_node, ["lat", "lon"]].to_numpy(float)
    b = nodes.loc[net.target_node, ["lat", "lon"]].to_numpy(float)
    lat, lon = nodes.lat.to_numpy(), nodes.lon.to_numpy()
    pad = 0.009                                        # ~1 km beyond the grid
    area = (lat.min() - pad, lon.min() - pad, lat.max() + pad, lon.max() + pad)
    return list(net.segment_id), a, b, project(a), project(b), area


def dist_to_segments(pts: np.ndarray, A: np.ndarray, B: np.ndarray) -> np.ndarray:
    """(N, S) distances in metres from points to line segments."""
    ab = B - A
    l2 = np.maximum((ab ** 2).sum(1), 1e-9)
    ap = pts[:, None, :] - A[None]
    t = np.clip((ap * ab[None]).sum(2) / l2, 0, 1)
    proj = A[None] + t[..., None] * ab[None]
    return np.linalg.norm(pts[:, None, :] - proj, axis=2)


def resample(xy: np.ndarray, step: float):
    """Points every ~step metres along a polyline: (points, positions, length represented by each point)."""
    seg = np.linalg.norm(np.diff(xy, axis=0), axis=1)
    cum = np.concatenate([[0.0], np.cumsum(seg)])
    total = float(cum[-1])
    n = max(2, int(np.ceil(total / step)) + 1)
    pos = np.linspace(0, total, n)
    pts = np.column_stack([np.interp(pos, cum, xy[:, 0]), np.interp(pos, cum, xy[:, 1])])
    edges = np.concatenate([[0.0], (pos[:-1] + pos[1:]) / 2, [total]])
    return pts, pos, np.diff(edges), cum


def _ratios(snap):
    """Current and forecast speed ratio (speed / free-flow speed) per simulated segment, in network order."""
    ff = data.network().set_index("segment_id").free_flow_speed_kmh.reindex(snap.p.segs).to_numpy(float)
    now = np.nan_to_num(snap.c["sr"][:, snap.ti].astype(float), nan=1.0)
    fc = {}
    if snap.forecast is not None:
        for h in FORECAST_HORIZONS:
            fc[h] = np.nan_to_num(snap.forecast[("speed", h)]["p50"].astype(float) / ff, nan=1.0)
    return now, fc


def _stretch_ratio(dist: np.ndarray, ratio: np.ndarray):
    """Per sample: worst ratio among segments within SEG_RADIUS_M, else the nearest within FALLBACK_M; NaN = no data."""
    near = dist <= SEG_RADIUS_M
    worst = np.where(near, ratio[None, :], np.inf).min(1)
    nearest = ratio[dist.argmin(1)]
    out = np.where(np.isfinite(worst), worst, np.where(dist.min(1) <= FALLBACK_M, nearest, np.nan))
    return out


def _delay_min(ratio: np.ndarray, ds_m: np.ndarray, base_kmh: float) -> float:
    eff = np.where(np.isnan(ratio) | (ratio >= FREE_FLOW_RATIO), 1.0, np.maximum(ratio, MIN_RATIO))
    return float(((ds_m / 1000) / max(base_kmh, 5.0) * 60 * (1 / eff - 1)).sum())


def _levels(ratio: np.ndarray) -> np.ndarray:
    names = np.where(np.isnan(ratio), "NO_DATA", detection.state_of(np.nan_to_num(ratio, nan=1.0)))
    return np.array([LEVEL_OF_STATE[s] for s in names])


def _route_level(level: np.ndarray, ds: np.ndarray) -> str:
    length = {k: float(ds[level == k].sum()) for k in ("severe", "heavy", "moderate")}
    if length["severe"] >= LEVEL_MIN_M:
        return "severe"
    if length["severe"] + length["heavy"] >= LEVEL_MIN_M:
        return "heavy"
    if sum(length.values()) >= LEVEL_MIN_M:
        return "moderate"
    return "free_flow"


def assess_route(route: dict, snap) -> dict:
    """Simulated traffic along one real route."""
    latlon = np.array([[c[1], c[0]] for c in route["coords"]], float)
    xy = project(latlon)
    pts, pos, ds, cum = resample(xy, SAMPLE_M)
    segs, _, _, A, B, area = _segments()
    dist = dist_to_segments(pts, A, B)
    now, fc = _ratios(snap)
    base_kmh = route["distance_km"] / max(route["duration_min"], 0.1) * 60
    r_now = _stretch_ratio(dist, now)
    level = _levels(r_now)
    lat = np.interp(pos, cum, latlon[:, 0]); lon = np.interp(pos, cum, latlon[:, 1])
    inside = (lat >= area[0]) & (lat <= area[2]) & (lon >= area[1]) & (lon <= area[3])
    delay_now = _delay_min(r_now, ds, base_kmh)
    delay_fc = {h: _delay_min(_stretch_ratio(dist, r), ds, base_kmh) for h, r in fc.items()}
    return {
        "traffic_level": _route_level(level, ds), "delay_min": delay_now,
        "delay_forecast_min": delay_fc, "coverage_pct": float(ds[inside].sum() / max(ds.sum(), 1) * 100),
        "stretches": _stretches(level, pos, ds, cum, latlon),
        "worst": _worst_point(level, r_now, lat, lon),
    }


def _stretches(level: np.ndarray, pos: np.ndarray, ds: np.ndarray, cum: np.ndarray, latlon: np.ndarray) -> list[dict]:
    """Contiguous runs of one traffic level, each with the route's own geometry, so the map can colour the route."""
    edges = np.concatenate([[0.0], (pos[:-1] + pos[1:]) / 2, [cum[-1]]])
    out, i = [], 0
    while i < len(level):
        j = i
        while j + 1 < len(level) and level[j + 1] == level[i]:
            j += 1
        d0, d1 = edges[i], edges[j + 1]
        inner = (cum > d0) & (cum < d1)
        coords = ([[float(np.interp(d0, cum, latlon[:, 0])), float(np.interp(d0, cum, latlon[:, 1]))]] + latlon[inner].tolist()
                  + [[float(np.interp(d1, cum, latlon[:, 0])), float(np.interp(d1, cum, latlon[:, 1]))]])
        out.append({"level": str(level[i]), "length_m": round(float(d1 - d0)), "coords": [[round(a, 5), round(b, 5)] for a, b in coords]})
        i = j + 1
    return out


def _worst_point(level, ratio, lat, lon):
    """Location of the most congested stretch (for a map marker), or None on a clear route."""
    order = {"severe": 3, "heavy": 2, "moderate": 1}
    rank = np.array([order.get(k, 0) for k in level])
    if rank.max() < 2:
        return None
    i = int(np.lexsort((-np.nan_to_num(ratio, nan=1.0), rank))[-1])
    return {"lat": round(float(lat[i]), 5), "lon": round(float(lon[i]), 5), "level": str(level[i])}


# ------------------------------------------------------------------ ranking, diversion, alerts
def build_options(provider_routes: list[dict], snap) -> dict:
    """Assess, rank and compare route candidates. The provider's first route is the 'current' (default) route."""
    routes = []
    for i, pr in enumerate(provider_routes):
        a = assess_route(pr, snap)
        eta = pr["duration_min"] + a["delay_min"]
        worsening = max(0.0, a["delay_forecast_min"].get(30, a["delay_min"]) - a["delay_min"])
        routes.append({
            "id": f"r{i + 1}", "default": i == 0, "distance_km": round(pr["distance_km"], 1), "duration_min": round(eta),
            "free_flow_min": round(pr["duration_min"]), "delay_min": round(a["delay_min"]), "traffic_level": a["traffic_level"],
            "traffic_label": LEVEL_LABEL[a["traffic_level"]], "expected_worsening_min": round(worsening),
            "forecast": {str(h): round(pr["duration_min"] + d) for h, d in a["delay_forecast_min"].items()},
            "simulation_coverage_pct": round(a["coverage_pct"]), "stretches": a["stretches"], "worst": a["worst"],
            "coords": [[c[1], c[0]] for c in pr["coords"]],
            "_score": eta + WORSEN_WEIGHT * worsening + DISTANCE_WEIGHT * pr["distance_km"], "_expected": eta + WORSEN_WEIGHT * worsening,
        })
    for rank, r in enumerate(sorted(routes, key=lambda r: r["_score"]), 1):
        r["ranking"], r["recommended"] = rank, rank == 1
        r["label"] = RANK_LABEL[min(rank - 1, len(RANK_LABEL) - 1)]
    routes.sort(key=lambda r: r["ranking"])
    return {"routes": [{k: v for k, v in r.items() if not k.startswith("_")} for r in routes],
            "diversion": _diversion(routes), "alerts": _route_alerts(routes)}


def _diversion(routes: list[dict]) -> dict | None:
    cur = next(r for r in routes if r["default"])
    alts = [r for r in routes if not r["default"]]
    if not alts:
        return None
    alt = min(alts, key=lambda r: r["_expected"])
    saving = cur["_expected"] - alt["_expected"]
    trouble = cur["traffic_level"] in ("heavy", "severe") or cur["expected_worsening_min"] >= 3
    if not (trouble and saving >= DIVERSION_MIN_SAVING):
        return None
    solid = saving >= 5 and min(cur["simulation_coverage_pct"], alt["simulation_coverage_pct"]) >= 40
    return {"recommended": True, "current_route_id": cur["id"], "alternative_route_id": alt["id"],
            "current_eta_min": cur["duration_min"], "alternative_eta_min": alt["duration_min"], "estimated_saving_min": round(saving),
            "alternative_traffic_level": alt["traffic_level"], "confidence": "Medium" if solid else "Low",
            "reason": "severe" if cur["traffic_level"] == "severe" else "heavy" if cur["traffic_level"] == "heavy" else "worsening"}


def _route_alerts(routes: list[dict]) -> list[dict]:
    """Alerts for the route the rider would take by default, worded for riders (no model internals, no invented accidents)."""
    cur = next(r for r in routes if r["default"])
    out = []
    if cur["traffic_level"] in ("heavy", "severe"):
        sev = cur["traffic_level"]
        out.append({"type": "congestion", "severity": sev, "route_id": cur["id"],
                    "title": "Severe congestion ahead" if sev == "severe" else "Heavy traffic ahead",
                    "message": "Traffic ahead on the current route is severe." if sev == "severe" else "Traffic ahead on the current route is heavy.",
                    "lat": (cur["worst"] or {}).get("lat"), "lon": (cur["worst"] or {}).get("lon"), "segment_id": None, "confidence": "medium"})
    if cur["expected_worsening_min"] >= 2:
        out.append({"type": "forecast", "severity": "warning", "route_id": cur["id"], "title": "Traffic is getting heavier",
                    "message": f"Traffic is expected to worsen in about 30 minutes (roughly +{cur['expected_worsening_min']} min).",
                    "lat": None, "lon": None, "segment_id": None, "confidence": "medium"})
    return out


def area_alerts(snap) -> list[dict]:
    """Traffic disruptions currently detected in the simulated area, in rider language.
    A detected disruption is not a confirmed accident, and is never called one."""
    _, a, b, *_ = _segments()
    idx = {s: i for i, s in enumerate(_segments()[0])}
    out = []
    for e in snap.events():
        if not e["ongoing"] or e["state"] not in ("HEAVY", "CRITICAL"):
            continue
        i = idx[e["segment_id"]]
        sev = "severe" if e["state"] == "CRITICAL" else "heavy"
        out.append({"type": "disruption", "severity": sev, "title": "Traffic disruption detected",
                    "message": "Possible incident. Severe congestion is likely nearby; expect delays." if sev == "severe"
                    else "Traffic is heavy here; expect delays.",
                    "segment_id": e["segment_id"], "lat": round(float((a[i, 0] + b[i, 0]) / 2), 5), "lon": round(float((a[i, 1] + b[i, 1]) / 2), 5),
                    "confidence": {"HIGH": "high", "MEDIUM": "medium"}.get(e.get("confidence_label") or "", "low")})
    return out


def traffic_zones(snap) -> dict:
    """Non-normal simulated segments, as rider-facing 'congestion areas' (no model internals)."""
    segs, a, b, *_ = _segments()
    rows = snap.rows.set_index("segment_id")
    zones, counts = [], {k: 0 for k in LEVEL_LABEL}
    for i, s in enumerate(segs):
        r = rows.loc[s]
        level = LEVEL_OF_STATE.get(r.state, "no_data")
        counts[level] += 1
        if level in ("free_flow", "no_data"):
            continue
        zones.append({"segment_id": s, "traffic_level": level, "status": LEVEL_LABEL[level],
                      "speed_kmh": None if r.speed_kmh != r.speed_kmh else round(float(r.speed_kmh), 1),
                      "congestion": None if r.congestion_index != r.congestion_index else round(float(r.congestion_index), 3),
                      "updated_at": str(snap.t), "path": [[round(float(a[i, 0]), 5), round(float(a[i, 1]), 5)], [round(float(b[i, 0]), 5), round(float(b[i, 1]), 5)]]})
    return {"zones": zones, "counts": counts}
