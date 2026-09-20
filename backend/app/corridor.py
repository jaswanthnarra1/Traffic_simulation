"""Corridor analysis engine: origin -> route -> segments -> traffic features -> congestion -> bottlenecks -> candidate zones ->
start/end optimisation -> suitability, explanation, alternatives and data confidence. See docs/TRAFFIC_CORRIDOR_ANALYSIS.md.

Design rules
- Reuses what the project already has: the road network + sanitised traffic panel (data.py), the LightGBM forecaster and snapshot
  (engine.py), the incident diagnosis, the routing provider (providers.py) and the route<->simulation matching (user_traffic.py).
- Nothing is invented. A field the data cannot supply is None with an explicit source tag ("data_required" / "unavailable" /
  "estimated" / "simulated"), and every score is a weighted mean over the components that DO have data (weights re-normalised).
- All weights and thresholds are in corridor_config.json. There is no labelled flyover ground truth, so scores are transparent
  design choices, not a trained classifier; only the traffic forecast is a learned model (the existing LightGBM one).
- The traffic FlowSense holds belongs to a synthetic 12x10 grid, not to real streets. A real route is matched to the nearest
  grid segment within match_radius_m (direction-aware); beyond that the stretch has no data and is excluded from every score.
"""
import hashlib
import json
import os
import time
from collections import OrderedDict
from functools import lru_cache
from pathlib import Path
from typing import Protocol

import numpy as np
import pandas as pd

from . import config as C, data, detection, engine, providers, user_explain, user_traffic as UT

CFG_PATH = Path(os.getenv("CORRIDOR_CONFIG_PATH") or Path(__file__).with_name("corridor_config.json"))
STATS_PATH = C.META / "corridor_segment_stats.parquet"
METRICS_PATH = C.METRICS / "corridor_metrics.json"
LEVELS = ["LOW", "MODERATE", "HIGH", "CRITICAL"]
SEVERITY_COLOR = {"LOW": "#2f8f5b", "MODERATE": "#d4a21a", "HIGH": "#e0661b", "CRITICAL": "#c62828", "NO_DATA": "#9ca3af"}


@lru_cache(maxsize=1)
def cfg() -> dict:
    return json.loads(CFG_PATH.read_text())


class CorridorError(Exception):
    def __init__(self, message: str, status: int = 422):
        super().__init__(message)
        self.message, self.status = message, status


# ------------------------------------------------------------------ traffic data provider seam
class TrafficDataProvider(Protocol):
    """Where traffic comes from. Plug a live feed in by implementing this and registering it in PROVIDERS."""
    name: str
    live: bool
    def segment_stats(self) -> pd.DataFrame: ...
    def current(self, when): ...           # snapshot-like object for `when`, or None


class DatasetTrafficProvider:
    """Historical peak-hour statistics from the organizer dataset + the FlowSense replay as 'current'. SIMULATION, not live."""
    name, live = "dataset (historical panel + simulation replay)", False

    def segment_stats(self) -> pd.DataFrame:
        return segment_stats()

    def current(self, when):
        try:
            return engine.get(when)
        except FileNotFoundError:
            return None


PROVIDERS = {"dataset": DatasetTrafficProvider}
def get_provider() -> TrafficDataProvider:
    name = os.getenv("CORRIDOR_TRAFFIC_PROVIDER", "dataset")
    if name not in PROVIDERS:
        raise CorridorError(f"Traffic provider '{name}' is not available. Configured providers: {', '.join(PROVIDERS)}.", 503)
    return PROVIDERS[name]()


