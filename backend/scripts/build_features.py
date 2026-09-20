"""Build (or rebuild with --rebuild) the sanitized train+validation panel cache used by training/evaluation."""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import data  # noqa: E402
from app.features import build_features, feature_names  # noqa: E402

t0 = time.time()
p = data.full_panel(rebuild="--rebuild" in sys.argv)
print(f"panel {len(p.segs)} segments x {len(p.times)} slots, sanitizer report {p.report} ({time.time() - t0:.1f}s)")
f = build_features(p, 3)
assert list(f) and set(f) == set(feature_names()), "feature schema drift"
print(f"{len(f)} features OK")
