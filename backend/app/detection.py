"""Congestion state, anomaly detection, incident diagnosis and the independent recurring-bottleneck detector.

Runtime inputs are traffic + context at <= t only. Incident / roadwork labels are used offline:
to clean the baseline, to calibrate the threshold, and for evaluation. `structural_bottleneck`
and `peak_capacity_factor` (a verified proxy for it) are never read here.
"""
import json

import numpy as np
import pandas as pd

from . import config as C
from .data import Panel, context, network, active_roadworks

BASELINE_PATH = C.MODELS / "detector_baseline.npz"
DETECTOR_META = C.META / "detector.json"
BOTTLENECK_PATH = C.META / "recurring_bottlenecks.json"
TYPE_CENTROIDS = C.META / "incident_type_centroids.json"


def daytype(times: pd.DatetimeIndex) -> np.ndarray:
    hol = context().reindex(times).holiday_flag.fillna(0).to_numpy()
    return ((times.dayofweek >= 5) | (hol > 0)).astype(int)


def label_mask(p: Panel, events: pd.DataFrame, seg_col: str = "segment_id") -> np.ndarray:
    """(S, T) True where a slot lies inside [start_time, end_time] of an event on that segment."""
    idx = {s: i for i, s in enumerate(p.segs)}
    m = np.zeros((len(p.segs), len(p.times)), bool)
    for r in events.itertuples():
        cols = (p.times >= pd.Timestamp(r.start_time).ceil("5min")) & (p.times <= r.end_time)
        m[idx[getattr(r, seg_col)], cols] = True
    return m


# ---------------------------------------------------------------- baseline
def fit_baseline(p: Panel, exclude: np.ndarray) -> dict:
    """Median / MAD of speed ratio and flow ratio per segment x hour x daytype, from training data
    with labelled incident and roadwork windows removed (offline use of labels = calibration)."""
    net = network()
    sr = p.v["speed_kmh"] / net.free_flow_speed_kmh.to_numpy("float32")[:, None]
    fr = p.v["flow_vph"] / net.capacity_vph.to_numpy("float32")[:, None]
    sr, fr = np.where(exclude, np.nan, sr), np.where(exclude, np.nan, fr)
    hours, dts = p.times.hour.to_numpy(), daytype(p.times)
    S = len(p.segs)
    out = {k: np.full((S, 24, 2), np.nan, "float32") for k in ("sr_med", "sr_mad", "fr_med", "fr_mad")}
    for h in range(24):
        for d in (0, 1):
            cols = (hours == h) & (dts == d)
            if not cols.any():
                continue
            for name, a in (("sr", sr), ("fr", fr)):
                blk = a[:, cols]
                med = np.nanmedian(blk, axis=1)
                out[f"{name}_med"][:, h, d] = med
                out[f"{name}_mad"][:, h, d] = np.nanmedian(np.abs(blk - med[:, None]), axis=1)
    np.savez(BASELINE_PATH, **out)
    return out


def load_baseline() -> dict:
    z = np.load(BASELINE_PATH)
    return {k: z[k] for k in z.files}


def components(p: Panel, base: dict) -> dict:
    """Per-slot anomaly components for the whole panel (vectorised)."""
    net = network()
    sr = p.v["speed_kmh"] / net.free_flow_speed_kmh.to_numpy("float32")[:, None]
    fr = p.v["flow_vph"] / net.capacity_vph.to_numpy("float32")[:, None]
    h, d = p.times.hour.to_numpy(), daytype(p.times)
    b_sr, b_srm = base["sr_med"][:, h, d], base["sr_mad"][:, h, d]
    b_fr, b_frm = base["fr_med"][:, h, d], base["fr_mad"][:, h, d]
    drop = b_sr - sr
    # network-wide shift at t (weather / events move every segment; an incident moves one) [DESIGN]
    net_shift = np.clip(np.nanmedian(drop, axis=0), 0, None)
    z_s = (drop - net_shift[None, :]) / np.maximum(1.4826 * b_srm, C.SR_MAD_FLOOR)
    z_f = (b_fr - fr) / np.maximum(1.4826 * b_frm, C.FLOW_MAD_FLOOR)
    # local excess drop: speed-ratio drop beyond the network-wide shift (speed-ratio units)
    d = np.nan_to_num(drop - net_shift[None, :], nan=0.0)
    up, dn = neighbor_lists()
    nb_max = np.stack([d[js].max(0) if len(js) else np.zeros(d.shape[1]) for js in up_down(up, dn)])
    return {"sr": sr, "fr": fr, "base_sr": b_sr, "base_fr": b_fr, "drop": drop, "net_shift": net_shift,
            "z_speed": z_s, "z_flow": z_f, "local_drop": d, "neighbor_max_drop": nb_max}


