"""Rider-facing "why is traffic heavy?": a translation of FlowSense's existing diagnosis, evidence and forecast.

Nothing new is inferred here. The cause comes from detection.diagnose (via Snapshot.diagnosis), the numbers from
Snapshot.evidence / row and the forecaster. Every line shown to a rider carries the metric, observed value, baseline and
timestamp it came from. If the data does not support a cause the answer is "unclear", never a guess.
Organizer incident labels are never read here: they are training/evaluation data, not runtime signals.
"""
import numpy as np

from . import config as C, detection
from .user_traffic import FALLBACK_M, LEVEL_LABEL, LEVEL_OF_STATE, _segments, dist_to_segments, project

SEG_RADIUS_M = 750
NEIGHBOR_SLOWING = 0.05   # ponytail: a neighbour whose speed ratio dropped this much counts as "also slowing"
TYPE_LABEL = {"accident_like": "Accident-like incident", "lane_blockage": "Lane blockage-like incident",
              "stalled_vehicle": "Stalled-vehicle-like incident", "road_closure": "Road closure-like incident",
              "demand_surge": "Demand surge"}
CERTAINTY = {"HIGH": "likely", "MEDIUM": "possible", "LOW": "possible"}
ORDER = ["free_flow", "moderate", "heavy", "severe"]


def find_segment(snap, lat: float, lon: float) -> str | None:
    """Most congested simulated segment within SEG_RADIUS_M of a point (else the nearest congested one within FALLBACK_M)."""
    segs, _, _, A, B, _ = _segments()
    d = dist_to_segments(project(np.array([[lat, lon]])), A, B)[0]
    sr = snap.c["sr"][:, snap.ti].astype(float)
    bad = np.nan_to_num(sr, nan=1.0) < C.STATE_THRESHOLDS[0][1]
    near = np.nonzero(bad & (d <= SEG_RADIUS_M))[0]
    if len(near):
        return segs[int(near[np.argmin(sr[near])])]
    cand = np.nonzero(bad & (d <= FALLBACK_M))[0]
    return segs[int(cand[np.argmin(d[cand])])] if len(cand) else None


def _item(signal, message, metric, e, snap, seg):
    n = lambda x: None if x is None else float(x)     # evidence() can carry numpy scalars, which JSON cannot encode
    return {"signal": signal, "message": message, "metric": metric, "observed": n(e.get("value")), "reference": n(e.get("baseline")),
            "change_pct": n(e.get("diff_pct")), "unit": e.get("unit"), "segment_id": seg, "timestamp": str(snap.t)}


def _forecast(snap, seg):
    if snap.forecast is None:
        return None
    si = snap.idx[seg]
    ff = float(snap.net.loc[seg].free_flow_speed_kmh)
    now = LEVEL_OF_STATE[str(snap.state[si])]
    out, worst = {}, 0
    for h in (15, 30, 60):
        r = snap.forecast[("speed", h)]
        p50, lo, hi = float(r["p50"][si]) / ff, float(r["p10"][si]) / ff, float(r["p90"][si]) / ff
        if not np.isfinite(p50):
            continue
        lvl = LEVEL_OF_STATE[str(detection.state_of(np.array([p50]))[0])]
        width = hi - lo if np.isfinite(hi - lo) else 1.0
        out[f"{h}m"] = {"level": lvl, "label": LEVEL_LABEL[lvl], "confidence": "High" if width < 0.15 else "Medium" if width < 0.3 else "Low"}
        worst = max(worst, ORDER.index(lvl) if lvl in ORDER else 0)
    if not out:
        return None
    cur = ORDER.index(now) if now in ORDER else 0
    return {"now": now, "now_label": LEVEL_LABEL[now], "horizons": out,
            "trend": "worsening" if worst > cur else "easing" if worst < cur else "steady"}