# ------------------------------------------------------------------ precomputed per-segment historical features
def _build_stats() -> pd.DataFrame:
    h = cfg()["history"]
    p = data.full_panel()
    net = data.network().set_index("segment_id").reindex(p.segs)
    t = p.times
    train = np.asarray(t <= pd.Timestamp(C.TRAIN_END) + pd.Timedelta(days=1) - pd.Timedelta(minutes=5))
    wk = np.asarray(t.dayofweek < 5) if h["weekdays_only"] else np.ones(len(t), bool)
    hours = np.asarray(t.hour)
    peak = np.isin(hours, h["peak_hours"]) & wk & train
    off = np.isin(hours, range(10, 16)) & wk & train
    flow, speed, delay = p.v["flow_vph"].astype(float), p.v["speed_kmh"].astype(float), p.v["delay_min"].astype(float)
    ff, cap, length = net.free_flow_speed_kmh.to_numpy(float), net.capacity_vph.to_numpy(float), net.length_km.to_numpy(float)
    sr_peak = np.nanmean(speed[:, peak], 1) / ff
    sr_off = np.nanmean(speed[:, off], 1) / ff
    days = pd.DatetimeIndex(t[peak]).normalize()
    dm = np.array([np.nanmean(flow[:, peak][:, days == d], 1) for d in days.unique()])        # (days, S)
    w = h["growth_window_days"]
    growth = (np.nanmean(dm[-w:], 0) / np.nanmean(dm[:w], 0) - 1) * 100 if len(dm) >= 2 * w else np.full(len(p.segs), np.nan)
    sig = data.table("signal_plans").set_index("signal_id")
    util = np.nanmean(flow[:, peak], 1) / cap
    sigdel = np.zeros(len(p.segs))
    for i, sid in enumerate(net.signal_id):                       # Webster uniform delay (s/veh) with the segment's own peak utilisation
        if isinstance(sid, str) and sid in sig.index:
            c_, g = float(sig.at[sid, "cycle_s"]), float(sig.at[sid, "green_ratio"])
            sigdel[i] = 0.5 * c_ * (1 - g) ** 2 / (1 - min(util[i], 0.95) * g)
    inc = data.incidents("train").segment_id.value_counts().reindex(p.segs).fillna(0).to_numpy()
    deg = net.groupby("source_node").size().reindex(net.target_node).to_numpy()
    control = ((net.signal_id.notna().to_numpy()) | (deg >= 3)).astype(float)
    z = pd.DataFrame(json.loads(detection.BOTTLENECK_PATH.read_text())["segments"]).set_index("segment_id").robust_z.reindex(p.segs)   # independent peak-throughput-ceiling detector
    df = pd.DataFrame({
        "length_km": length, "lanes": net.lanes.to_numpy(), "road_class": net.road_class.to_numpy(), "free_flow_kmh": ff, "capacity_vph": cap,
        "signalized": net.signal_id.notna().to_numpy(), "peak_flow_vph": np.nanmean(flow[:, peak], 1), "utilization": util,
        "peak_speed_kmh": sr_peak * ff, "speed_reduction": np.clip(1 - sr_peak / sr_off, 0, 1),
        "delay_min_per_km": np.nanmean(delay[:, peak], 1) / length,
        "persistence": np.nanmean(np.where(np.isnan(speed[:, peak]), np.nan, (speed[:, peak] / ff[:, None]) < h["congested_speed_ratio"]), 1),
        "growth_pct": growth, "signal_delay_s": sigdel, "control_points_per_km": control / length, "incidents": inc,
        "incidents_per_km": inc / length, "ceiling_raw": -z.to_numpy(), "history_days": len(dm),
    }, index=pd.Index(p.segs, name="segment_id"))
    df["junction_signal_raw"] = df.control_points_per_km / df.control_points_per_km.max() * 50 + np.clip(df.signal_delay_s / 60, 0, 1) * 50
    return df


@lru_cache(maxsize=1)
def segment_stats() -> pd.DataFrame:
    """Precomputed once (scripts/build_corridor_features.py) and cached as parquet: never computed per request unless missing."""
    if STATS_PATH.exists():
        return pd.read_parquet(STATS_PATH)
    df = _build_stats()
    STATS_PATH.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(STATS_PATH)
    return df


@lru_cache(maxsize=1)
def _norm() -> dict:
    """Network-wide 5th/95th percentile per metric: robust min-max normalisation to 0-100."""
    s = segment_stats()
    return {c: (float(s[c].quantile(.05)), float(s[c].quantile(.95))) for c in
            ("peak_flow_vph", "delay_min_per_km", "incidents_per_km", "growth_pct", "junction_signal_raw", "speed_reduction", "ceiling_raw")}


def norm(x, col):
    if x is None or not np.isfinite(x):
        return None
    lo, hi = _norm()[col]
    return float(np.clip((x - lo) / (hi - lo), 0, 1) * 100) if hi > lo else 0.0


def wmean(comps: dict, weights: dict):
    """Weighted mean over the components that have data. Returns (score | None, weights actually used, components dropped)."""
    use = {k: w for k, w in weights.items() if comps.get(k) is not None}
    if not use:
        return None, {}, list(weights)
    tot = sum(use.values())
    return float(sum(comps[k] * w for k, w in use.items()) / tot), {k: round(w / tot, 3) for k, w in use.items()}, [k for k in weights if k not in use]


def level_of(score) -> str:
    if score is None:
        return "NO_DATA"
    lv = cfg()["bottleneck_levels"]
    return "CRITICAL" if score >= lv["CRITICAL"] else "HIGH" if score >= lv["HIGH"] else "MODERATE" if score >= lv["MODERATE"] else "LOW"


def congestion_components(r, sr_now=None) -> dict:
    """Standardised 0-100 features of one matched grid segment (historical peak hours; `current` from the replay when available)."""
    return {"utilization": float(np.clip(r.utilization, 0, 1) * 100), "speed_reduction": norm(r.speed_reduction, "speed_reduction"),
            "delay": norm(r.delay_min_per_km, "delay_min_per_km"), "junction_signal": norm(r.junction_signal_raw, "junction_signal_raw"),
            "persistence": float(r.persistence * 100) if np.isfinite(r.persistence) else None,
            "current": None if sr_now is None else float(np.clip((1 - sr_now) / 0.5, 0, 1) * 100),   # sr 0.5 (the HEAVY threshold) -> 100
            "throughput_ceiling": norm(r.ceiling_raw, "ceiling_raw")}


