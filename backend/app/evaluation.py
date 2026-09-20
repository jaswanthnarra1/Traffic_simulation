"""Unified evaluation. The only module that reads forecast_targets_* (as ground truth, never as features).
scenario_examples is deliberately not used anywhere here (it duplicates incidents_train)."""
import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score

from . import config as C, data, detection as D, forecasting, propagation, simulation
from .graph import path_is_legal


def _err(y, yhat, mape=False):
    ok = np.isfinite(y) & np.isfinite(yhat)
    e = yhat[ok] - y[ok]
    out = {"mae": float(np.mean(np.abs(e))), "rmse": float(np.sqrt(np.mean(e ** 2))), "n": int(ok.sum())}
    if mape:
        nz = ok & (np.abs(y) > 1e-6)
        out["mape_pct"] = float(np.mean(np.abs((yhat[nz] - y[nz]) / y[nz])) * 100)
    return out


# ---------------------------------------------------------------- forecast
def forecast_metrics(p: data.Panel) -> dict:
    tg = data.q("SELECT * FROM forecast_targets_validation")  # LABELS ONLY
    anchors = pd.DatetimeIndex(sorted(tg.timestamp.unique()))
    cols = np.array([p.times.get_loc(t) for t in anchors])
    tg = tg.set_index(["segment_id", "timestamp"])
    idx = pd.MultiIndex.from_product([p.segs, anchors])  # segment-major, matches to_matrix row order
    tg = tg.reindex(idx)
    fc = forecasting.predict(p, cols)
    sr_now = (p.v["speed_kmh"] / data.network().free_flow_speed_kmh.to_numpy("float32")[:, None])[:, cols].reshape(-1)
    congested = sr_now < C.STATE_THRESHOLDS[0][1]
    out = {"split": "validation (forecast_targets_validation.csv as ground truth)", "anchors": int(len(anchors)),
           "segments": len(p.segs), "congested_share": float(congested.mean()), "results": {}}
    for (metric, hmin), r in fc.items():
        y = tg[f"target_{metric}_{hmin}m"].to_numpy(float)
        model, pers = _err(y, r["p50"], metric == "speed"), _err(y, r["now"], metric == "speed")
        cov = np.isfinite(y) & (y >= r["p10"]) & (y <= r["p90"])
        entry = {"model": model, "persistence": pers,
                 "mae_improvement_pct": round((1 - model["mae"] / pers["mae"]) * 100, 2) if pers["mae"] else None,
                 "p10_p90_coverage": float(cov[np.isfinite(y)].mean()),
                 "congested_slots": {"model": _err(y[congested], r["p50"][congested]),
                                     "persistence": _err(y[congested], r["now"][congested])}}
        out["results"][f"{metric}_{hmin}m"] = entry
    return out


# ---------------------------------------------------------------- detection
def _prf(pred, truth):
    tp = int((pred & truth).sum()); fp = int((pred & ~truth).sum()); fn = int((~pred & truth).sum())
    pr = tp / (tp + fp) if tp + fp else 0.0
    rc = tp / (tp + fn) if tp + fn else 0.0
    return {"precision": pr, "recall": rc, "f1": 2 * pr * rc / (pr + rc) if pr + rc else 0.0,
            "true_positives": tp, "false_positives": fp, "false_negatives": fn}


