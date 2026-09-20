"""Network flow simulation: static equilibrium assignment + pivot-point on observed flows.

1. Assignment: BPR volume-delay (+ Webster uniform delay on signalized links), Frank-Wolfe with a
   bisection line search, on the line graph (node = segment) so turn restrictions are enforced as
   transitions (prev segment -> next segment) — a restricted turn never deletes a segment.
2. Pivot point: the static OD model alone reproduces observed link flows poorly (see EVALUATION_REPORT),
   so a scenario's flow = observed flow at t + (assigned scenario flow - assigned reference flow).
   The model supplies only the *change*; the level is anchored on what the sensors report at t.

Modelling assumptions, all [DESIGN] unless tagged:
- Demand = od_demand_profiles.base_demand_vph (static) x one scale factor calibrated so assigned veh-km
  equals observed veh-km at t (raw OD totals are ~5x observed flow).
- Capacity = capacity_vph x peak_capacity_factor during PEAK_HOURS [INF: factor semantics undocumented].
- Every planning candidate (incl. signal_retiming, connector, turn_lane) acts as +capacity_delta_vph on its
  target segment — the only effect the candidate file specifies.
- An incident is a capacity multiplier on its segment, floored at 10% so the network stays valid.
Outputs are SIMULATED ESTIMATES, not verified outcomes.
"""
from functools import lru_cache

import numpy as np
import pandas as pd
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import dijkstra

from . import config as C
from .data import network, table
from .graph import seg_index, turn_pairs

EPS = 1e-9
FW_ITERS, FW_TOL = 80, 1e-4


class Assignment:
    def __init__(self, peak: bool):
        net = network()
        nodes = sorted(table("nodes").node_id)
        self.peak = peak
        self.S, self.N = len(net), len(nodes)
        nid = {n: i for i, n in enumerate(nodes)}
        self.seg = list(net.segment_id)
        self.length = net.length_km.to_numpy(float)
        self.fft = self.length / net.free_flow_speed_kmh.to_numpy(float)  # hours
        self.cap0 = net.capacity_vph.to_numpy(float) * (net.peak_capacity_factor.to_numpy(float) if peak else 1.0)
        sp = table("signal_plans").set_index("signal_id")
        self.cycle = net.signal_id.map(sp.cycle_s).fillna(0).to_numpy(float)
        self.green = net.signal_id.map(sp.green_ratio).fillna(1).to_numpy(float)
        src = net.source_node.map(nid).to_numpy()
        tgt = net.target_node.map(nid).to_numpy()
        S, N = self.S, self.N
        rows, cols = [], []
        for a, b in turn_pairs("peak" if peak else "offpeak"):
            rows.append(a), cols.append(b)
        for s in range(S):
            rows.append(S + src[s]), cols.append(s)          # origin supernode -> first segment
            rows.append(s), cols.append(S + N + tgt[s])      # last segment -> destination supernode
        self.rows, self.cols = np.array(rows), np.array(cols)
        self.n_nodes = S + 2 * N
        od = table("od_demand_profiles")
        self.od_o = od.origin_node.map(nid).to_numpy()
        self.od_d = od.destination_node.map(nid).to_numpy()
        self.od_q = od.base_demand_vph.to_numpy(float)
        self.origins = np.unique(self.od_o)

    def link_time(self, v, cap):
        x = np.maximum(v, 0) / cap
        bpr = self.fft * (1 + C.BPR_ALPHA * x ** C.BPR_BETA)
        g = self.green
        webster = np.where(self.cycle > 0, 0.5 * self.cycle * (1 - g) ** 2 / (1 - np.minimum(x, 0.95) * g), 0) / 3600
        return bpr + webster

    def aon(self, cost, demand):
        """All-or-nothing load on current shortest legal paths. Returns (link flows, paths per OD)."""
        ext = np.concatenate([cost, np.full(2 * self.N, EPS)])
        g = csr_matrix((ext[self.cols] + EPS, (self.rows, self.cols)), shape=(self.n_nodes, self.n_nodes))
        dist, pred = dijkstra(g, indices=self.S + self.origins, return_predecessors=True)
        row_of = {o: i for i, o in enumerate(self.origins)}
        flow = np.zeros(self.S)
        paths = []
        for o, d, q in zip(self.od_o, self.od_d, demand):
            r = row_of[o]
            node = self.S + self.N + d
            if not np.isfinite(dist[r, node]):
                raise RuntimeError(f"OD {o}->{d} disconnected — invalid network state")
            path = []
            node = pred[r, node]
            while node < self.S:
                path.append(node)
                node = pred[r, node]
            path.reverse()
            flow[path] += q
            paths.append(path)
        return flow, paths

    def capacity(self, cap_mult=None, cap_delta=None):
        cap = self.cap0.copy()
        idx = seg_index()
        for s, m in (cap_mult or {}).items():
            cap[idx[s]] *= max(m, 1 - C.MAX_CAPACITY_REDUCTION)
        for s, d in (cap_delta or {}).items():
            cap[idx[s]] += d
        if (cap <= 0).any():
            raise ValueError("capacity must stay positive")
        return cap

    def equilibrium(self, demand, cap, x0=None):
        """Frank-Wolfe; line search = bisection on d·t(x + λd) (needs only the cost function)."""
        x = self.aon(self.fft, demand)[0] if x0 is None else x0.copy()
        gap, paths = None, None
        for _ in range(FW_ITERS):
            t = self.link_time(x, cap)
            y, paths = self.aon(t, demand)
            gap = float((x @ t - y @ t) / max(x @ t, EPS))
            if gap < FW_TOL:
                break
            d = y - x
            if d @ self.link_time(x + d, cap) <= 0:
                lam = 1.0
            else:
                lo, hi = 0.0, 1.0
                for _ in range(25):
                    mid = (lo + hi) / 2
                    lo, hi = (lo, mid) if d @ self.link_time(x + mid * d, cap) > 0 else (mid, hi)
                lam = (lo + hi) / 2
            x = x + lam * d
        return x, gap, paths


