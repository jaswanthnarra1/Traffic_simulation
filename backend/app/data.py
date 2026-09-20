"""DuckDB access, sanitizer and the (segment x time) grid Panel every other module works on.

The raw CSVs are never touched here: reads go through the DuckDB tables built from them.
"""
from dataclasses import dataclass, field
from functools import lru_cache
import threading

import duckdb
import numpy as np
import pandas as pd

from . import config as C

VALUE_COLS = ["speed_kmh", "flow_vph", "occupancy_pct", "delay_min", "queue_length_veh", "congestion_index"]

_con = None
_lock = threading.Lock()


def q(sql: str, params=None) -> pd.DataFrame:
    """Read-only query. One shared connection, a cursor per call (DuckDB cursors are thread-safe)."""
    global _con
    with _lock:
        if _con is None:
            _con = duckdb.connect(str(C.DB_PATH), read_only=True)
        cur = _con.cursor()
    return cur.execute(sql, params or []).df()


@lru_cache(maxsize=None)
def table(name: str) -> pd.DataFrame:
    """Small static tables, cached. Name is checked against a whitelist (no SQL from callers)."""
    allowed = {"network", "nodes", "signal_plans", "turn_restrictions", "od_demand_profiles", "planning_candidates",
               "incidents_train", "incidents_validation", "roadworks_train", "roadworks_validation",
               "scenario_examples", "context_train", "context_validation"}
    if name not in allowed:
        raise ValueError(f"unknown table {name}")
    return q(f"SELECT * FROM {name}")


def network() -> pd.DataFrame:
    return table("network")


def context() -> pd.DataFrame:
    return pd.concat([table("context_train"), table("context_validation")], ignore_index=True).set_index("timestamp")


def roadworks() -> pd.DataFrame:
    return pd.concat([table("roadworks_train"), table("roadworks_validation")], ignore_index=True)


def incidents(split: str) -> pd.DataFrame:
    return table(f"incidents_{split}")


def data_range():
    return pd.Timestamp(C.TRAIN_START), pd.Timestamp(C.VAL_END)


def load_traffic(start, end) -> pd.DataFrame:
    """Long-format traffic rows in [start, end] across train+validation (both are past observations)."""
    cols = "timestamp, segment_id, " + ", ".join(VALUE_COLS) + ", sensor_quality"
    sql = (f"SELECT {cols} FROM traffic_train WHERE timestamp BETWEEN ? AND ? "
           f"UNION ALL SELECT {cols} FROM traffic_validation WHERE timestamp BETWEEN ? AND ?")
    s, e = str(pd.Timestamp(start)), str(pd.Timestamp(end))
    return q(sql, [s, e, s, e])


@dataclass
class Panel:
    segs: list
    times: pd.DatetimeIndex
    v: dict                      # col -> float32 array (S, T)
    valid: np.ndarray            # (S, T) bool: value came from an accepted original reading
    report: dict = field(default_factory=dict)

    def at(self, t) -> int:
        return int(self.times.get_loc(pd.Timestamp(t)))


