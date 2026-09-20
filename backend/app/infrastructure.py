"""AI Infrastructure Recommendation: compare interventions for the bottleneck zone found by the corridor analysis.

Layers (each replaceable without touching the UI):
- Traffic: the corridor analysis (corridor.py) through its TrafficDataProvider. Real per-segment features from the dataset.
- Land / infrastructure: an InfrastructureDataProvider. The only built-in one is SIMULATED (deterministic per zone, clearly labelled).
  A government-land / GIS provider implements the same protocol and is selected with INFRASTRUCTURE_PROVIDER.
- Impact: a BPR volume-delay model (config.BPR_ALPHA / BPR_BETA, the same one the simulation engine uses) applied to the zone's real
  utilisation and speed, with each intervention's capacity / signal-delay / demand effect from infrastructure_config.json.
  The effect sizes are DEMO ASSUMPTIONS, so every impact figure is labelled "simulated".
Nothing here is an engineering estimate or an official record.
"""
import hashlib
import json
import os
from collections import OrderedDict
from functools import lru_cache
from pathlib import Path
from typing import Protocol

import numpy as np

from . import config as C, corridor as K

CFG_PATH = Path(os.getenv("INFRASTRUCTURE_CONFIG_PATH") or Path(__file__).with_name("infrastructure_config.json"))
STRUCT_COST_FACTOR = 0.01     # +1% cost per affected structure (acquisition/relocation), a demo assumption
DISCLAIMER = ("This AI-generated infrastructure analysis is intended for planning and simulation purposes only. Actual construction requires detailed traffic "
              "engineering, structural, geotechnical, environmental, land and government feasibility studies.")


@lru_cache(maxsize=1)
def cfg() -> dict:
    return json.loads(CFG_PATH.read_text())


# ------------------------------------------------------------------ infrastructure data provider seam
class InfrastructureDataProvider(Protocol):
    """Land, right-of-way and constraint data for a zone. Implement with real GIS/land records to replace the simulation."""
    name: str
    simulated: bool
    def land(self, zone: dict) -> dict: ...
    def confidence(self) -> dict: ...


class SimulationInfrastructureProvider:
    """SIMULATED land evaluation. Deterministic for a given zone (seeded by its coordinates), so a corridor always shows the same
    values: it is a stable demo dataset, not random 'live' data. Not government land records."""
    name, simulated = "simulation (demo estimate)", True

    def land(self, zone: dict) -> dict:
        c = cfg()["land"]
        seed = int(hashlib.sha1(f"{zone['key']}".encode()).hexdigest()[:8], 16)
        r = np.random.default_rng(seed)
        avail = float(r.uniform(*c["availability_pct"]))
        gov = avail * float(r.uniform(*c["government_share_of_available"]))
        private = (100 - avail) * float(r.uniform(*c["private_impact_share_of_rest"]))
        return {
            "data_status": "SIMULATED", "label": "SIMULATED LAND EVALUATION (demo estimate, not government land records)",
            "land_availability_pct": round(avail), "government_land_pct": round(gov), "private_property_impact_pct": round(private),
            "structures_affected": int(round(zone["length_km"] * float(r.uniform(*c["structures_per_km"])))),
            "row_availability_pct": round(float(np.clip(avail + r.uniform(*c["row_delta_pct"]), 0, 100))),
            "utility_conflict": str(r.choice(c["utility_conflict"])), "environmental_constraint": str(r.choice(c["environmental"])),
            "construction_difficulty": round(float(r.uniform(*c["construction_difficulty"])), 2),
        }

    def confidence(self) -> dict:
        return {"land": cfg()["land"]["confidence_pct"], "infrastructure": cfg()["infrastructure_confidence_pct"]}


PROVIDERS = {"simulation": SimulationInfrastructureProvider}
def get_provider() -> InfrastructureDataProvider:
    name = os.getenv("INFRASTRUCTURE_PROVIDER", "simulation")
    if name not in PROVIDERS:
        raise K.CorridorError(f"Infrastructure data provider '{name}' is not available. Configured providers: {', '.join(PROVIDERS)}.", 503)
    return PROVIDERS[name]()