# ------------------------------------------------------------------ route -> stretches
def _polyline(route: dict):
    latlon = np.array([[c[1], c[0]] for c in route["coords"]], float)
    xy = UT.project(latlon)
    cum = np.concatenate([[0.0], np.cumsum(np.linalg.norm(np.diff(xy, axis=0), axis=1))])
    return latlon, xy, cum


def _at(latlon, cum, d):
    return [float(np.interp(d, cum, latlon[:, 0])), float(np.interp(d, cum, latlon[:, 1]))]


def segment_route(route: dict) -> list[dict]:
    """Cut the real road geometry into equal stretches of ~segment_length_m (never a straight line between the two points)."""
    latlon, xy, cum = _polyline(route)
    total = float(cum[-1])
    if total < 20:
        raise CorridorError("Origin and destination are too close together to analyse a corridor.")
    n = int(min(cfg()["max_segments"], max(1, round(total / cfg()["segment_length_m"]))))
    edges = np.linspace(0, total, n + 1)
    out = []
    for i in range(n):
        d0, d1 = edges[i], edges[i + 1]
        inner = (cum > d0) & (cum < d1)
        coords = [_at(latlon, cum, d0)] + latlon[inner].tolist() + [_at(latlon, cum, d1)]
        out.append({"index": i, "segment_id": f"S{i + 1:02d}", "start_m": float(d0), "end_m": float(d1), "length_m": float(d1 - d0),
                    "start": coords[0], "end": coords[-1], "coords": [[round(a, 5), round(b, 5)] for a, b in coords]})
    return out


def _road_name(route: dict, d0: float, d1: float):
    """Name covering most of [d0, d1] metres from the provider's steps; None (unavailable) when it gave none."""
    best, cover = None, 0.0
    for r in route.get("roads", []):
        o = min(d1, r["end_m"]) - max(d0, r["start_m"])
        if o > cover:
            best, cover = r["name"], o
    return best


def match_to_grid(stretches: list[dict]) -> list[tuple[str | None, float | None]]:
    """(grid segment id, distance m) per stretch: nearest simulated segment within match_radius_m, preferring the one that runs the same way."""
    segs, _, _, A, B, _ = UT._segments()
    mids = UT.project(np.array([[(s["start"][0] + s["end"][0]) / 2, (s["start"][1] + s["end"][1]) / 2] for s in stretches]))
    dirs = UT.project(np.array([s["end"] for s in stretches])) - UT.project(np.array([s["start"] for s in stretches]))
    dist = UT.dist_to_segments(mids, A, B)
    sd = B - A
    out = []
    for k in range(len(stretches)):
        dmin = dist[k].min()
        if dmin > cfg()["match_radius_m"]:
            out.append((None, None))
            continue
        near = np.nonzero(dist[k] <= dmin + 40)[0]
        cos = (sd[near] @ dirs[k]) / (np.linalg.norm(sd[near], axis=1) * np.linalg.norm(dirs[k]) + 1e-9)
        j = int(near[np.argmax(cos)])
        out.append((segs[j], float(dist[k][j])))
    return out


# ------------------------------------------------------------------ analysis
FIELD_SOURCES = {"road_name": "routing provider", "road_type": "matched simulation network", "lane_count": "matched simulation network",
                 "junction_count": "routing provider (intersections with 3+ approaches)", "signal_count": "matched simulation network (estimated)",
                 "traffic_volume": "historical peak-hour mean (simulated dataset)", "average_speed": "historical peak-hour mean (simulated dataset)",
                 "historical_congestion": "share of peak slots below 70% of free-flow speed", "accident_count": "historical incident records on the matched segment (training period)"}
UNAVAILABLE = {"speed_limit": "data_required", "road_width": "data_required", "pedestrian_activity": "data_required", "public_transport_activity": "data_required"}


