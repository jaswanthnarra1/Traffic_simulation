"""Multi-horizon forecaster: LightGBM on Δ(target − now) per metric x horizon, with a persistence baseline.

Training labels come from `features.future()` on traffic_train (allowed: features stay <= t).
Uncertainty = empirical residual quantiles on a held-out calibration window (days 13-15), split by
current traffic state. It is an empirical band, not a calibrated probabilistic forecast.
"""
import hashlib
import json
from datetime import datetime, timezone
from functools import lru_cache

import joblib
import lightgbm as lgb
import numpy as np
import pandas as pd

from . import config as C
from .data import Panel, network
from .features import build_features, feature_names, future, to_matrix

PARAMS = dict(n_estimators=300, learning_rate=0.05, num_leaves=63, min_child_samples=50, subsample=0.8,
              subsample_freq=1, colsample_bytree=0.8, random_state=42, verbose=-1, n_jobs=-1)


def model_path(metric, hmin):
    return C.MODELS / f"forecast_{metric}_{hmin}m_{C.MODEL_VERSION}.joblib"


def data_version() -> str:
    p = C.META / "raw_checksums.json"
    return hashlib.sha256(p.read_bytes()).hexdigest()[:12] if p.exists() else "unknown"


def _bucket(sr):
    return np.where(sr < C.STATE_THRESHOLDS[0][1], "congested", "normal")


def train(p: Panel, log=print) -> dict:
    names = feature_names()
    fit_cols = np.nonzero(p.times <= pd.Timestamp(C.FIT_END))[0][::3]
    cal_cols = np.nonzero((p.times >= pd.Timestamp(C.CALIB_START)) & (p.times <= pd.Timestamp(C.TRAIN_END)))[0][::3]
    summary = {}
    for hmin, h in C.HORIZONS.items():
        f = build_features(p, h)
        Xf, Xc = to_matrix(f, names, fit_cols), to_matrix(f, names, cal_cols)
        sr_c = (f["sr_now"][:, cal_cols]).reshape(-1)
        for metric, col in C.METRICS_FC.items():
            a = p.v[col]
            y = future(a, h) - a
            # labels must stay inside the training period: drop fit rows whose target crosses FIT_END
            yf = y[:, fit_cols].reshape(-1)
            in_fit = (p.times[fit_cols] + pd.Timedelta(minutes=hmin)) <= pd.Timestamp(C.FIT_END)
            ok = np.isfinite(yf) & np.tile(in_fit, len(p.segs))  # rows are segment-major
            m = lgb.LGBMRegressor(**PARAMS).fit(Xf[ok], yf[ok])
            yc = y[:, cal_cols].reshape(-1)
            in_cal = (p.times[cal_cols] + pd.Timedelta(minutes=hmin)) <= pd.Timestamp(C.TRAIN_END)
            okc = np.isfinite(yc) & np.tile(in_cal, len(p.segs))
            res = yc[okc] - m.predict(Xc[okc])
            bands = {}
            for b in ("normal", "congested"):
                rb = res[_bucket(sr_c[okc]) == b]
                if len(rb) < 50:
                    rb = res
                bands[b] = {"p10": float(np.quantile(rb, 0.1)), "p90": float(np.quantile(rb, 0.9)), "n": int(len(rb))}
            meta = {"metric": metric, "horizon_min": hmin, "features": names, "params": PARAMS,
                    "target": f"{col}(t+{hmin}m) - {col}(t)", "trained_at": datetime.now(timezone.utc).isoformat(),
                    "fit_window": [str(p.times[fit_cols[0]]), C.FIT_END], "calibration_window": [C.CALIB_START, C.TRAIN_END],
                    "rows_fit": int(ok.sum()), "residual_bands": bands, "model_version": C.MODEL_VERSION,
                    "data_version": data_version(),
                    "calibration_mae": float(np.mean(np.abs(res)))}
            joblib.dump({"model": m, "meta": meta}, model_path(metric, hmin))
            summary[f"{metric}_{hmin}m"] = {"rows_fit": meta["rows_fit"], "calibration_mae": round(meta["calibration_mae"], 4)}
            log(f"  {metric} +{hmin}m  rows={ok.sum():,}  calib MAE={meta['calibration_mae']:.4f}")
    (C.META / "forecast_models.json").write_text(json.dumps(summary, indent=2))
    return summary


@lru_cache(maxsize=None)
def load(metric, hmin):
    path = model_path(metric, hmin)
    if not path.exists():
        raise FileNotFoundError(f"model artifact missing: {path.name} — run scripts/train_models.py")
    obj = joblib.load(path)
    if obj["meta"]["features"] != feature_names():
        raise RuntimeError(f"{path.name} was trained on a different feature schema — retrain")
    return obj


def models_available() -> bool:
    return all(model_path(m, h).exists() for m in C.METRICS_FC for h in C.HORIZONS)


def _clip(metric, v, ff):
    if metric == "speed":
        return np.clip(v, 0, ff)
    if metric == "flow":
        return np.clip(v, 0, None)
    return np.clip(v, 0, 1)


def predict(p: Panel, cols) -> dict:
    """Forecasts for all segments at time columns `cols`. Returns {(metric, hmin): dict(p50, p10, p90, now)}."""
    ff = network().free_flow_speed_kmh.to_numpy("float32")
    ff_rows = np.repeat(ff, len(np.atleast_1d(cols)))
    out = {}
    for hmin, h in C.HORIZONS.items():
        f = build_features(p, h)
        X = to_matrix(f, feature_names(), cols)
        b = _bucket(f["sr_now"][:, cols].reshape(-1))
        for metric, col in C.METRICS_FC.items():
            obj = load(metric, hmin)
            now = p.v[col][:, cols].reshape(-1)
            p50 = now + obj["model"].predict(X)
            bands = obj["meta"]["residual_bands"]
            lo = p50 + np.where(b == "congested", bands["congested"]["p10"], bands["normal"]["p10"])
            hi = p50 + np.where(b == "congested", bands["congested"]["p90"], bands["normal"]["p90"])
            out[(metric, hmin)] = {"p50": _clip(metric, p50, ff_rows), "p10": _clip(metric, lo, ff_rows),
                                   "p90": _clip(metric, hi, ff_rows), "now": now}
    return out