# ------------------------------------------------------------------ zone metrics (real corridor features)
def zone_metrics(cand: dict, segments: list[dict]) -> dict:
    ids = set(cand["affected_segments"])
    z = [s for s in segments if s["segment_id"] in ids and s["data_status"] == "MATCHED"]
    if not z:
        raise K.CorridorError("Insufficient traffic observations are available for this corridor.", 422)
    w = K._wavg
    length_km = cand["estimated_length_m"] / 1000
    speed, ff = w(z, "average_speed"), w(z, "free_flow_speed_kmh")
    grid = {s["grid_segment_id"]: s for s in z}
    return {
        "key": f"{cand['start'][0]:.4f},{cand['start'][1]:.4f}-{cand['end'][0]:.4f},{cand['end'][1]:.4f}", "length_km": length_km, "segments": len(z),
        "utilization": w(z, "capacity_utilization_pct") / 100, "speed": speed, "ff": ff, "volume": w(z, "traffic_volume"), "capacity": w(z, "road_capacity"),
        "min_lanes": min(s["lane_count"] for s in z), "signals": sum(s["signal_count"] for s in z), "signal_delay_s": max((s["signal_delay_s"] or 0) for s in z),
        "junctions": sum(s["junction_count"] or 0 for s in z), "incidents": sum(s["accident_count"] for s in grid.values()),
        "growth_pct": w(z, "historical_growth_pct"), "congestion_index": w(z, "congestion_index"), "delay_min": sum(s["delay_min"] for s in z),
    }


def _times(z: dict, u: float) -> tuple[float, float, float]:
    """(non-signal minutes, signal minutes, free-flow minutes) to cross the zone at utilisation u, from the measured peak speed."""
    L, speed = z["length_km"], max(z["speed"], 1.0)
    t0 = L / speed * 60
    sig = min(z["signals"] * z["signal_delay_s"] / 60, cfg()["simulation"]["max_signal_share_of_time"] * t0)
    r0 = 1 + C.BPR_ALPHA * min(z["utilization"], 1.5) ** C.BPR_BETA
    r = 1 + C.BPR_ALPHA * min(u, 1.5) ** C.BPR_BETA
    return (t0 - sig) * r / r0, sig, L / z["ff"] * 60


def _state(z, t_ns, t_sig, t_ff):
    t = max(t_ns + t_sig, t_ff)
    speed = z["length_km"] / t * 60
    return {"speed_kmh": round(float(speed), 1), "congestion_pct": round(float(max(0.0, 1 - speed / z["ff"]) * 100)), "delay_min": round(float(max(t - t_ff, 0)), 1)}


def impact(iv: dict, z: dict, growth: float = 0.0) -> dict:
    """Before/after for one intervention: BPR on the zone's measured utilisation. `growth` scales demand (planning-horizon view)."""
    gain = iv["capacity_gain"] if iv["capacity_gain"] is not None else min(iv["max_capacity_gain"], 1 / max(z["min_lanes"], 1))
    u_b = z["utilization"] * (1 + growth)
    u_a = u_b * (1 - iv["demand_shift"]) / (1 + gain)
    t_ns_b, sig_b, t_ff = _times(z, u_b)
    t_ns_a, _, _ = _times(z, u_a)
    b, a = _state(z, t_ns_b, sig_b, t_ff), _state(z, t_ns_a, sig_b * (1 - iv["signal_removal"]), t_ff)
    dred = (b["delay_min"] - a["delay_min"]) / b["delay_min"] if b["delay_min"] > 0 else 0.0
    return {"before": b, "after": a, "capacity_change_pct": round(gain * 100), "speed_change_pct": round((a["speed_kmh"] / b["speed_kmh"] - 1) * 100),
            "congestion_change_pct": round(-(b["congestion_pct"] - a["congestion_pct"]) / b["congestion_pct"] * 100) if b["congestion_pct"] else 0,
            "congestion_change_pts": a["congestion_pct"] - b["congestion_pct"],
            "delay_reduction_min": round(b["delay_min"] - a["delay_min"], 1), "delay_reduction_share": dred,
            "emissions_change_pct": -round(dred * cfg()["simulation"]["emission_delay_elasticity"] * 100), "data_status": "SIMULATED"}