def detection_metrics(p: data.Panel) -> dict:
    meta = D.detector_meta()
    base = D.load_baseline()
    out = {"detector": meta["variant"], "threshold": meta["threshold"],
           "positive_definition": meta["positive_definition"],
           "threshold_selected_on": "incidents_train only", "splits": {}}
    for split, (a, b) in {"train (in-sample, calibration)": (C.TRAIN_START, C.TRAIN_END),
                          "validation (held-out)": (C.VAL_START, C.VAL_END)}.items():
        key = "train" if split.startswith("train") else "validation"
        pp = data.slice_panel(p, a, b)
        inc, rw = data.incidents(key), data.table(f"roadworks_{key}")
        m, r = D.label_mask(pp, inc), D.label_mask(pp, rw)
        c = D.components(pp, base)
        s = D.score_variant(c, meta["variant"])
        flag = s >= meta["threshold"]
        slot = _prf(flag & ~r, m & ~r)
        days = len(pp.times) / C.SLOTS_PER_DAY
        slot["false_positive_slots_per_day"] = slot["false_positives"] / days
        # event level
        idx = {x: i for i, x in enumerate(pp.segs)}
        detected, delays, types = 0, [], []
        cents_ok = D.TYPE_CENTROIDS.exists()
        for x in inc.itertuples():
            si = idx[x.segment_id]
            cols = np.nonzero(m[si])[0]
            hit = np.nonzero(flag[si, cols])[0]
            if len(hit):
                detected += 1
                delays.append((pp.times[cols[hit[0]]] - pd.Timestamp(x.start_time)).total_seconds() / 60)
                if cents_ok:
                    tl = D.type_likelihood(c, si, cols[hit[0]], cols[-1], pp)
                    types.append((x.incident_type, tl["type"]))
        ev = D.events_from_flags(flag, s, pp)
        if len(ev):
            lab = m | r
            ev["explained"] = [lab[e.s, e.st:e.en + 1].any() for e in ev.itertuples()]
        unexplained = int((~ev.explained).sum()) if len(ev) else 0
        entry = {"slot_level": slot, "event_level": {
            "incidents": int(len(inc)), "incidents_detected": detected,
            "event_recall": detected / len(inc) if len(inc) else None,
            "median_detection_delay_min": float(np.median(delays)) if delays else None,
            "detected_events": int(len(ev)), "events_not_matching_any_label": unexplained,
            "unmatched_events_per_day": unexplained / days}}
        if types:
            present = sorted({t for t, _ in types})
            acc = {t: {"n": sum(1 for a2, _ in types if a2 == t), "correct": sum(1 for a2, b2 in types if a2 == t and b2 == t)}
                   for t in present}
            entry["type_diagnosis"] = {
                "evaluated_on_detected_incidents": len(types),
                "accuracy": sum(a2 == b2 for a2, b2 in types) / len(types),
                "unknown_rate": sum(b2 == "UNKNOWN" for _, b2 in types) / len(types),
                "per_type": acc,
                "types_absent_from_this_split": sorted({"lane_blockage", "accident_like", "stalled_vehicle",
                                                        "demand_surge", "road_closure"} - set(present)),
                "chance_level_uniform_5_types": 0.2}
        out["splits"][key] = entry
    out["note"] = ("Events not matching any label include (a) incident-shaped drops absent from the labels and "
                   "(b) residual spillback. [INF] Training data contains ~1,000 abrupt local drops with no label, "
                   "roadwork, rain or adjacent incident, so label-based precision is a lower bound.")
    return out


# ---------------------------------------------------------------- bottleneck
def bottleneck_metrics() -> dict:
    import json
    det = json.loads(D.BOTTLENECK_PATH.read_text())
    ref = data.network().set_index("segment_id").structural_bottleneck  # benchmark only
    rows = pd.DataFrame(det["segments"]).set_index("segment_id")
    truth = ref.reindex(rows.index).astype(bool)
    pred = rows.detected
    res = _prf(pred.to_numpy(), truth.to_numpy())
    sens = {}
    for z in (-1.5, -2.0, -2.5, -3.0):
        sens[str(z)] = {k: v for k, v in _prf((rows.robust_z <= z).to_numpy(), truth.to_numpy()).items()}
        sens[str(z)]["n_flagged"] = int((rows.robust_z <= z).sum())
    order = rows.robust_z.sort_values()
    return {"reference": "network.structural_bottleneck (organizer flag) — used as a BENCHMARK, not ground truth for the UI",
            "method": det["method"], "threshold_z": C.BOTTLENECK_Z, "threshold_fixed_a_priori": True,
            **res, "n_detected": int(pred.sum()), "n_reference": int(truth.sum()),
            "segments_recovered": sorted(rows.index[pred & truth]),
            "ranking_auc": float(roc_auc_score(truth, -rows.robust_z)),
            "reference_in_top_16": int(truth[order.index[:16]].sum()),
            "sensitivity_by_threshold_label_informed": sens,
            "note": "Sensitivity table uses the reference labels and is reported for transparency only; "
                    "the runtime threshold was not changed after seeing it."}


