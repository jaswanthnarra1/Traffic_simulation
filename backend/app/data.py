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
               "scenario_examples", "context_train", "context_validation",
               "context_test", "roadworks_test", "evaluation_windows"}   # hidden test input (optional)
    if name not in allowed:
        raise ValueError(f"unknown table {name}")
    return q(f"SELECT * FROM {name}")


@lru_cache(maxsize=1)
def has_test() -> bool:
    """True once the organizer's hidden test input is imported (scripts/import_test_data.py).

    It is unlabelled analysis-only data: it extends the observable time range, but full_panel() — the input to every
    fit and to evaluation — still stops at VAL_END, so no model can be trained or scored on it.
    """
    return not q("SELECT 1 FROM duckdb_tables() WHERE table_name = 'traffic_test' LIMIT 1").empty


def network() -> pd.DataFrame:
    return table("network")


def context() -> pd.DataFrame:
    parts = [table("context_train"), table("context_validation")] + ([table("context_test")] if has_test() else [])
    return pd.concat(parts, ignore_index=True).set_index("timestamp")


def roadworks() -> pd.DataFrame:
    parts = [table("roadworks_train"), table("roadworks_validation")] + ([table("roadworks_test")] if has_test() else [])
    return pd.concat(parts, ignore_index=True)


def incidents(split: str) -> pd.DataFrame:
    return table(f"incidents_{split}")


def data_range():
    """Observable span. Extends across the hidden test input once it is imported."""
    return pd.Timestamp(C.TRAIN_START), pd.Timestamp(C.TEST_END if has_test() else C.VAL_END)


def load_traffic(start, end) -> pd.DataFrame:
    """Long-format traffic rows in [start, end] across every observed split (all are past observations).

    The splits are disjoint in time, so the union is an ordinary concatenation: train (Jan 1-15), validation
    (Jan 16-19) and, when imported, the hidden test input (Jan 20-27).
    """
    cols = "timestamp, segment_id, " + ", ".join(VALUE_COLS) + ", sensor_quality"
    tables = ["traffic_train", "traffic_validation"] + (["traffic_test"] if has_test() else [])
    sql = " UNION ALL ".join(f"SELECT {cols} FROM {t} WHERE timestamp BETWEEN ? AND ?" for t in tables)
    s, e = str(pd.Timestamp(start)), str(pd.Timestamp(end))
    return q(sql, [s, e] * len(tables))


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


def _cached_panel(name: str, start, end, rebuild: bool = False) -> Panel:
    path = C.FEATURE_CACHE / name
    if path.exists() and not rebuild:
        z = np.load(path, allow_pickle=True)
        return Panel(list(z["segs"]), pd.DatetimeIndex(z["times"]), {c: z[c] for c in VALUE_COLS}, z["valid"],
                     dict(z["report"].item()))
    p = load_panel(start, end)
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savez(path, segs=np.array(p.segs), times=p.times.values, valid=p.valid, report=np.array(p.report), **p.v)
    return p


def full_panel(rebuild: bool = False) -> Panel:
    """Sanitized train+validation grid (the input to training, evaluation and robustness).

    Deliberately stops at VAL_END: this is the FITTING boundary. The hidden test input must never reach a fit,
    so runtime analysis uses runtime_panel() instead.
    """
    return _cached_panel("panel_full.npz", C.TRAIN_START, C.VAL_END, rebuild)


def runtime_panel(rebuild: bool = False) -> Panel:
    """Sanitized grid over everything observable, including the hidden test input once imported.

    This is what live analysis (engine/dashboard/rider portal/corridor) reads. It is never used to fit anything.
    Without the test input it is identical to full_panel(), so no second cache is written.
    """
    if not has_test():
        return full_panel(rebuild)
    return _cached_panel("panel_runtime.npz", C.TRAIN_START, C.TEST_END, rebuild)


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