# ------------------------------------------------------------------ evaluation of all interventions
def _applicable(iv_id: str, z: dict, alt_routes: int) -> str | None:
    if iv_id == "signal_optimization" and z["signals"] <= 0:
        return "No signalised control in the zone."
    if iv_id == "junction_redesign" and z["junctions"] < 1 and z["signals"] <= 0:
        return "No junction in the zone."
    if iv_id == "traffic_diversion" and alt_routes < 1:
        return "The routing provider found no alternative route."
    return None


def _length_and_cost(iv_id, iv, z, cand_len_km):
    if iv_id == "flyover":
        return cand_len_km, iv["cost_per_km_cr"] * cand_len_km
    if iv_id == "road_extension":
        L = z["length_km"] * iv["length_factor"]
        return L, iv["cost_per_km_cr"] * L
    if iv_id == "road_widening":
        return z["length_km"], iv["cost_per_km_cr"] * z["length_km"]
    if iv_id == "junction_redesign":
        return None, iv["cost_per_junction_cr"] * max(1, min(iv["max_junctions"], round(z["junctions"] or z["signals"])))
    if iv_id == "signal_optimization":
        return None, iv["cost_per_signal_cr"] * max(1, round(z["signals"]))
    return None, iv["fixed_cost_cr"]


def evaluate_interventions(z: dict, land: dict, alt_routes: int, cand_len_km: float) -> list[dict]:
    c = cfg()
    out = []
    for iv_id, iv in c["interventions"].items():
        na = _applicable(iv_id, z, alt_routes)
        length, cost = _length_and_cost(iv_id, iv, z, cand_len_km)
        row = {"id": iv_id, "label": iv["label"], "applicable": na is None, "reason_not_applicable": na, "data_status": "SIMULATED"}
        if na:
            out.append(row | {"scores": None, "simulated_impact": None, "length_km": length, "estimated_cost_cr": None})
            continue
        cost *= 1 + STRUCT_COST_FACTOR * land["structures_affected"] * (iv["land_need"] > 0)
        now = impact(iv, z)
        future = impact(iv, z, c["simulation"]["horizon_demand_growth_pct"] / 100)
        traffic_impact = float(np.clip(-now["congestion_change_pct"], 0, 100))
        long_term = float(np.clip(-future["congestion_change_pct"], 0, 100))
        need = iv["land_need"]
        land_score = float(np.clip(100 - need * (land["private_property_impact_pct"] + min(land["structures_affected"], 30)), 0, 100))
        constr = float(np.clip(iv["base_construction"] - iv["difficulty_sensitivity"] * land["construction_difficulty"] * 2
                               - (5 if land["utility_conflict"] == "Medium" else 10 if land["utility_conflict"] == "High" else 0), 0, 100))
        out.append(row | {"length_km": None if length is None else round(length, 2), "estimated_cost_cr": round(cost, 1), "land_need_factor": need,
                          "scores": {"traffic_impact": round(traffic_impact), "land_feasibility": round(land_score), "construction_feasibility": round(constr),
                                     "long_term_impact": round(long_term)}, "simulated_impact": now, "_eff": traffic_impact / max(cost, 0.5)})
    live = [r for r in out if r["applicable"]]
    best = max((r["_eff"] for r in live), default=0)
    for r in live:                                              # cost efficiency: benefit per crore, relative to the best applicable option
        r["scores"]["cost_efficiency"] = round(100 * r.pop("_eff") / best) if best > 0 else 0
        w = c["score_weights"]
        r["scores"]["overall"] = round(sum(r["scores"][k] * w[k] for k in w) / sum(w.values()))
    return sorted(out, key=lambda r: (not r["applicable"], -(r["scores"]["overall"] if r["scores"] else 0)))


def _place(seg: dict) -> str:
    name = seg.get("road_name")
    return f"near {name}" if name else "road name unavailable"


