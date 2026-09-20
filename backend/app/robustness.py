"""Synthetic robustness evaluation.

The manifest's noise (missing values, duplicates, spikes, stuck sensors, negatives, row shuffle) is
absent from train/validation, so we inject it ourselves into an in-memory COPY of a held-out validation
slice (raw files are never touched) and compare:
  - sanitized pipeline (data.sanitize — what the product runs)
  - naive pipeline     (same grid, no cleaning) — shows what sanitization buys
This is a synthetic robustness evaluation; it does not claim to reproduce the hidden test noise.
"""
import numpy as np
import pandas as pd

from . import config as C, data, detection as D, forecasting

SLICE = ("2026-01-15 00:00:00", "2026-01-17 23:55:00")   # first day = feature history (train tail)
EVAL = ("2026-01-16 00:00:00", "2026-01-17 23:55:00")    # scored days (validation)
VALS = ["speed_kmh", "flow_vph", "occupancy_pct", "delay_min", "queue_length_veh", "congestion_index"]


def corrupt(df: pd.DataFrame, kind: str, rng: np.random.Generator, rate: float = 0.05) -> pd.DataFrame:
    df = df.copy()
    n = len(df)
    if kind in ("missing", "combined"):
        df = df.drop(df.index[rng.random(n) < rate])
        for col in VALS:
            df.loc[rng.random(len(df)) < rate / 2, col] = np.nan
    if kind in ("duplicates", "combined"):
        dup = df.sample(frac=rate, random_state=int(rng.integers(1e9)))
        df = pd.concat([df, dup])
    if kind in ("spikes", "combined"):
        m = rng.random(len(df)) < rate / 5
        df.loc[m, "flow_vph"] = df.loc[m, "flow_vph"] * rng.uniform(4, 8, m.sum()) + 2000
        m2 = rng.random(len(df)) < rate / 5
        df.loc[m2, "occupancy_pct"] = rng.uniform(120, 300, m2.sum())
    if kind in ("stuck", "combined"):
        segs = rng.choice(df.segment_id.unique(), size=max(1, int(0.05 * df.segment_id.nunique())), replace=False)
        times = np.sort(df.timestamp.unique())
        for s in segs:
            st = int(rng.integers(0, len(times) - 24))
            blk = (df.segment_id == s) & (df.timestamp >= times[st]) & (df.timestamp <= times[st + 23])
            first = df[blk].sort_values("timestamp").head(1)
            if len(first):
                for col in VALS:
                    df.loc[blk, col] = first[col].iloc[0]
    if kind in ("negatives", "combined"):
        for col in ("speed_kmh", "flow_vph", "occupancy_pct"):
            m = rng.random(len(df)) < rate / 3
            df.loc[m, col] = -df.loc[m, col].abs()
    if kind in ("shuffle", "combined"):
        df = df.sample(frac=1.0, random_state=int(rng.integers(1e9)))
    return df


def naive_panel(df: pd.DataFrame, start, end) -> data.Panel:
    """No cleaning: last duplicate wins, negatives/spikes/stuck values kept, gaps left empty."""
    segs = list(data.network().segment_id)
    times = pd.date_range(start, end, freq="5min")
    df = df.drop_duplicates(subset=["timestamp", "segment_id"], keep="last")
    wide = df.set_index(["segment_id", "timestamp"]).reindex(pd.MultiIndex.from_product([segs, times]))
    v = {c: wide[c].to_numpy("float32").reshape(len(segs), len(times)) for c in VALS}
    return data.Panel(segs, times, v, ~np.isnan(v["speed_kmh"]), {})