def neighbor_lists():
    from .graph import neighbors, seg_index
    idx, nb = seg_index(), neighbors()
    segs = list(idx)
    return [[idx[x] for x in nb[s]["up"]] for s in segs], [[idx[x] for x in nb[s]["down"]] for s in segs]


def up_down(up, dn):
    return [u + v for u, v in zip(up, dn)]


def score_variant(c: dict, variant: str) -> np.ndarray:
    """Candidate anomaly formulations (compared on train, best one used at runtime):
    - z_composite:  0.8 * robust-z(speed-ratio drop) + 0.2 * robust-z(flow drop)
    - drop:         local speed-ratio drop (network-wide shift removed)
    - drop_epicentre: `drop`, kept only where it is >= every 1-hop neighbor's drop. Spillback makes
                    neighbors slow down too; the epicentre rule points at the source segment."""
    if variant == "z_composite":
        w = C.ANOMALY_WEIGHTS
        s = w["speed"] * np.clip(c["z_speed"], 0, None) + w["flow"] * np.clip(c["z_flow"], 0, None)
    elif variant == "drop":
        s = c["local_drop"]
    else:
        s = np.where(c["local_drop"] >= c["neighbor_max_drop"], c["local_drop"], 0.0)
    return np.nan_to_num(s, nan=0.0)


VARIANTS = ("z_composite", "drop", "drop_epicentre")


def detector_meta() -> dict:
    return json.loads(DETECTOR_META.read_text())


def state_of(sr: np.ndarray) -> np.ndarray:
    out = np.full(sr.shape, "NO_DATA", dtype=object)
    for name, thr in reversed(C.STATE_THRESHOLDS):
        out[sr >= thr] = name
    return out


def confidence(score: np.ndarray, thr: float, quality: np.ndarray) -> np.ndarray:
    """Heuristic confidence: logistic distance from the calibrated threshold, scaled by data quality.
    Not a calibrated probability."""
    return (1 / (1 + np.exp(-(score - thr) / (0.25 * thr)))) * quality