def recommend(ivs: list[dict], cand: dict, z: dict, land: dict, segments: list[dict], provider) -> dict:
    live = [r for r in ivs if r["applicable"]]
    rc = cfg()["recommend"]
    top = live[0] if live else None
    base = {"data_status": "SIMULATED", "disclaimer": DISCLAIMER}
    if not top or top["scores"]["overall"] < rc["min_overall"] or top["scores"]["traffic_impact"] < rc["min_traffic_impact"]:
        return base | {"action": "none", "headline": "No major infrastructure intervention indicated",
                       "summary": "No evaluated intervention reaches the minimum simulated benefit for this corridor. Review operational measures first.", "why": []}
    cov = [s for s in segments if s["segment_id"] in set(cand["affected_segments"])]
    why = [e["text"] for e in cand["explanation"] if e["factor"] != "concentration"][:4]
    why.append(f"{sum(1 for s in cov if s['bottleneck_level'] in ('HIGH', 'CRITICAL'))} of {len(cov)} adjacent segments are HIGH or CRITICAL bottlenecks.")
    if land["land_availability_pct"] >= 60:
        why.append(f"Sufficient simulated land availability ({land['land_availability_pct']}%).")
    else:
        why.append(f"Simulated land availability is limited ({land['land_availability_pct']}%).")
    runner = live[1] if len(live) > 1 else None
    head = {"flyover": "Construct a candidate flyover", "road_extension": "Extend the road", "road_widening": "Widen the road",
            "junction_redesign": "Redesign the junction(s)", "signal_optimization": "Optimise signal timing", "traffic_diversion": "Divert traffic"}[top["id"]]
    imp = top["simulated_impact"]
    out = base | {
        "action": top["id"], "headline": head, "overall_score": top["scores"]["overall"], "provider": provider.name,
        "summary": (f"Based on the current traffic simulation, the corridor contains a persistent bottleneck zone. Among the evaluated interventions, "
                    f"'{top['label']}' gives the highest overall suitability ({top['scores']['overall']}/100)"
                    + (f", ahead of '{runner['label']}' ({runner['scores']['overall']}/100)." if runner else ".")),
        "why": why,
        "simulated_impact": {"congestion_change_pct": imp["congestion_change_pct"], "congestion_change_pts": imp["congestion_change_pts"], "peak_travel_time_change_min": -imp["delay_reduction_min"],
                             "speed_change_pct": imp["speed_change_pct"], "emissions_change_pct": imp["emissions_change_pct"]},
        "candidate": None,
        "caution": (f"In this simulation the zone runs only {imp['before']['congestion_pct']}% below free-flow speed at peak, so absolute gains are modest."
                    if imp["before"]["congestion_pct"] < 20 else None),
    }
    if top["id"] in ("flyover", "road_extension", "road_widening"):
        first = next(s for s in cov if s["segment_id"] == cand["affected_segments"][0])
        last = next(s for s in cov if s["segment_id"] == cand["affected_segments"][-1])
        out["candidate"] = {"start": cand["start"], "end": cand["end"], "start_label": _place(first), "end_label": _place(last),
                            "length_km": top["length_km"], "estimated_cost_cr": top["estimated_cost_cr"]}
    return out


def data_confidence(corr: dict, provider) -> dict:
    w = cfg()["confidence_weights"]
    segs = corr["segments"]
    named = sum(1 for s in segs if s["road_name"]) / len(segs) * 100
    pc = provider.confidence()
    comps = {"traffic": corr["confidence"]["score"], "road_network": round(named), "land": pc["land"], "infrastructure": pc["infrastructure"]}
    return {"overall": round(sum(comps[k] * w[k] for k in w) / sum(w.values())), "components": comps,
            "simulated": [k for k in ("land", "infrastructure") if provider.simulated],
            "meaning": "Data-quality indicator (coverage, completeness, source). It is not the probability that a project would succeed or be built."}


_CACHE: "OrderedDict[str, dict]" = OrderedDict()      # ponytail: in-process (last 50), like the corridor cache. Upgrade: persist with the corridor analyses