def sanitize(df: pd.DataFrame, start, end) -> Panel:
    """Turn possibly-corrupted long rows into a complete, ordered grid.

    Handles the manifest's noise types: row shuffle (sort/reindex), duplicates, negatives,
    impossible values, spikes, stuck sensors, missing values. Everything is causal (uses only
    the same or earlier timestamps), so it is safe at inference time too.
    """
    net = network().set_index("segment_id")
    segs = list(net.index)
    times = pd.date_range(pd.Timestamp(start), pd.Timestamp(end), freq=f"{C.STEP_MIN}min")
    rep = {"rows_in": int(len(df))}

    df = df.dropna(subset=["timestamp", "segment_id"])
    df = df[df.segment_id.isin(net.index)]
    n0 = len(df)
    df = df.drop_duplicates(subset=["timestamp", "segment_id"], keep="first")
    rep["duplicates_removed"] = int(n0 - len(df))

    idx = pd.MultiIndex.from_product([segs, times], names=["segment_id", "timestamp"])
    wide = df.set_index(["segment_id", "timestamp"]).reindex(idx)
    S, T = len(segs), len(times)
    v = {c: wide[c].to_numpy(dtype="float32").reshape(S, T) for c in VALUE_COLS}
    sq = wide["sensor_quality"].to_numpy(dtype="float32").reshape(S, T)
    rep["missing_cells"] = int(np.isnan(v["speed_kmh"]).sum())

    ff = net["free_flow_speed_kmh"].to_numpy("float32")[:, None]
    cap = net["capacity_vph"].to_numpy("float32")[:, None]
    bad = np.zeros((S, T), bool)
    neg = np.zeros((S, T), bool)
    for c in VALUE_COLS:
        neg |= v[c] < 0
    rep["negative_cells"] = int(neg.sum())
    impossible = (v["speed_kmh"] > 1.5 * ff) | (v["occupancy_pct"] > 100) | (v["congestion_index"] > 1)
    rep["impossible_cells"] = int((impossible & ~neg).sum())
    # spikes: flow far above physical capacity, or a sudden >3x jump over the previous reading [DESIGN]
    prev_flow = np.concatenate([np.full((S, 1), np.nan, "float32"), v["flow_vph"][:, :-1]], axis=1)
    spike = (v["flow_vph"] > 2.5 * cap) | ((v["flow_vph"] > 3 * prev_flow + 300) & (v["flow_vph"] > cap))
    rep["spike_cells"] = int((spike & ~neg & ~impossible).sum())
    # stuck: identical (speed, flow, occupancy) for >= 4 consecutive readings; first reading kept [DESIGN]
    same = np.zeros((S, T), bool)
    same[:, 1:] = ((v["speed_kmh"][:, 1:] == v["speed_kmh"][:, :-1]) & (v["flow_vph"][:, 1:] == v["flow_vph"][:, :-1])
                   & (v["occupancy_pct"][:, 1:] == v["occupancy_pct"][:, :-1]))
    run = np.zeros((S, T), np.int32)
    for t in range(1, T):  # O(T) vectorised over segments
        run[:, t] = np.where(same[:, t], run[:, t - 1] + 1, 0)
    # [FACT] clean data legitimately repeats readings pinned at the generator's bounds
    # (occupancy 98 / flow 4502.2 when saturated, flow 0 when empty) — those are not stuck sensors
    at_bound = (v["occupancy_pct"] >= 97) | (v["flow_vph"] <= 0)
    stuck = (run >= 3) & ~at_bound
    rep["stuck_cells"] = int(stuck.sum())
    low_q = sq < 0.5
    bad = neg | impossible | spike | stuck | low_q
    valid = ~np.isnan(v["speed_kmh"]) & ~bad
    for c in VALUE_COLS:
        v[c] = np.where(bad | np.isnan(v[c]), np.nan, v[c]).astype("float32")
        # causal forward-fill, max 3 steps (15 min); longer gaps stay NaN = "no data" [DESIGN]
        s = pd.DataFrame(v[c].T).ffill(limit=3).to_numpy("float32").T
        v[c] = s
    rep["cells_rejected"] = int(bad.sum())
    rep["cells_unfilled"] = int(np.isnan(v["speed_kmh"]).sum())
    rep["quality_ratio"] = float(valid.mean()) if valid.size else 0.0
    return Panel(segs, times, v, valid, rep)


def load_panel(start, end) -> Panel:
    return sanitize(load_traffic(start, end), start, end)


def full_panel(rebuild: bool = False) -> Panel:
    """Sanitized train+validation grid, cached as .npz (it is the input to training, evaluation and robustness)."""
    path = C.FEATURE_CACHE / "panel_full.npz"
    if path.exists() and not rebuild:
        z = np.load(path, allow_pickle=True)
        return Panel(list(z["segs"]), pd.DatetimeIndex(z["times"]), {c: z[c] for c in VALUE_COLS}, z["valid"],
                     dict(z["report"].item()))
    p = load_panel(C.TRAIN_START, C.VAL_END)
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savez(path, segs=np.array(p.segs), times=p.times.values, valid=p.valid, report=np.array(p.report), **p.v)
    return p


def slice_panel(p: Panel, start, end) -> Panel:
    m = (p.times >= pd.Timestamp(start)) & (p.times <= pd.Timestamp(end))
    return Panel(p.segs, p.times[m], {k: a[:, m] for k, a in p.v.items()}, p.valid[:, m], p.report)


def quality_score(p: Panel, ti: int, window: int = 12) -> np.ndarray:
    """Per-segment share of accepted original readings over the last hour (<= t)."""
    lo = max(0, ti - window + 1)
    return p.valid[:, lo:ti + 1].mean(axis=1)


def active_roadworks(t) -> pd.DataFrame:
    """Roadworks active at t only: start_time <= t < end_time (RULE 4)."""
    rw = roadworks()
    t = pd.Timestamp(t)
    return rw[(rw.start_time <= t) & (t < rw.end_time)]


def floor_time(t) -> pd.Timestamp:
    t = pd.Timestamp(t).floor(f"{C.STEP_MIN}min")
    lo, hi = data_range()
    if not (lo <= t <= hi):
        raise ValueError(f"time {t} outside data range {lo} .. {hi}")
    return t