@lru_cache(maxsize=2)
def model(peak: bool) -> Assignment:
    return Assignment(peak)


def demand_scale(observed_flow: np.ndarray, peak: bool) -> float:
    """Scale OD demand so assigned veh-km matches observed veh-km at the simulated time."""
    a = model(peak)
    v, _ = a.aon(a.fft, a.od_q)
    obs = np.nan_to_num(observed_flow) @ a.length
    return float(obs / max(v @ a.length, EPS))


_ref_cache: dict = {}


def _reference(peak, scale, ref_mult):
    key = (peak, round(scale, 4), tuple(sorted(ref_mult.items())))
    if key not in _ref_cache:
        a = model(peak)
        _ref_cache[key] = a.equilibrium(a.od_q * scale, a.capacity(ref_mult))
        if len(_ref_cache) > 64:
            _ref_cache.pop(next(iter(_ref_cache)))
    return _ref_cache[key]


def scenario(hour: int, observed_flow: np.ndarray, ref_mult: dict | None = None,
             cap_mult: dict | None = None, cap_delta: dict | None = None) -> dict:
    """Pivot-point scenario. ref_mult describes the *current* network (e.g. an ongoing incident already
    reflected in observed flows); cap_mult / cap_delta are the scenario's changes on top of it."""
    peak = hour in C.PEAK_HOURS
    a = model(peak)
    ref_mult = ref_mult or {}
    obs = np.nan_to_num(np.asarray(observed_flow, float))
    scale = demand_scale(obs, peak)
    x_ref, gap_ref, _ = _reference(peak, scale, ref_mult)
    mult = {**ref_mult, **(cap_mult or {})}
    cap = a.capacity(mult, cap_delta)
    if cap_mult or cap_delta:
        x, gap, paths = a.equilibrium(a.od_q * scale, cap, x0=x_ref)
    else:
        x, gap, paths = x_ref, gap_ref, None
    flow = np.maximum(obs + (x - x_ref), 0)
    t = a.link_time(flow, cap)
    vc = flow / cap
    ci = 1 - a.fft / t
    kpis = {
        "total_travel_time_veh_h": float(flow @ t),
        "total_delay_veh_h": float(flow @ (t - a.fft)),
        "avg_speed_kmh": float((flow @ a.length) / max(flow @ t, EPS)),
        "avg_congestion_index": float((flow @ ci) / max(flow.sum(), EPS)),
        "over_capacity_links": int((vc > 1).sum()),
        "vehicles_on_over_capacity_links_vph": float(flow[vc > 1].sum()),
    }
    return {"flow": flow, "time": t, "cap": cap, "vc": vc, "kpis": kpis, "paths": paths,
            "demand_scale": scale, "relative_gap": gap, "reference_gap": gap_ref}


def compare(base: dict, cf: dict, focus: list | None = None) -> dict:
    """Before/after: absolute and percent change for each KPI, plus per-segment effects.
    A network-level change smaller than the equilibrium's own convergence gap is flagged as within tolerance."""
    kb, kc = base["kpis"], cf["kpis"]
    k = {}
    for name in kb:
        a, b = kb[name], kc[name]
        k[name] = {"baseline": round(a, 4), "counterfactual": round(b, 4), "abs_change": round(b - a, 4),
                   "pct_change": round((b - a) / a * 100, 3) if a else None}
    # equilibrium gap bounds how far each run is from its own optimum, in travel-time units
    tol_veh_h = max(base["relative_gap"] or 0, cf["relative_gap"] or 0) * kb["total_travel_time_veh_h"]
    segs = network().segment_id.to_numpy()
    dt = (cf["time"] - base["time"]) * 60  # minutes per vehicle
    rel = dt / (base["time"] * 60)
    improved, worsened = rel < -0.01, rel > 0.01
    per = pd.DataFrame({"segment_id": segs, "time_before_min": base["time"] * 60, "time_after_min": cf["time"] * 60,
                        "flow_before": base["flow"], "flow_after": cf["flow"],
                        "vc_before": base["vc"], "vc_after": cf["vc"], "delta_time_min": dt})
    out ={"kpis": k, "segments_improved": int(improved.sum()), "segments_worsened": int(worsened.sum()),
           "worst_side_effects": per[worsened].sort_values("delta_time_min", ascending=False).head(5).round(3).to_dict("records"),
           "largest_improvements": per[improved].sort_values("delta_time_min").head(5).round(3).to_dict("records"),
           "per_segment": per.round(4).to_dict("records"),
           "convergence": {"baseline_gap": base["relative_gap"], "counterfactual_gap": cf["relative_gap"],
                           "tolerance_veh_h": round(tol_veh_h, 3),
                           "network_change_within_tolerance": abs(k["total_travel_time_veh_h"]["abs_change"]) < tol_veh_h}}
    if focus:
        out["focus"] = per[per.segment_id.isin(focus)].round(3).to_dict("records")
    return out