def analyze(corr: dict, provider: InfrastructureDataProvider | None = None) -> dict:
    provider = provider or get_provider()
    key = f"{corr['id']}:{provider.name}"
    if key in _CACHE:
        return _CACHE[key]
    cand = corr["recommended_analysis"]
    common = {"mode": "SIMULATION", "corridor_id": corr["id"], "corridor": corr, "disclaimer": DISCLAIMER, "labels": {
        "traffic": "SIMULATION (organizer dataset replay)", "land": "SIMULATED" if provider.simulated else "provider data",
        "infrastructure": "SIMULATED" if provider.simulated else "provider data", "banner": "Simulation / Demo Data"}}
    if cand is None:
        res = common | {"traffic": {"congestion_index": corr["corridor"]["congestion_index"], "traffic_status": corr["corridor"]["traffic_status"]},
                        "bottlenecks": {"zones": [], "counts": corr["bottlenecks"]["counts"]}, "land_evaluation": None, "interventions": [],
                        "recommendation": {"action": "none", "headline": "No bottleneck zone found", "data_status": "SIMULATED", "disclaimer": DISCLAIMER, "why": [],
                                           "summary": "No group of HIGH or CRITICAL bottleneck segments was found on this corridor, so no infrastructure intervention is indicated by this data."},
                        "confidence": data_confidence(corr, provider), "provider": {"infrastructure": provider.name, "traffic": corr["provider"]["traffic"]}}
    else:
        z = zone_metrics(cand, corr["segments"])
        land = provider.land(z)
        ivs = evaluate_interventions(z, land, corr["corridor"]["alternative_routes"], cand["estimated_length_m"] / 1000)
        rec = recommend(ivs, cand, z, land, corr["segments"], provider)
        res = common | {
            "traffic": {"zone_congestion_pct": ivs[0]["simulated_impact"]["before"]["congestion_pct"] if ivs and ivs[0]["applicable"] else None,
                        "zone_delay_min": round(z["delay_min"], 1), "zone_avg_speed_kmh": round(z["speed"], 1), "zone_volume_vph": round(z["volume"]),
                        "zone_capacity_utilization_pct": round(z["utilization"] * 100), "zone_incidents": z["incidents"], "zone_length_km": round(z["length_km"], 2),
                        "congestion_index": corr["corridor"]["congestion_index"], "traffic_status": corr["corridor"]["traffic_status"]},
            "bottlenecks": {"zones": [{"name": cand["name"], "start_segment": cand["affected_segments"][0], "end_segment": cand["affected_segments"][-1],
                                       "bottleneck_segments": cand["bottleneck_segments"], "length_km": round(z["length_km"], 2)}], "counts": corr["bottlenecks"]["counts"]},
            "land_evaluation": land, "interventions": ivs, "recommendation": rec,
            "flyover": next((r for r in ivs if r["id"] == "flyover"), None), "candidate": {k: cand[k] for k in ("name", "start", "end", "estimated_length_m", "affected_segments", "suitability")},
            "confidence": data_confidence(corr, provider), "provider": {"infrastructure": provider.name, "traffic": corr["provider"]["traffic"]},
        }
    _CACHE[key] = res
    while len(_CACHE) > 50:
        _CACHE.popitem(last=False)
    return res


def simulate(corridor_id: str, intervention: str) -> dict:
    """Current vs after for one intervention, now and at the planning horizon (demand grown by the configured %). SIMULATED."""
    res = next((v for k, v in _CACHE.items() if k.startswith(corridor_id + ":")), None)
    if res is None or not res["interventions"]:
        raise K.CorridorError("Run the infrastructure analysis first (results are kept in memory for the last 50 runs).", 404)
    iv = next((r for r in res["interventions"] if r["id"] == intervention), None)
    if iv is None:
        raise K.CorridorError(f"Unknown intervention '{intervention}'.", 422)
    if not iv["applicable"]:
        raise K.CorridorError(iv["reason_not_applicable"] or "This intervention is not applicable to the corridor.", 422)
    cand = res["corridor"]["recommended_analysis"]
    z = zone_metrics(cand, res["corridor"]["segments"])
    p = cfg()["interventions"][intervention]
    g = cfg()["simulation"]["horizon_demand_growth_pct"]
    return {"mode": "SIMULATION", "label": "SIMULATED IMPACT", "intervention": {"id": iv["id"], "label": iv["label"]},
            "current_vs_after": iv["simulated_impact"], "planning_horizon": impact(p, z, g / 100) | {"demand_growth_pct": g},
            "throughput": {"capacity_change_pct": iv["simulated_impact"]["capacity_change_pct"]},
            "method": "BPR volume-delay model on the zone's measured peak-hour utilisation and speed; intervention effects are demo assumptions.",
            "disclaimer": "Simulated results are not predictions of actual future outcomes. " + DISCLAIMER}
