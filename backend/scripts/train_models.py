"""Fit everything that is learned or calibrated — on TRAINING data only.

1. detector baseline (segment x hour x daytype), incident/roadwork windows excluded
2. detector variant + threshold chosen by slot-level F1 on incidents_train
3. incident-type centroids (incidents_train)
4. independent recurring-bottleneck detector (traffic_train; organizer flag NOT read)
5. propagation decay/lag (incidents_train)
6. 12 LightGBM forecasters + residual bands (days 1-12 fit, 13-15 calibration)
"""
import json
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import config as C, data, detection as D, forecasting, propagation  # noqa: E402


def prf(pred, truth):
    tp = int((pred & truth).sum()); fp = int((pred & ~truth).sum()); fn = int((~pred & truth).sum())
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    return {"precision": p, "recall": r, "f1": 2 * p * r / (p + r) if p + r else 0.0, "tp": tp, "fp": fp, "fn": fn}


def main():
    t0 = time.time()
    p = data.full_panel()
    ptr = data.slice_panel(p, C.TRAIN_START, C.TRAIN_END)
    inc, rw = data.incidents("train"), data.table("roadworks_train")
    inc_m, rw_m = D.label_mask(ptr, inc), D.label_mask(ptr, rw)
    excl = inc_m | rw_m

    print("[1] detector baseline")
    base = D.fit_baseline(ptr, excl)
    c = D.components(ptr, base)

    print("[2] detector calibration on incidents_train")
    scored = ~rw_m  # roadwork slots are explained slowdowns: excluded from P/R, not counted as FP
    results = {}
    for v in D.VARIANTS:
        s = D.score_variant(c, v)
        grid = np.unique(np.quantile(s[scored], np.linspace(0.95, 0.99995, 300)))
        best = max(({"threshold": float(t), **prf((s >= t) & scored, inc_m & scored)} for t in grid),
                   key=lambda r: r["f1"])
        results[v] = best
        print(f"   {v:18s} thr={best['threshold']:.2f}  train F1={best['f1']:.3f} P={best['precision']:.3f} R={best['recall']:.3f}")
    chosen = max(results, key=lambda k: results[k]["f1"])
    meta = {"variant": chosen, "threshold": results[chosen]["threshold"], "selection": "max slot-level F1 on incidents_train",
            "train_results": results, "positive_definition": "slot within [ceil5(start_time), end_time] of a labelled incident on that segment"}
    D.DETECTOR_META.write_text(json.dumps(meta, indent=2))

    print("[3] incident type centroids")
    D.fit_type_centroids(ptr, c, inc)

    print("[4] recurring bottleneck detector (organizer flag not used)")
    b = D.detect_recurring_bottlenecks(ptr, excl)
    print(f"   detected {sum(r['detected'] for r in b['segments'])} segments")

    print("[5] propagation calibration")
    pr = propagation.calibrate(ptr, c, inc)
    print(f"   decay {pr['decay']}  lag {pr['lag_min_per_hop']}")

    print("[6] forecasters")
    forecasting.train(p)
    print(f"done in {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