def events_from_flags(flags: np.ndarray, score: np.ndarray, p: Panel) -> pd.DataFrame:
    """Contiguous flagged runs per segment -> event rows."""
    rows = []
    S, T = flags.shape
    padded = np.concatenate([np.zeros((S, 1), bool), flags, np.zeros((S, 1), bool)], axis=1).astype(int)
    d = np.diff(padded, axis=1)
    for s, st in zip(*np.nonzero(d == 1)):
        en = np.nonzero(d[s, st + 1:] == -1)[0][0] + st  # inclusive end index
        rows.append({"segment_id": p.segs[s], "start": p.times[st], "end": p.times[en],
                     "slots": int(en - st + 1), "peak_score": float(score[s, st:en + 1].max()),
                     "s": int(s), "st": int(st), "en": int(en)})
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- diagnosis
def diagnose(seg: str, si: int, ti: int, p: Panel, c: dict, score: np.ndarray, thr: float,
             recurring: set, t) -> dict:
    """Transparent rule-based cause attribution for one segment at time index ti."""
    ctx = context().reindex([p.times[ti]]).iloc[0]
    rain, event = float(ctx.rain_intensity), bool(ctx.event_level > 0)
    shift = float(c["net_shift"][ti])
    flagged = score[si, ti] >= thr
    sr = c["sr"][si, ti]
    congested = bool(np.isfinite(sr) and sr < C.STATE_THRESHOLDS[0][1])
    rw = active_roadworks(t)
    rw = rw[rw.segment_id == seg]
    hour = p.times[ti].hour
    reasons = []

    # onset: first flagged slot of the current run and the speed-ratio step at that moment
    run_start = ti
    while flagged and run_start > 0 and score[si, run_start - 1] >= thr:
        run_start -= 1
    onset_step = float(c["sr"][si, run_start - 1] - c["sr"][si, run_start]) if flagged and run_start > 0 else 0.0
    duration_min = (ti - run_start + 1) * C.STEP_MIN if flagged else 0

    if len(rw):
        cause = "ROADWORK_EFFECT"
        reasons.append(f"roadwork {rw.iloc[0].work_id} active ({rw.iloc[0].work_type}, closure {rw.iloc[0].closure_fraction:.0%})")
    elif flagged and onset_step >= 0.1:
        cause = "TRANSIENT_INCIDENT"
        reasons.append(f"abrupt onset: speed ratio fell {onset_step:.2f} in one 5-min step")
        reasons.append("deviation is local (network-wide shift removed)")
    elif flagged:
        cause = "UNKNOWN"
        reasons.append("anomalous vs. baseline but onset was gradual — cause not determinable from the data")
    elif c["local_drop"][si, ti] >= thr and c["neighbor_max_drop"][si, ti] > c["local_drop"][si, ti]:
        up, dn = neighbor_lists()
        js = up_down(up, dn)[si]
        src = max(js, key=lambda j: c["local_drop"][j, ti])
        cause = "PROPAGATED_CONGESTION"
        reasons.append(f"slowdown matches spillback from neighbor {p.segs[src]} "
                       f"(its drop {c['local_drop'][src, ti]:.2f} > this segment's {c['local_drop'][si, ti]:.2f})")
    elif congested and seg in recurring and hour in C.PEAK_HOURS:
        cause = "RECURRING_BOTTLENECK"
        reasons.append("segment independently detected as recurring peak-hour throughput ceiling; congestion is within its normal range")
    elif congested and (shift >= C.NETWORK_SHIFT_EFFECT or rain >= C.RAIN_EFFECT or event):
        cause = "WEATHER_EVENT_EFFECT"
        reasons.append(f"network-wide speed-ratio shift {shift:.3f}, rain {rain:.2f}, event {'present' if event else 'none'}")
    else:
        cause = "NORMAL_VARIATION"
        reasons.append("within the segment's normal range for this hour and day type")
    return {"cause": cause, "reasons": reasons, "onset_step": onset_step, "duration_min": duration_min,
            "run_start": str(p.times[run_start]) if flagged else None,
            "context": {"rain_intensity": rain, "event_present": event, "network_shift": shift,
                        "roadwork_active": bool(len(rw))}}


def type_likelihood(c: dict, si: int, run_start: int, ti: int, p: Panel) -> dict:
    """Nearest-centroid likelihood over incident types from train incident shapes. Weak by design:
    the data shows severity, not type, drives the signature. UNKNOWN when no type is clearly ahead."""
    cents = json.loads(TYPE_CENTROIDS.read_text())
    x = _shape_vector(c, si, run_start, ti)
    if x is None:
        return {"type": "UNKNOWN", "confidence": "LOW", "likelihoods": {}}
    sig = np.array(cents["pooled_std"])
    d2 = {k: float((((x - np.array(v)) / sig) ** 2).sum()) for k, v in cents["centroids"].items()}
    w = {k: np.exp(-0.5 * v) for k, v in d2.items()}
    tot = sum(w.values()) or 1
    lik = {k: round(v / tot, 3) for k, v in sorted(w.items(), key=lambda kv: -kv[1])}
    best, pbest = next(iter(lik.items()))
    if pbest < C.TYPE_MIN_LIKELIHOOD:
        return {"type": "UNKNOWN", "confidence": "LOW", "likelihoods": lik}
    return {"type": best, "confidence": "MEDIUM" if pbest >= 0.7 else "LOW", "likelihoods": lik}


def _shape_vector(c, si, st, en):
    sl = slice(st, en + 1)
    drop = np.nanmean(c["base_sr"][si, sl] - c["sr"][si, sl])
    fchg = np.nanmean(c["fr"][si, sl] / np.maximum(c["base_fr"][si, sl], 1e-3) - 1)
    if not np.isfinite(drop) or not np.isfinite(fchg):
        return None
    return np.array([drop, fchg])