def build_segments(route, stretches, matches, snap, stats) -> list[dict]:
    net = data.network().set_index("segment_id")
    junc = np.array(route.get("junctions", []), float)
    jxy = UT.project(junc) if len(junc) else None
    latlon, xy, cum = _polyline(route)
    have_junctions = bool(route.get("roads") or route.get("junctions"))
    out = []
    for s, (gid, gdist) in zip(stretches, matches):
        n_j = None
        if have_junctions:
            mid = UT.project(np.array([_at(latlon, cum, (s["start_m"] + s["end_m"]) / 2)]))[0]
            n_j = 0 if jxy is None else int((np.linalg.norm(jxy - mid, axis=1) <= s["length_m"] / 2).sum())
        rec = {**{k: s[k] for k in ("index", "segment_id", "length_m", "start", "end", "coords")},
               "start_lat": s["start"][0], "start_lng": s["start"][1], "end_lat": s["end"][0], "end_lng": s["end"][1],
               "road_name": _road_name(route, s["start_m"], s["end_m"]), "junction_count": n_j,
               "start_m": round(s["start_m"]), "end_m": round(s["end_m"]), "grid_segment_id": gid, "match_distance_m": None if gdist is None else round(gdist),
               "unavailable": dict(UNAVAILABLE), "sources": FIELD_SOURCES}
        if rec["road_name"] is None:
            rec["unavailable"]["road_name"] = "unavailable (routing provider gave no road names)"
        if n_j is None:
            rec["unavailable"]["junction_count"] = "unavailable (routing provider gave no intersections)"
        if gid is None:
            rec |= {"data_status": "NO_DATA", "bottleneck_level": "NO_DATA", "bottleneck_score": None, "congestion_index": None, "features": None}
            out.append(rec)
            continue
        r = stats.loc[gid]
        sr_now, cur = None, None
        if snap is not None:
            row = snap.row(gid)
            sr_now = row["speed_ratio"]
            cur = {"source": "simulation replay", "time": str(snap.t), "speed_kmh": row["speed_kmh"], "state": row["state"], "delay_min": row["delay_min"],
                   "forecast": user_explain._forecast(snap, gid)}
        comps = congestion_components(r, sr_now)
        cong, cw, _ = wmean(comps, cfg()["congestion_weights"])
        bott, bw, dropped = wmean(comps, cfg()["bottleneck_weights"])
        km = s["length_m"] / 1000
        rec |= {
            "data_status": "MATCHED", "road_type": r.road_class, "lane_count": int(r.lanes), "free_flow_speed_kmh": float(r.free_flow_kmh),
            "signal_count": round(float(r.signalized) * km / r.length_km, 2), "signal_delay_s": round(float(r.signal_delay_s), 1),
            "traffic_volume": round(float(r.peak_flow_vph)), "road_capacity": round(float(r.capacity_vph)), "capacity_utilization_pct": round(float(r.utilization) * 100),
            "average_speed": round(float(r.peak_speed_kmh), 1), "travel_time_min": round(km / max(float(r.peak_speed_kmh), 1) * 60, 2),
            "delay_min": round(float(r.delay_min_per_km) * km, 2), "historical_congestion_pct": round(float(r.persistence) * 100) if np.isfinite(r.persistence) else None,
            "accident_count": int(r.incidents), "historical_growth_pct": None if not np.isfinite(r.growth_pct) else round(float(r.growth_pct), 1),
            "current": cur, "congestion_index": round(cong), "bottleneck_score": round(bott), "bottleneck_level": level_of(bott),
            "features": {k: (None if v is None else round(v, 1)) for k, v in comps.items()},
            "score_weights": {"congestion": cw, "bottleneck": bw}, "_raw": r, "_dropped": dropped,
        }
        out.append(rec)
    return out


def find_candidates(segments: list[dict], total_m: float) -> list[dict]:
    """Group neighbouring high-severity segments into zones, then choose the best start/end around each (see the algorithm doc)."""
    c = cfg()["candidate"]
    T = cfg()["bottleneck_levels"]
    seed_min = cfg()["bottleneck_levels"][c["seed_level"]]
    b = [s["bottleneck_score"] if s["bottleneck_score"] is not None else -1 for s in segments]
    seeds = [i for i, v in enumerate(b) if v >= seed_min]
    zones, cur = [], None
    for i in seeds:                                                             # merge seeds separated by <= merge_gap segments that still have data
        if cur and i - cur[1] - 1 <= c["merge_gap_segments"] and all(b[k] >= 0 for k in range(cur[1] + 1, i)):
            cur[1] = i
        else:
            cur = [i, i]
            zones.append(cur)
    out = []
    n = len(segments)
    for a0, b0 in zones:
        lo, hi = max(0, a0 - c["extend_segments"]), min(n - 1, b0 + c["extend_segments"])
        left = [i for i in seeds if a0 <= i <= b0]
        while left:                                                         # one candidate per pass: best extent <= max_length_m that covers a remaining seed
            best = None
            for a in range(lo, max(left) + 1):
                for e in range(min(left), hi + 1):
                    if e < a or any(b[k] < 0 for k in range(a, e + 1)) or segments[e]["end_m"] - segments[a]["start_m"] > c["max_length_m"]:
                        continue
                    if not any(a <= i <= e for i in left):
                        continue
                    j = sum(b[k] - T["MODERATE"] for k in range(a, e + 1))     # maximise the summed excess over the MODERATE threshold
                    if best is None or j > best[0]:
                        best = (j, a, e)
            if best is None:                                                # a single seed longer than max_length_m cannot happen (segments are ~300 m)
                break
            _, a, e = best
            while segments[e]["end_m"] - segments[a]["start_m"] < c["min_length_m"]:   # a flyover has a minimum useful length: extend toward the worse neighbour
                l_ = b[a - 1] if a > 0 else -2
                r_ = b[e + 1] if e + 1 < n else -2
                if (l_ < 0 and r_ < 0) or segments[e]["end_m"] - segments[a]["start_m"] >= c["max_length_m"]:
                    break
                a, e = (a - 1, e) if l_ >= r_ else (a, e + 1)
            inside = [i for i in left if a <= i <= e]
            left = [i for i in left if i not in inside]
            out.append({"first": a, "last": e, "seeds": inside, "start_m": max(0.0, segments[a]["start_m"] - c["ramp_margin_m"]),
                        "end_m": min(total_m, segments[e]["end_m"] + c["ramp_margin_m"])})
    return out


