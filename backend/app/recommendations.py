"""Intervention search -> feasibility -> counterfactual simulation -> ranking -> advisory.

No learned recommender: there is no historical 'which intervention worked' label. Candidates come
only from planning_candidates.csv; the ranking weights are a documented [DESIGN] choice (config.py).
"""
from functools import lru_cache

import numpy as np
import pandas as pd

from . import config as C, data, simulation
from .graph import hops_from, legal_detour, path_is_legal


def candidate_pool(seg: str) -> pd.DataFrame:
    """1) direct target candidate, 2) candidates within MAX_CANDIDATE_HOPS on the segment graph."""
    near = hops_from(seg, C.MAX_CANDIDATE_HOPS)
    cand = data.table("planning_candidates")
    cand = cand[cand.target_segment.isin(near)].copy()
    cand["hop"] = cand.target_segment.map(lambda s: near[s][0])
    cand["relation"] = cand.target_segment.map(lambda s: "direct" if near[s][0] == 0 else f"{near[s][1]} neighbor (hop {near[s][0]})")
    return cand.sort_values(["hop", "cost_index"])


def feasibility(row) -> dict:
    net = data.network().set_index("segment_id")
    checks = []
    ok = row.target_segment in net.index
    checks.append({"check": "target segment exists in network", "passed": bool(ok)})
    if ok and row.intervention_type == "signal_retiming":
        sig = net.at[row.target_segment, "signal_id"]
        checks.append({"check": "signal_retiming requires a signal at the target's downstream node",
                       "passed": bool(pd.notna(sig)), "detail": sig if pd.notna(sig) else "no signal_id"})
    if ok and row.intervention_type == "turn_lane":
        tgt = net.at[row.target_segment, "target_node"]
        n_exits = int((net.source_node == tgt).sum())
        checks.append({"check": "turn_lane requires >= 2 exits at the downstream node", "passed": n_exits >= 2,
                       "detail": f"{n_exits} exits"})
    checks.append({"check": "capacity after intervention stays positive", "passed": bool(row.capacity_delta_vph > -1e9)})
    checks.append({"check": f"feasibility_band = {row.feasibility_band}", "passed": True,
                   "detail": "penalised in ranking" if row.feasibility_band != "high" else None})
    return {"feasible": all(c["passed"] for c in checks), "checks": checks}


def score(cmp_: dict, row, local_before: float, local_after: float) -> dict:
    w = C.SCORE_WEIGHTS
    net_red = -(cmp_["kpis"]["total_delay_veh_h"]["pct_change"] or 0.0)
    loc_red = (local_before - local_after) / local_before * 100 if local_before > 0 else 0.0
    worse = cmp_["segments_worsened"] / len(cmp_["per_segment"]) * 100
    cost = row.cost_index / data.table("planning_candidates").cost_index.max()
    parts = {"network_delay_reduction_pct": round(net_red, 3), "local_delay_reduction_pct": round(loc_red, 3),
             "worsened_segments_pct": round(worse, 3), "cost_index_normalised": round(cost, 3),
             "feasibility_penalty": C.FEASIBILITY_PENALTY[row.feasibility_band]}
    total = (w["network_delay_reduction_pct"] * net_red + w["target_delay_reduction_pct"] * loc_red
             - w["worsened_links_pct"] * worse - w["cost_index"] * cost - parts["feasibility_penalty"])
    return {"score": round(total, 3), "components": parts, "weights": w}