def fit_type_centroids(p: Panel, c: dict, incidents: pd.DataFrame):
    idx = {s: i for i, s in enumerate(p.segs)}
    rows = []
    for r in incidents.itertuples():
        cols = np.nonzero((p.times >= pd.Timestamp(r.start_time).ceil("5min")) & (p.times <= r.end_time))[0]
        if len(cols):
            x = _shape_vector(c, idx[r.segment_id], cols[0], cols[-1])
            if x is not None:
                rows.append((r.incident_type, x))
    X = np.array([x for _, x in rows])
    cents = {t: np.mean([x for k, x in rows if k == t], axis=0).tolist() for t in sorted({k for k, _ in rows})}
    out = {"features": ["mean_speed_ratio_drop", "mean_flow_ratio_change"], "centroids": cents,
           "pooled_std": np.maximum(X.std(axis=0), 1e-3).tolist(),
           "counts": {t: sum(1 for k, _ in rows if k == t) for t in cents}}
    TYPE_CENTROIDS.write_text(json.dumps(out, indent=2))
    return out


# ---------------------------------------------------------------- recurring bottlenecks
def detect_recurring_bottlenecks(p: Panel, exclude: np.ndarray) -> dict:
    """Independent detector: a structural bottleneck is a segment whose throughput cannot rise at peak
    the way the rest of the network's does (a peak-hour capacity ceiling), repeatedly across days.

    Score = p99(flow/capacity at peak) / p99(flow/capacity off-peak), with labelled incident and
    roadwork windows excluded so transient events cannot explain it. Flag = robust z <= BOTTLENECK_Z
    across all segments (threshold fixed a priori, not tuned on the organizer flag)."""
    net = network()
    fr = p.v["flow_vph"] / net.capacity_vph.to_numpy("float32")[:, None]
    fr = np.where(exclude, np.nan, fr)
    peak = np.isin(p.times.hour, C.PEAK_HOURS)
    ratio = np.nanquantile(fr[:, peak], 0.99, axis=1) / np.nanquantile(fr[:, ~peak], 0.99, axis=1)
    med = np.nanmedian(ratio)
    mad = 1.4826 * np.nanmedian(np.abs(ratio - med))
    z = (ratio - med) / mad
    # recurrence: share of days on which the same segment's daily ratio is low vs. that day's network
    days = p.times.normalize().unique()
    low_days = np.zeros(len(p.segs))
    for d in days:
        dm = p.times.normalize() == d
        r = np.nanquantile(fr[:, dm & peak], 0.95, axis=1) / np.nanquantile(fr[:, dm & ~peak], 0.95, axis=1)
        m, md = np.nanmedian(r), 1.4826 * np.nanmedian(np.abs(r - np.nanmedian(r)))
        low_days += ((r - m) / md) <= -1.5
    rec = low_days / len(days)
    ci = np.where(exclude, np.nan, p.v["congestion_index"])
    rows = []
    for i, s in enumerate(p.segs):
        rows.append({"segment_id": s, "throughput_amplification": round(float(ratio[i]), 4),
                     "robust_z": round(float(z[i]), 2), "recurrence_days_frac": round(float(rec[i]), 3),
                     "peak_mean_congestion": round(float(np.nanmean(ci[i, peak])), 4),
                     "detected": bool(z[i] <= C.BOTTLENECK_Z)})
    out = {"method": "peak/off-peak p99 throughput amplification, robust z <= %.1f" % C.BOTTLENECK_Z,
           "network_median_amplification": round(float(med), 4), "segments": rows}
    BOTTLENECK_PATH.write_text(json.dumps(out, indent=2))
    return out


def recurring_set() -> set:
    try:
        return {r["segment_id"] for r in json.loads(BOTTLENECK_PATH.read_text())["segments"] if r["detected"]}
    except FileNotFoundError:
        return set()


def evidence(metric, value, baseline=None, threshold=None, unit="", source="traffic", note=None) -> dict:
    """Structured evidence item. Every UI explanation is rendered from these, never free text."""
    diff = None
    if baseline not in (None, 0) and value is not None and np.isfinite(value) and np.isfinite(baseline):
        diff = round((value - baseline) / abs(baseline) * 100, 1)
    f = lambda x: None if x is None or not np.isfinite(x) else round(float(x), 4)
    return {"metric": metric, "value": f(value), "baseline": f(baseline), "diff_pct": diff,
            "threshold": f(threshold), "unit": unit, "source": source, "note": note}