def evaluate_panel(p: data.Panel, targets: pd.DataFrame) -> dict:
    ev_cols = np.nonzero((p.times >= pd.Timestamp(EVAL[0])) & (p.times <= pd.Timestamp(EVAL[1])))[0]
    anchors = pd.DatetimeIndex(sorted(targets.timestamp.unique()))
    anchors = anchors[(anchors >= pd.Timestamp(EVAL[0])) & (anchors <= pd.Timestamp(EVAL[1]))][::4]
    cols = np.array([p.times.get_loc(a) for a in anchors])
    tg = targets.set_index(["segment_id", "timestamp"]).reindex(pd.MultiIndex.from_product([p.segs, anchors]))
    fc = forecasting.predict(p, cols)
    out = {}
    for key in (("speed", 15), ("flow", 15), ("congestion", 15), ("speed", 60), ("congestion", 60)):
        y = tg[f"target_{key[0]}_{key[1]}m"].to_numpy(float)
        yhat = fc[key]["p50"]
        ok = np.isfinite(y) & np.isfinite(yhat)
        out[f"forecast_mae_{key[0]}_{key[1]}m"] = float(np.mean(np.abs(yhat[ok] - y[ok])))
        out[f"forecast_coverage_{key[0]}_{key[1]}m"] = float(ok.mean())  # share of rows that produced a forecast
    # detection on validation incidents in the eval days
    meta = D.detector_meta()
    pe = data.slice_panel(p, *EVAL)
    inc = data.incidents("validation")
    inc = inc[inc.start_time <= pd.Timestamp(EVAL[1])]
    m = D.label_mask(pe, inc)
    rw = D.label_mask(pe, data.table("roadworks_validation"))
    c = D.components(pe, D.load_baseline())
    s = D.score_variant(c, meta["variant"])
    flag = s >= meta["threshold"]
    tp = int((flag & m & ~rw).sum()); fp = int((flag & ~m & ~rw).sum()); fn = int((~flag & m & ~rw).sum())
    pr = tp / (tp + fp) if tp + fp else 0.0
    rc = tp / (tp + fn) if tp + fn else 0.0
    out["detection_f1"] = 2 * pr * rc / (pr + rc) if pr + rc else 0.0
    out["detection_precision"], out["detection_recall"] = pr, rc
    out["false_positive_slots_per_day"] = fp / (len(pe.times) / C.SLOTS_PER_DAY)
    idx = {x: i for i, x in enumerate(pe.segs)}
    out["incidents_detected"] = f"{sum(bool(flag[idx[r.segment_id]][m[idx[r.segment_id]]].any()) for r in inc.itertuples())}/{len(inc)}"
    q = np.stack([data.quality_score(pe, i) for i in range(11, len(pe.times), 12)], axis=1)
    out["mean_data_quality"] = float(q.mean())
    conf = [float(D.confidence(s[idx[r.segment_id]][m[idx[r.segment_id]]], meta["threshold"],
                               q[idx[r.segment_id]].mean()).mean()) for r in inc.itertuples() if m[idx[r.segment_id]].any()]
    out["mean_confidence_on_incidents"] = float(np.mean(conf)) if conf else None
    return out


def run(kinds=("missing", "duplicates", "spikes", "stuck", "negatives", "shuffle", "combined"), seed=7, log=print) -> dict:
    raw = data.load_traffic(*SLICE)
    targets = data.q("SELECT * FROM forecast_targets_validation")  # labels only
    rng = np.random.default_rng(seed)
    results = {"label": "SYNTHETIC ROBUSTNESS EVALUATION (self-injected noise; not the hidden test)",
               "slice": SLICE, "scored_days": EVAL, "corruption_rate": 0.05, "seed": seed, "runs": {}}
    clean = evaluate_panel(data.sanitize(raw, *SLICE), targets)
    results["runs"]["clean"] = {"sanitized": clean}
    log(f"  clean: {clean}")
    for k in kinds:
        bad = corrupt(raw, k, rng)
        row = {"rows_after_corruption": int(len(bad))}
        for name, fn in (("sanitized", lambda d: data.sanitize(d, *SLICE)), ("naive", lambda d: naive_panel(d, *SLICE))):
            try:
                p = fn(bad)
                row[name] = evaluate_panel(p, targets)
                if name == "sanitized":
                    row["sanitizer_report"] = p.report
            except Exception as e:  # a crash is a result, recorded as a pipeline failure
                row[name] = {"pipeline_failure": f"{type(e).__name__}: {e}"}
        results["runs"][k] = row
        log(f"  {k}: sanitized F1={row['sanitized'].get('detection_f1')}, naive F1={row['naive'].get('detection_f1')}")
    return results