def _point_at(segments, d):
    for s in segments:
        if s["start_m"] <= d <= s["end_m"] or s is segments[-1]:
            f = 0 if s["end_m"] == s["start_m"] else (d - s["start_m"]) / (s["end_m"] - s["start_m"])
            pts = np.array(s["coords"])
            cum = np.concatenate([[0], np.cumsum(np.linalg.norm(np.diff(UT.project(pts), axis=0), axis=1))])
            return [round(float(np.interp(f * cum[-1], cum, pts[:, 0])), 6), round(float(np.interp(f * cum[-1], cum, pts[:, 1])), 6)]


def _wavg(segs, key):
    v = [(s[key], s["length_m"]) for s in segs if s.get(key) is not None]
    return None if not v else float(sum(a * w for a, w in v) / sum(w for _, w in v))


def suitability(covered: list[dict]) -> dict:
    """Flyover suitability 0-100 (an analytical indicator, NOT an engineering approval) with its breakdown."""
    m = [s for s in covered if s["data_status"] == "MATCHED"]
    raw = [s["_raw"] for s in m]
    w = [s["length_m"] for s in m]
    lw = lambda f: None if not m else float(sum(f(r) * wi for r, wi in zip(raw, w)) / sum(w))
    comps = {
        "traffic_pressure": lw(lambda r: norm(r.peak_flow_vph, "peak_flow_vph")),
        "junction_bottleneck": _wavg(m, "bottleneck_score"),
        "delay": lw(lambda r: norm(r.delay_min_per_km, "delay_min_per_km")),
        "capacity_utilization": lw(lambda r: float(np.clip(r.utilization, 0, 1) * 100)),
        "historical_growth": lw(lambda r: norm(r.growth_pct, "growth_pct") or 0.0) if any(np.isfinite(r.growth_pct) for r in raw) else None,
        "accident_risk": lw(lambda r: norm(r.incidents_per_km, "incidents_per_km")),
    }
    score, used, dropped = wmean(comps, cfg()["suitability_weights"])
    bands = cfg()["suitability_bands"]
    return {"score": None if score is None else round(score), "breakdown": {k: None if v is None else round(v) for k, v in comps.items()},
            "weights_used": used, "excluded": {**{k: "no data" for k in dropped},
                                                 "feasibility": "data_required (no land-use, right-of-way, utilities or geotechnical data)",
                                                 "pedestrian": "data_required", "public_transport": "data_required"},
            "screening_level": None if score is None else "strong" if score >= bands["strong"] else "moderate" if score >= bands["moderate"] else "weak"}


def explain_candidate(covered: list[dict], suit: dict) -> list[dict]:
    """Reasons built from the actual feature values of the covered segments (each with the numbers it rests on)."""
    m = [s for s in covered if s["data_status"] == "MATCHED"]
    r = []
    util = _wavg(m, "capacity_utilization_pct")
    vol, cap = _wavg(m, "traffic_volume"), _wavg(m, "road_capacity")
    if util is not None and util >= 70:
        r.append({"factor": "capacity", "text": f"Peak-hour volume averages about {round(vol):,} veh/h against roughly {round(cap):,} veh/h of capacity ({round(util)}% utilisation)."})
    slow = [s for s in m if s["average_speed"] and s["free_flow_speed_kmh"] and s["average_speed"] < 0.8 * s["free_flow_speed_kmh"]]
    if slow:
        r.append({"factor": "speed", "text": f"Average peak speed falls to {min(s['average_speed'] for s in slow):.0f} km/h on {len(slow)} of {len(m)} segments (free-flow is about {slow[0]['free_flow_speed_kmh']:.0f} km/h)."})
    sig = sum(s["signal_count"] for s in m)
    hi_sig = [s for s in m if s["signal_delay_s"] and s["signal_delay_s"] >= 20]
    if hi_sig:
        r.append({"factor": "signals", "text": f"Signalised control adds an estimated {max(s['signal_delay_s'] for s in hi_sig):.0f} s of delay per vehicle on {len(hi_sig)} segment(s) in the zone."})
    js = [s["junction_count"] for s in covered if s["junction_count"]]
    if js and sum(js) >= 3:
        r.append({"factor": "junctions", "text": f"{sum(js)} junctions (3+ approaches) lie within the candidate length."})
    pers = _wavg(m, "historical_congestion_pct")
    if pers is not None and pers >= 30:
        r.append({"factor": "persistence", "text": f"Peak-hour speeds are below 70% of free-flow in {round(pers)}% of weekday peak intervals (training period)."})
    g = _wavg(m, "historical_growth_pct")
    if g is not None and g >= 3:
        r.append({"factor": "growth", "text": f"Peak volume rose about {g:.1f}% between the first and last days of the history window."})
    inc = sum(s["accident_count"] for s in {s["grid_segment_id"]: s for s in m}.values())
    if inc:
        r.append({"factor": "incidents", "text": f"{inc} recorded incident(s) on the matched segments during the training period."})
    n_hi = sum(1 for s in m if s["bottleneck_level"] in ("HIGH", "CRITICAL"))
    r.append({"factor": "concentration", "text": f"{n_hi} of the {len(covered)} segments in this zone are HIGH or CRITICAL bottlenecks."})
    return r