# ---------------------------------------------------------------- propagation
def propagation_metrics(p: data.Panel) -> dict:
    pv = data.slice_panel(p, C.VAL_START, C.VAL_END)
    c = D.components(pv, D.load_baseline())
    out = propagation.evaluate(pv, c, data.incidents("validation"))
    out["calibration"] = propagation.params()
    return out


# ---------------------------------------------------------------- simulation
def simulation_metrics(p: data.Panel) -> dict:
    """No intervention ground truth exists (intervention_reference.csv is withheld). We report internal
    consistency: convergence, conservation, constraint validity, fit to observed flows, and simulated deltas."""
    net = data.network()
    out = {"ground_truth_available": False, "checks": {}, "baseline_fit": [], "scenario_deltas": []}
    for t in ("2026-01-16 08:00", "2026-01-16 13:00", "2026-01-17 18:00"):
        ti = p.at(t)
        obs = p.v["flow_vph"][:, ti].astype(float)
        hour = pd.Timestamp(t).hour
        peak = hour in C.PEAK_HOURS
        a = simulation.model(peak)
        sc = simulation.demand_scale(obs, peak)
        x, gap, paths = a.equilibrium(a.od_q * sc, a.capacity())
        if t.endswith("08:00"):
            out["checks"]["all_assigned_paths_respect_turn_restrictions"] = all(
                path_is_legal([a.seg[i] for i in pth], hour=hour) for pth in paths)
        out["baseline_fit"].append({"time": t, "demand_scale": round(sc, 4),
                                    "raw_assignment_flow_correlation_vs_observed": float(np.corrcoef(x, obs)[0, 1]),
                                    "relative_gap": gap,
                                    "over_capacity_links_raw_assignment": int((x / a.capacity() > 1).sum()),
                                    "over_capacity_links_observed": int((obs / net.capacity_vph.to_numpy() > 1).sum())})
    inc = data.incidents("validation")
    cand = data.table("planning_candidates").set_index("target_segment")
    for x in inc.itertuples():
        t = pd.Timestamp(x.start_time).ceil("5min")
        obs = p.v["flow_vph"][:, p.at(t)].astype(float)
        red = C.SEVERITY_CAPACITY_REDUCTION["CRITICAL" if x.severity >= 3 else "HEAVY" if x.severity == 2 else "MODERATE"]
        cur = simulation.scenario(t.hour, obs, {x.segment_id: 1 - red})
        clear = simulation.scenario(t.hour, obs, {x.segment_id: 1 - red}, cap_mult={x.segment_id: 1.0})
        cmp_ = simulation.compare(clear, cur, [x.segment_id])
        out["scenario_deltas"].append({"incident_segment": x.segment_id, "time": str(t), "capacity_reduction": red,
                                       "incident_delay_change_pct": cmp_["kpis"]["total_delay_veh_h"]["pct_change"],
                                       "within_solver_tolerance": cmp_["convergence"]["network_change_within_tolerance"],
                                       "segments_worsened": cmp_["segments_worsened"],
                                       "direct_candidate": x.segment_id in cand.index})
    out["checks"]["incident_increases_network_delay_share"] = float(np.mean(
        [s["incident_delay_change_pct"] > 0 for s in out["scenario_deltas"]]))
    out["checks"]["conservation"] = "each OD's demand is loaded on exactly one connected path per iteration (enforced; disconnection raises)"
    out["label"] = "SIMULATION ESTIMATE — not validated against intervention ground truth"
    return out
