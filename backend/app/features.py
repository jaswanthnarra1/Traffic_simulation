"""Leakage-safe feature builder.

Everything operates on the complete (S x T) grid from data.sanitize, so shifting by k columns is
exactly k x 5 minutes. Feature code only ever shifts *backwards* (`_lag`, trailing `_roll`).
The single forward shift in the codebase is `future()`, used to build training labels and never
called from `build_features`. forecast_targets_* are not read anywhere in this module.
"""
import numpy as np
import pandas as pd

from . import config as C
from .data import Panel, context, network
from .graph import neighbor_matrices


def _lag(a: np.ndarray, k: int) -> np.ndarray:
    """Value k steps in the past (k >= 0)."""
    assert k >= 0, "features may only look backwards"
    if k == 0:
        return a
    out = np.full_like(a, np.nan)
    out[:, k:] = a[:, :-k]
    return out


def _roll(a: np.ndarray, w: int) -> np.ndarray:
    """Trailing mean over [t-w+1, t] — pandas rolling is backward-looking."""
    return pd.DataFrame(a.T).rolling(w, min_periods=1).mean().to_numpy("float32").T


def future(a: np.ndarray, k: int) -> np.ndarray:
    """LABELS ONLY: value k steps ahead. Never used for features."""
    out = np.full_like(a, np.nan)
    out[:, :-k] = a[:, k:]
    return out


def static_features() -> pd.DataFrame:
    net = network()
    return pd.DataFrame({
        "arterial": (net.road_class == "arterial").astype("float32"),
        "lanes": net.lanes, "capacity": net.capacity_vph, "ff_speed": net.free_flow_speed_kmh,
        "length": net.length_km, "grade": net.grade_pct, "signalized": net.signal_id.notna().astype("float32"),
        "importance": net.importance,
    }).astype("float32")


def build_features(p: Panel, horizon_steps: int | None = None) -> dict:
    """Return {name: (S, T) array}. horizon_steps adds the 'same slot yesterday' features for that horizon
    (value at t + h - 1 day, which is strictly before t)."""
    S, T = p.v["speed_kmh"].shape
    net = network()
    ff = net.free_flow_speed_kmh.to_numpy("float32")[:, None]
    cap = net.capacity_vph.to_numpy("float32")[:, None]
    sr = p.v["speed_kmh"] / ff
    base = {"speed": p.v["speed_kmh"], "flow": p.v["flow_vph"], "congestion": p.v["congestion_index"]}
    f = {}
    for name, a in base.items():
        f[f"{name}_now"] = a
        for k in (1, 3, 6, 12):
            f[f"{name}_lag{k}"] = _lag(a, k)
        for w in (3, 6, 12):
            f[f"{name}_roll{w}"] = _roll(a, w)
        f[f"{name}_slope3"] = a - _lag(a, 3)
        if horizon_steps:
            f[f"{name}_yday_at_target"] = _lag(a, C.SLOTS_PER_DAY - horizon_steps)
    f["occupancy_now"] = p.v["occupancy_pct"]
    f["queue_now"] = p.v["queue_length_veh"]
    f["queue_slope3"] = p.v["queue_length_veh"] - _lag(p.v["queue_length_veh"], 3)
    f["delay_now"] = p.v["delay_min"]
    f["vc_now"] = p.v["flow_vph"] / cap
    f["sr_now"] = sr

    # neighbor state at t (same timestamp — known at t)
    up, dn = neighbor_matrices()
    ci = np.nan_to_num(p.v["congestion_index"], nan=0.0)
    srz = np.nan_to_num(sr, nan=1.0)
    f["up_congestion"] = up @ ci
    f["down_congestion"] = dn @ ci
    f["up_sr"] = up @ srz
    f["down_sr"] = dn @ srz
    f["up_flow"] = up @ np.nan_to_num(p.v["flow_vph"], nan=0.0)

    # time + context at t (observed context, same timestamp)
    ctx = context().reindex(p.times)
    tt = p.times
    hour = (tt.hour + tt.minute / 60).to_numpy("float32")
    row = lambda x: np.broadcast_to(np.asarray(x, "float32")[None, :], (S, T))
    f["hour"] = row(hour)
    f["hour_sin"], f["hour_cos"] = row(np.sin(2 * np.pi * hour / 24)), row(np.cos(2 * np.pi * hour / 24))
    if horizon_steps:
        th = hour + horizon_steps * C.STEP_MIN / 60
        f["target_hour_sin"], f["target_hour_cos"] = row(np.sin(2 * np.pi * th / 24)), row(np.cos(2 * np.pi * th / 24))
    f["dow"] = row(tt.dayofweek)
    f["weekend"] = row(tt.dayofweek >= 5)
    f["holiday"] = row(ctx.holiday_flag.fillna(0))
    f["rain"] = row(ctx.rain_intensity.fillna(0))
    f["temperature"] = row(ctx.temperature_c.fillna(ctx.temperature_c.mean()))
    f["event_present"] = row(ctx.event_level.fillna(0) > 0)  # event_id / level semantics not used (RULE 5)

    st = static_features()
    for c in st.columns:
        f[c] = np.broadcast_to(st[c].to_numpy("float32")[:, None], (S, T))
    return f


def feature_names() -> list:
    """Stable model feature order (tests assert it equals build_features(p, h).keys())."""
    base = [f"{m}_{s}" for m in ("speed", "flow", "congestion")
            for s in ("now", "lag1", "lag3", "lag6", "lag12", "roll3", "roll6", "roll12", "slope3", "yday_at_target")]
    return base + ["occupancy_now", "queue_now", "queue_slope3", "delay_now", "vc_now", "sr_now",
                   "up_congestion", "down_congestion", "up_sr", "down_sr", "up_flow",
                   "hour", "hour_sin", "hour_cos", "target_hour_sin", "target_hour_cos", "dow", "weekend", "holiday",
                   "rain", "temperature", "event_present",
                   "arterial", "lanes", "capacity", "ff_speed", "length", "grade", "signalized", "importance"]


def to_matrix(f: dict, names: list, cols: np.ndarray | slice) -> np.ndarray:
    """Stack selected time columns into a (rows, features) float32 matrix, segment-major."""
    return np.stack([np.asarray(f[n])[:, cols].reshape(-1) for n in names], axis=1).astype("float32")
