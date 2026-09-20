"""Explicit congestion-propagation model (no GNN — the network is a regular 12x10 grid).

predicted_drop(neighbor) = source_drop x decay[direction][hop] x load_factor(neighbor)
eta(neighbor)            = lag[direction] x hop

`decay` and `lag` are calibrated on training incidents (source speed-ratio drop vs. the neighbors'
drop over the incident window). Evaluation is indirect: there are no labelled spillback events.
"""
import json

import numpy as np
import pandas as pd

from . import config as C
from .data import Panel
from .graph import hops_from, seg_index

CALIB_PATH = C.META / "propagation.json"


def _drop_series(c, si, cols):
    return c["base_sr"][si, cols] - c["sr"][si, cols]


def calibrate(p: Panel, c: dict, incidents: pd.DataFrame) -> dict:
    idx = seg_index()
    ratios = {("upstream", h): [] for h in range(1, C.PROP_MAX_HOPS + 1)}
    ratios.update({("downstream", h): [] for h in range(1, C.PROP_MAX_HOPS + 1)})
    lags = {"upstream": [], "downstream": []}
    for r in incidents.itertuples():
        cols = np.nonzero((p.times >= pd.Timestamp(r.start_time).ceil("5min")) & (p.times <= r.end_time))[0]
        if len(cols) < 2:
            continue
        src = np.nanmean(_drop_series(c, idx[r.segment_id], cols))
        if not src > 0.1:
            continue
        for seg, (hop, d) in hops_from(r.segment_id, C.PROP_MAX_HOPS).items():
            if hop == 0:
                continue
            s = _drop_series(c, idx[seg], cols)
            ratios[(d, hop)].append(float(np.nanmean(s) / src))
            if hop == 1 and np.nanmax(s) > 0.05:
                first = int(np.argmax(s > 0.5 * np.nanmax(s)))
                lags[d].append(first * C.STEP_MIN)
    out = {"decay": {d: {str(h): round(float(np.median(ratios[(d, h)])), 4) for h in range(1, C.PROP_MAX_HOPS + 1)}
                     for d in ("upstream", "downstream")},
           "lag_min_per_hop": {d: float(max(C.STEP_MIN, np.median(lags[d]) if lags[d] else C.STEP_MIN)) for d in lags},
           "n_incidents": int(len(incidents)),
           "samples": {f"{d}_{h}": len(v) for (d, h), v in ratios.items()}}
    CALIB_PATH.write_text(json.dumps(out, indent=2))
    return out


def params() -> dict:
    return json.loads(CALIB_PATH.read_text())


def propagate(source: str, source_drop: float, vc_now: dict, horizon_min: int | None = None) -> list:
    """Neighbors at risk from congestion on `source`. vc_now: segment -> current v/c (known at t)."""
    prm = params()
    out = []
    for seg, (hop, d) in hops_from(source, C.PROP_MAX_HOPS).items():
        if hop == 0:
            continue
        load = 0.75 + 0.5 * min(1.0, max(0.0, vc_now.get(seg, 0.5) or 0.0))  # busier neighbors absorb less [DESIGN]
        drop = max(0.0, source_drop) * prm["decay"][d][str(hop)] * load
        eta = prm["lag_min_per_hop"][d] * hop
        if horizon_min is not None and eta > horizon_min:
            continue
        risk = min(1.0, drop / 0.4)
        level = next((name for name, thr in C.PROP_RISK_LEVELS if risk >= thr), "MINIMAL")
        out.append({"segment_id": seg, "hop": hop, "direction": d, "predicted_speed_ratio_drop": round(drop, 4),
                    "risk_score": round(risk, 3), "impact_level": level, "estimated_time_to_impact_min": eta,
                    "reason": f"{d} hop {hop}: calibrated decay {prm['decay'][d][str(hop)]:.2f} x load factor {load:.2f}"})
    return sorted(out, key=lambda r: -r["risk_score"])


def evaluate(p: Panel, c: dict, incidents: pd.DataFrame, affected_thr: float = 0.05) -> dict:
    """Indirect validation on held-out incidents. Prediction issued 10 min after incident start using
    only data <= that time; 'affected' = neighbor mean speed-ratio drop over the rest of the window > thr.
    Control = same neighbors, same clock time, on other days (how often they drop that much anyway)."""
    idx = seg_index()
    tp = fp = fn = 0
    lag_err, ctrl_hits, ctrl_n = [], 0, 0
    for r in incidents.itertuples():
        t_issue = pd.Timestamp(r.start_time).ceil("5min") + pd.Timedelta(minutes=10)
        if t_issue not in p.times:
            continue
        ti = p.times.get_loc(t_issue)
        cols = np.nonzero((p.times > t_issue) & (p.times <= r.end_time))[0]
        if not len(cols):
            continue
        src_drop = float(c["base_sr"][idx[r.segment_id], ti] - c["sr"][idx[r.segment_id], ti])
        vc = {s: float(c["fr"][idx[s], ti]) for s in idx}
        pred = {x["segment_id"]: x for x in propagate(r.segment_id, src_drop, vc) if x["impact_level"] != "MINIMAL"}
        for seg, (hop, d) in hops_from(r.segment_id, C.PROP_MAX_HOPS).items():
            if hop == 0:
                continue
            s = _drop_series(c, idx[seg], cols)
            actual = bool(np.nanmean(s) > affected_thr)
            if seg in pred and actual:
                tp += 1
            elif seg in pred:
                fp += 1
            elif actual:
                fn += 1
            # control: other days, same clock times
            for dd in range(-3, 4):
                if dd == 0:
                    continue
                cc = cols + dd * C.SLOTS_PER_DAY
                if cc.min() >= 0 and cc.max() < len(p.times):
                    ctrl_n += 1
                    ctrl_hits += bool(np.nanmean(_drop_series(c, idx[seg], cc)) > affected_thr)
    prec = tp / (tp + fp) if tp + fp else None
    rec = tp / (tp + fn) if tp + fn else None
    return {"evaluation": "indirect behavioural validation on incidents_validation (not supervised spillback truth)",
            "prediction_issued": "incident start + 10 min, data <= issue time",
            "affected_definition": f"neighbor mean speed-ratio drop vs. baseline > {affected_thr} over the remaining window",
            "neighbor_hit_rate_precision": prec, "neighbor_recall": rec,
            "false_propagation_rate": (fp / (tp + fp)) if tp + fp else None,
            "tp": tp, "fp": fp, "fn": fn,
            "control_base_rate": ctrl_hits / ctrl_n if ctrl_n else None,
            "note": "control_base_rate = how often the same neighbors show that drop at the same clock time on other days"}