def alternatives(covered: list[dict], planning: list[dict], route_alternatives: int) -> list[dict]:
    """Options to weigh before a flyover. Each carries whether the data supports it, from the same features."""
    m = [s for s in covered if s["data_status"] == "MATCHED"]
    sig_segs = [s for s in m if s["signal_count"] > 0]
    juncs = sum(s["junction_count"] or 0 for s in covered)
    util = _wavg(m, "capacity_utilization_pct") or 0
    lanes = min((s["lane_count"] for s in m), default=None)
    late = max((s["signal_delay_s"] for s in m), default=0)
    def opt(name, supported, basis, note=None):
        return {"name": name, "supported_by_data": supported, "basis": basis, "note": note}
    out = [
        opt("Signal optimization", bool(sig_segs) and late >= 10, f"{len(sig_segs)} signalised segment(s) in the zone, estimated signal delay up to {late:.0f} s/veh." if sig_segs else "No signalised segment in the zone."),
        opt("Intelligent signal control", len(sig_segs) >= 2, f"{len(sig_segs)} signalised segments could be coordinated." if sig_segs else "Needs 2+ signals."),
        opt("Junction redesign", juncs >= 3, f"{juncs} junctions with 3+ approaches in the zone." if juncs else "No junction data for this zone."),
        opt("Lane optimization", lanes is not None and lanes <= 3 and util >= 80, f"Minimum {lanes} lanes with {round(util)}% peak utilisation." if lanes is not None else "No lane data."),
        opt("Bus priority", None, "Cannot be assessed: no public transport data.", "data_required"),
        opt("Traffic diversion", route_alternatives >= 2, f"{route_alternatives} alternative route(s) exist between the two points." if route_alternatives >= 2 else "The routing provider found no alternative route."),
        opt("Underpass", None, "A grade-separation alternative to a flyover; needs the same feasibility studies.", "data_required"),
    ]
    return {"options": out, "dataset_planning_candidates": planning}


def data_confidence(segments, stats, snap, provider) -> dict:
    """A data-quality indicator (0-100), NOT a probability and NOT engineering certainty."""
    w = cfg()["confidence_weights"]
    total = sum(s["length_m"] for s in segments)
    cov = sum(s["length_m"] for s in segments if s["data_status"] == "MATCHED") / total
    days = int(stats.history_days.iloc[0]) if len(stats) else 0
    have = cfg()["required_fields"]
    got = sum(1 for f in have if segments and any(s.get(f) is not None for s in segments if s["data_status"] == "MATCHED"))
    comps = {"coverage": cov, "history": min(1.0, days / cfg()["history"]["min_days_full_confidence"]),
             "freshness": 1.0 if provider.live else 0.3,                                # replayed/simulated data is never "fresh"
             "model_validation": 1.0 if METRICS_PATH.exists() and engine_models() else 0.0, "field_completeness": got / len(have)}
    score = sum(comps[k] * w[k] for k in w) / sum(w.values())
    return {"score": round(score * 100), "components": {k: round(v * 100) for k, v in comps.items()}, "weights": w,
            "meaning": "Data-quality indicator combining coverage, history length, freshness, validation and field completeness. It is not a probability and not engineering certainty.",
            "missing_fields": [f for f in have if not (segments and any(s.get(f) is not None for s in segments if s["data_status"] == "MATCHED"))]}


def engine_models() -> bool:
    from . import forecasting
    return forecasting.models_available()


_CACHE: "OrderedDict[str, dict]" = OrderedDict()      # ponytail: in-process, 50 analyses. Upgrade: persist to the DB (PostGIS) when analyses must survive restarts