def explain(snap, seg: str) -> dict:
    row, dg = snap.row(seg), snap.diagnosis(seg)
    ev = {e["metric"]: e for e in snap.evidence(seg)}
    level = LEVEL_OF_STATE.get(str(row["state"]), "no_data")
    cause, ctx, items = dg["cause"], dg["context"], []
    detected = bool(dg["detected"])
    out = {"segment_id": seg, "traffic_level": level, "traffic_label": LEVEL_LABEL[level], "updated_at": str(snap.t),
           "source": "FlowSense traffic simulation (organizer dataset replay)", "detected": detected}
    fc = _forecast(snap, seg)
    if level in ("free_flow", "no_data"):
        return {**out, "status": "normal", "cause": None, "evidence": [], "forecast": fc,
                "headline": "No significant traffic problem here", "summary": "Traffic is flowing normally at this location."}

    sp = ev["speed"]
    if sp["diff_pct"] is not None and sp["diff_pct"] <= -10:
        items.append(_item("speed_drop", f"Speed is {abs(round(sp['diff_pct']))}% below normal for this time of day", "speed", sp, snap, seg))
    if dg["onset_step"] >= 0.1:
        items.append(_item("sudden_onset", "Traffic slowed sharply within one 5-minute interval", "local_speed_ratio_drop",
                           {"value": round(float(dg["onset_step"]), 3), "baseline": None, "unit": "speed ratio"}, snap, seg))
    q = ev["queue_change_15min"]
    if q["value"] is not None and q["value"] >= 5:
        items.append(_item("queue_growth", f"Queue grew by about {round(q['value'])} vehicles in the last 15 minutes", "queue_change_15min", q, snap, seg))
    ci = ev["congestion_index"]
    if ci["value"] is not None and ci["baseline"] is not None and ci["value"] - ci["baseline"] >= 0.15:
        items.append(_item("congestion_change", "Congestion is well above what is usual here", "congestion_index", ci, snap, seg))
    rw = ev["roadwork_active"]
    items.append(_item("roadwork", ("Active roadwork on record here" + (f" ({rw['note']})" if rw.get("note") else "")) if ctx["roadwork_active"]
                       else "No active roadwork on record", "roadwork_active", rw, snap, seg))
    if ctx["rain_intensity"] >= C.RAIN_EFFECT:
        items.append(_item("weather", "Rain is reported, which can slow traffic", "rain_intensity", ev["rain_intensity"], snap, seg))

    cause_out = {"label": "Cause unclear", "certainty": "unknown", "confidence": None, "confidence_label": None, "type": None, "type_note": None}
    status, headline = "congestion", "Severe congestion ahead" if level == "severe" else "Heavy traffic ahead"
    summary = "Traffic is abnormal, but FlowSense cannot confidently identify the cause."
    if cause == "ROADWORK_EFFECT":
        cause_out |= {"label": "Roadwork", "certainty": "reported", "confidence_label": "High"}
        summary = "Reduced road capacity because of active roadwork on record."
    elif cause == "TRANSIENT_INCIDENT":
        t = dg["incident_type"] or {}
        typed = t.get("type") not in (None, "UNKNOWN") and t.get("confidence") == "MEDIUM"
        cause_out |= {"label": "Traffic disruption", "certainty": CERTAINTY[dg["confidence_label"]], "confidence": dg["confidence"],
                      "confidence_label": dg["confidence_label"].capitalize(), "type": "Undetermined"}
        if typed:
            cause_out["type"] = TYPE_LABEL.get(str(t["type"]).lower(), "Undetermined")
            cause_out["type_note"] = "The type is a weak estimate: FlowSense cannot tell an accident from a breakdown or blockage."
        status, headline = "disruption_detected", "Traffic disruption ahead"
        summary = ("Traffic conditions changed sharply here"
                   + (f", which looks like a {cause_out['type'].lower()}" if typed else "") + ". This is an inference, not a confirmed report.")
    elif cause == "PROPAGATED_CONGESTION":
        cause_out |= {"label": "Congestion spreading from nearby roads", "certainty": "likely", "confidence_label": "Medium"}
        summary = "Slowing traffic on a neighbouring road is backing up onto this one."
    elif cause == "RECURRING_BOTTLENECK":
        cause_out |= {"label": "Recurring congestion", "certainty": "likely", "confidence_label": "Medium"}
        summary = "This road repeatedly slows at similar times of day."
        items.append(_item("recurring", "This road is a known peak-hour bottleneck", "recurring_bottleneck", {"value": 1.0}, snap, seg))
    elif cause == "WEATHER_EVENT_EFFECT":
        why = "Rain" if ctx["rain_intensity"] >= C.RAIN_EFFECT else "A city event" if ctx["event_present"] else "City-wide slowing"
        cause_out |= {"label": "City-wide slowdown" if why == "City-wide slowing" else "Weather or event slowdown", "certainty": "possible", "confidence_label": "Low"}
        summary = f"{why} may be contributing to slower traffic."
    # UNKNOWN / NORMAL_VARIATION while congested: stays "Cause unclear"

    nb = ev["largest_neighbor_drop"]["value"]
    neigh = nb is not None and nb >= NEIGHBOR_SLOWING
    if fc:
        fc["why"] = ("Congestion is building and nearby roads are also slowing." if fc["trend"] == "worsening" and neigh else
                     "Congestion is building here." if fc["trend"] == "worsening" else
                     "Traffic is expected to ease." if fc["trend"] == "easing" else "Traffic is expected to stay about the same.")
        if neigh and fc["trend"] != "easing":
            fc["spread"] = "Congestion may spread to nearby roads."
    return {**out, "status": status, "headline": headline, "summary": summary, "cause": cause_out, "evidence": items, "forecast": fc}