@lru_cache(maxsize=64)
def recommend(t: str, seg: str) -> dict:
    from .engine import get
    snap = get(t)
    row = snap.row(seg)
    dg = snap.diagnosis(seg)
    si = snap.idx[seg]
    hour = snap.t.hour
    state = row["state"]
    obs = np.nan_to_num(snap.p.v["flow_vph"][:, snap.ti].astype(float))

    # current network: a detected incident is modelled as a capacity loss sized by the observed state [DESIGN]
    reduction = C.SEVERITY_CAPACITY_REDUCTION.get(state, 0.0) if dg["detected"] else 0.0
    ref = {seg: 1 - reduction} if reduction else {}
    current = simulation.scenario(hour, obs, ref)
    focus = [seg] + [s for s, (h, _) in hops_from(seg, 1).items() if h == 1]
    fidx = [snap.idx[s] for s in focus]
    local_before = float(current["flow"][fidx] @ (current["time"][fidx] - simulation.model(hour in C.PEAK_HOURS).fft[fidx]))

    pool = candidate_pool(seg)
    evaluated = []
    for r in pool.itertuples():
        fz = feasibility(r)
        item = {"candidate_id": r.candidate_id, "intervention_type": r.intervention_type, "target_segment": r.target_segment,
                "relation": r.relation, "hop": int(r.hop), "capacity_delta_vph": int(r.capacity_delta_vph),
                "cost_index": int(r.cost_index), "feasibility_band": r.feasibility_band, "feasibility": fz,
                "simulated": False}
        evaluated.append(item)
    feasible = [e for e in evaluated if e["feasibility"]["feasible"]][:C.MAX_CANDIDATES_SIMULATED]
    for e in feasible:
        cf = simulation.scenario(hour, obs, ref, cap_delta={e["target_segment"]: e["capacity_delta_vph"]})
        cmp_ = simulation.compare(current, cf, focus)
        local_after = float(cf["flow"][fidx] @ (cf["time"][fidx] - simulation.model(hour in C.PEAK_HOURS).fft[fidx]))
        rowc = pool.set_index("candidate_id").loc[e["candidate_id"]]
        e.update({"simulated": True, "impact": {k: cmp_[k] for k in ("kpis", "segments_improved", "segments_worsened",
                                                                       "worst_side_effects", "convergence", "focus")},
                  "local_delay_veh_h": {"before": round(local_before, 3), "after": round(local_after, 3)},
                  "ranking": score(cmp_, rowc, local_before, local_after),
                  "label": "SIMULATED IMPACT"})
    ranked = sorted([e for e in evaluated if e["simulated"]], key=lambda e: -e["ranking"]["score"])
    not_sim = [e for e in evaluated if not e["simulated"]]

    # operational options (always available, simulation-free)
    ops = []
    detour = legal_detour(seg, hour) if state in ("HEAVY", "CRITICAL") or dg["detected"] else None
    if detour:
        net = data.network().set_index("segment_id")
        tt = float((net.loc[detour, "length_km"] / net.loc[detour, "free_flow_speed_kmh"]).sum() * 60)
        ops.append({"action": "Prepare diversion", "path": detour, "legal_turns_verified": path_is_legal(detour, hour),
                    "free_flow_time_min": round(tt, 2),
                    "direct_free_flow_time_min": round(float(net.at[seg, "length_km"] / net.at[seg, "free_flow_speed_kmh"] * 60), 2)})
    if dg["detected"]:
        ops.append({"action": "Investigate", "reason": f"detected anomaly, cause {dg['cause']}, confidence {dg['confidence_label']}"})
    if state != "NORMAL" or dg["detected"]:
        ops.append({"action": "Monitor", "reason": "re-evaluate on the next 5-minute reading"})

    fc = snap.forecast_for(seg)
    worsening = None
    if fc:
        now_ci = fc["congestion"]["forecast"][0]["p50"]
        ci30 = fc["congestion"]["forecast"][2]["p50"]
        worsening = bool(now_ci is not None and ci30 is not None and ci30 > now_ci + 0.02)
    prop = snap.propagation_for(seg)
    high_risk = [x["segment_id"] for x in prop if x["impact_level"] == "HIGH"]

    best = ranked[0] if ranked else None
    improving = bool(best and best["ranking"]["components"]["network_delay_reduction_pct"] > 0
                     and best["ranking"]["components"]["local_delay_reduction_pct"] > 0)
    if state == "NORMAL" and not dg["detected"]:
        headline, kind = "No intervention recommended", "none"
    elif improving:
        verb = {"signal_retiming": "signal retiming", "capacity_upgrade": "capacity upgrade", "lane_addition": "lane addition",
                "turn_lane": "turn lane", "connector": "connector"}[best["intervention_type"]]
        headline, kind = f"Simulate {verb} {best['candidate_id']} on {best['target_segment']}", "candidate"
    else:
        headline, kind = "Operational-only response", "operational"

    reasons = []
    if dg["detected"]:
        reasons.append(f"anomaly score {row['anomaly_score']:.2f} >= threshold {snap.thr:.2f} ({dg['cause']})")
    reasons.append(f"current state {state}, speed ratio {row['speed_ratio']:.2f}" if row["speed_ratio"] is not None else "no current reading")
    if worsening is not None:
        reasons.append("forecast: congestion rises over the next 30 min" if worsening else "forecast: congestion not expected to rise over 30 min")
    if high_risk:
        reasons.append(f"high spillback risk to {', '.join(high_risk[:4])}")
    if kind == "candidate":
        reasons.append(f"best-ranked feasible candidate ({best['relation']}), score {best['ranking']['score']}")
    elif kind == "operational":
        reasons.append("no direct/neighbor candidate reduced simulated delay" if ranked else "no feasible planning candidate within 2 hops")

    conf_levels = ["LOW", "MEDIUM", "HIGH"]
    conf = dg["confidence_label"] if dg["detected"] else ("MEDIUM" if state != "NORMAL" else "HIGH")
    if kind == "candidate" and best["hop"] > 0:
        conf = conf_levels[max(0, conf_levels.index(conf) - 1)]  # neighbor-based -> one level lower
    if kind == "candidate" and best["impact"]["convergence"]["network_change_within_tolerance"]:
        conf = conf_levels[max(0, conf_levels.index(conf) - 1)]
    limitations = ["Impact is simulation-derived (static equilibrium, pivot-point on observed flows) — not a verified outcome.",
                   "Every candidate is modelled as +capacity_delta_vph on its target segment (the only effect the candidate data specifies).",
                   "Demand is static OD x a scale calibrated to observed veh-km at t; no time-varying demand exists in the data."]
    if kind == "candidate" and best["hop"] > 0:
        limitations.append("Neighbor-based intervention: no candidate targets this segment directly.")
    if kind == "candidate" and best["impact"]["convergence"]["network_change_within_tolerance"]:
        limitations.append("Network-wide change is within the solver's convergence tolerance; rely on the local (segment + neighbors) effect.")
    if dg.get("incident_type") and dg["incident_type"]["type"] == "UNKNOWN":
        limitations.append("Incident type could not be determined from traffic data.")

    return {"segment_id": seg, "time": str(snap.t), "state": state, "diagnosis": dg,
            "incident_capacity_reduction_assumed": reduction,
            "advisory": {"recommendation": headline, "kind": kind, "reasons": reasons, "confidence": conf,
                         "evidence": snap.evidence(seg),
                         "simulated_impact": ({"kpis": best["impact"]["kpis"], "local_delay_veh_h": best["local_delay_veh_h"],
                                               "segments_improved": best["impact"]["segments_improved"],
                                               "segments_worsened": best["impact"]["segments_worsened"],
                                               "label": "SIMULATED IMPACT"} if kind == "candidate" else None),
                         "limitations": limitations},
            "candidates_ranked": ranked, "candidates_not_simulated": not_sim, "operational_options": ops,
            "search": {"direct_candidates": int((pool.hop == 0).sum()), "neighbor_candidates": int((pool.hop > 0).sum()),
                       "max_hops": C.MAX_CANDIDATE_HOPS, "simulated": len(ranked)}}