def corridor_id(o, d, when) -> str:
    return hashlib.sha1(f"{o[0]:.4f},{o[1]:.4f},{d[0]:.4f},{d[1]:.4f},{when}".encode()).hexdigest()[:12]


def get_cached(cid: str) -> dict | None:
    return _CACHE.get(cid)


def analyze(origin: tuple[float, float], dest: tuple[float, float], when: str | None = None, provider: TrafficDataProvider | None = None) -> dict:
    provider = provider or get_provider()
    when = when or str(pd.Timestamp(C.DEMO_TIME))
    cid = corridor_id(origin, dest, when)
    if cid in _CACHE:
        return _CACHE[cid]
    stages, t0 = [], time.perf_counter()
    def mark(name):
        nonlocal t0
        stages.append({"stage": name, "ms": round((time.perf_counter() - t0) * 1000)}); t0 = time.perf_counter()

    try:
        routes = providers.route_options(origin, dest)
    except providers.ProviderError as e:
        raise CorridorError(e.message, e.status)
    if not routes or len(routes[0]["coords"]) < 2:
        raise CorridorError("No route could be found between these points.", 404)
    route = routes[0]
    mark("Generating route")
    stats = provider.segment_stats()
    snap = provider.current(when)
    mark("Loading traffic data")
    stretches = segment_route(route)
    matches = match_to_grid(stretches)
    mark("Segmenting corridor")
    segments = build_segments(route, stretches, matches, snap, stats)
    mark("Running traffic analysis")
    total = stretches[-1]["end_m"]
    zones = find_candidates(segments, total)
    mark("Detecting bottlenecks")
    candidates = []
    for z in zones:
        cov = segments[z["first"]:z["last"] + 1]
        suit = suitability(cov)
        grid_ids = {s["grid_segment_id"] for s in cov if s["grid_segment_id"]}
        pc = data.table("planning_candidates")
        planning = [{"candidate_id": r.candidate_id, "segment": r.target_segment, "type": r.intervention_type, "capacity_delta_vph": int(r.capacity_delta_vph),
                     "cost_index": int(r.cost_index), "feasibility_band": r.feasibility_band} for r in pc[pc.target_segment.isin(grid_ids)].itertuples()]
        gs = sorted({s["road_name"] for s in cov if s["road_name"] and any(w in s["road_name"].lower() for w in ("flyover", "underpass", "bridge", "overpass"))})
        candidates.append({
            "existing_grade_separation": gs, "name": f"Zone {chr(65 + len(candidates))}", "start": _point_at(segments, z["start_m"]), "end": _point_at(segments, z["end_m"]),
            "start_m": round(z["start_m"]), "end_m": round(z["end_m"]), "estimated_length_m": round(z["end_m"] - z["start_m"]),
            "affected_segments": [s["segment_id"] for s in cov], "bottleneck_segments": [segments[i]["segment_id"] for i in z["seeds"]],
            "suitability": suit, "explanation": explain_candidate(cov, suit), "alternatives": alternatives(cov, planning, len(routes)),
            "reason_for_selection": f"Segments {cov[0]['segment_id']}-{cov[-1]['segment_id']} hold {len(z['seeds'])} HIGH/CRITICAL bottleneck segment(s); the extent maximises the summed bottleneck excess over the MODERATE threshold, "
                                    f"then adds a {cfg()['candidate']['ramp_margin_m']} m approach on each side.",
            "coords": [c for s in cov for c in s["coords"]],
        })
    candidates.sort(key=lambda c: -(c["suitability"]["score"] or 0))
    candidates = candidates[:cfg()["candidate"]["max_candidates"]]
    for i, c in enumerate(candidates):
        c["name"] = f"Zone {chr(65 + i)}"
    mark("Evaluating candidate zones")
    conf = data_confidence(segments, stats, snap, provider)
    matched = [s for s in segments if s["data_status"] == "MATCHED"]
    counts = {lv: sum(1 for s in segments if s["bottleneck_level"] == lv) for lv in LEVELS + ["NO_DATA"]}
    cong = _wavg(matched, "congestion_index")
    res = {
        "id": cid, "mode": "simulation", "data_notice": "SIMULATION DATA: traffic is the organizer dataset (historical peak hours + a replayed moment), matched approximately to real roads. Not live traffic.",
        "provider": {"traffic": provider.name, "live": provider.live, "routing": C.ROUTING_PROVIDER, "as_of": str(snap.t) if snap else None},
        "corridor": {"origin": {"lat": origin[0], "lng": origin[1]}, "destination": {"lat": dest[0], "lng": dest[1]}, "distance_km": round(total / 1000, 2),
                     "free_flow_time_min": round(route["duration_min"], 1), "estimated_travel_time_min": round(route["duration_min"] + sum(s["delay_min"] for s in matched), 1),
                     "segments": len(segments), "junctions": route.get("junctions", []), "coords": route["coords"] and [[c[1], c[0]] for c in route["coords"]],
                     "congestion_index": None if cong is None else round(cong), "traffic_status": level_of(cong) if cong is not None else "NO_DATA",
                     "coverage_pct": round(100 * sum(s["length_m"] for s in matched) / total), "alternative_routes": len(routes) - 1},
        "segments": [{k: v for k, v in s.items() if not k.startswith("_")} for s in segments],
        "bottlenecks": {"counts": counts, "segments": [s["segment_id"] for s in segments if s["bottleneck_level"] in ("HIGH", "CRITICAL")]},
        "field_sources": FIELD_SOURCES, "candidates": candidates,
        "recommended_analysis": candidates[0] if candidates else None,
        "confidence": conf, "stages": stages,
        "disclaimer": {"layers": ["AI/Data analysis (this screen)", "Engineering feasibility (not assessed)", "Official infrastructure decision (not made)"],
                       "text": "This is an AI/data-driven planning aid. It does not constitute engineering feasibility or approval."},
    }
    mark("Generating result")
    res["stages"] = stages
    _CACHE[cid] = res
    while len(_CACHE) > 50:
        _CACHE.popitem(last=False)
    return res


def predict(segment_ids: list[str], when: str | None = None) -> dict:
    """Near-term traffic for grid segments from the EXISTING LightGBM forecaster (+15/+30/+60 min), plus the historical peak."""
    snap = get_provider().current(when or str(pd.Timestamp(C.DEMO_TIME)))
    stats = segment_stats()
    if snap is None or snap.forecast is None:
        raise CorridorError("The traffic forecasting models are not available.", 503)
    out = []
    for sid in segment_ids:
        if sid not in snap.idx:
            raise CorridorError(f"Unknown segment {sid}.", 422)
        r = stats.loc[sid]
        out.append({"segment_id": sid, "as_of": str(snap.t), "source": "simulation replay + LightGBM forecaster", "current_state": str(snap.state[snap.idx[sid]]),
                    "forecast": user_explain._forecast(snap, sid), "historical_peak": {"flow_vph": round(float(r.peak_flow_vph)), "speed_kmh": round(float(r.peak_speed_kmh), 1),
                                                                                       "utilization_pct": round(float(r.utilization) * 100)}})
    return {"predictions": out, "note": "Forecasts come from a model trained on the simulated dataset; they are not real-time traffic predictions."}


def evaluate() -> dict:
    """Benchmark of the segment bottleneck score against the organizer's structural_bottleneck flag (also reported without the throughput-ceiling component, which comes from the independent bottleneck detector)."""
    from scipy.stats import rankdata
    s = segment_stats()
    def auc_of(w):
        score = np.array([wmean(congestion_components(r), w)[0] or 0 for r in s.itertuples()])
        return score, float((rankdata(score)[y].sum() - y.sum() * (y.sum() + 1) / 2) / (y.sum() * (~y).sum()))
    y = data.network().set_index("segment_id").structural_bottleneck.reindex(s.index).to_numpy().astype(bool)
    full = cfg()["bottleneck_weights"]
    score, auc = auc_of(full)
    _, auc_wo = auc_of({k: v for k, v in full.items() if k != "throughput_ceiling"})
    top = np.argsort(-score)[: int(y.sum())]
    res = {"reference": "network.structural_bottleneck (organizer flag): a benchmark, not ground truth for flyover need",
           "segments": int(len(s)), "reference_positives": int(y.sum()), "roc_auc": round(auc, 3), "roc_auc_without_throughput_ceiling": round(auc_wo, 3),
           "precision_at_k": round(float(y[top].mean()), 3), "k": int(y.sum()),
           "history": {"days": int(s.history_days.iloc[0]), "period": f"{C.TRAIN_START} to {C.TRAIN_END} (weekday peak hours)"},
           "traffic_forecast": "reuses the existing LightGBM forecaster; see forecast_metrics.json",
           "note": "There is no labelled flyover dataset: suitability and bottleneck scores are transparent weighted indicators and cannot be validated as accuracy."}
    METRICS_PATH.write_text(json.dumps(res, indent=2))
    return res


def model_info() -> dict:
    fm = C.METRICS / "forecast_metrics.json"
    ev = json.loads(METRICS_PATH.read_text()) if METRICS_PATH.exists() else None
    fc = json.loads(fm.read_text()) if fm.exists() else None
    return {"scoring": "transparent weighted indicators (corridor_config.json)", "config": cfg(), "evaluation": ev,
            "forecaster": {"model": "LightGBM quantile regressors (existing FlowSense forecaster), one per metric and horizon", "version": C.MODEL_VERSION,
                           "why_not_deep_learning": "1.9M 5-minute rows but only 15 training days and 436 segments: gradient-boosted trees on lag/seasonal features are more reliable than an LSTM here.",
                           "validation": None if fc is None else {"split": fc.get("split"), "speed_15m": fc["results"].get("speed_15m", {}).get("model")}}}
