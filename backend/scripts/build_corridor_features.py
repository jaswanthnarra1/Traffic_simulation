"""Precompute the per-segment historical features used by the corridor analysis, and benchmark the bottleneck score.
Run from backend/ after the data pipeline:  python scripts/build_corridor_features.py"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import corridor  # noqa: E402

corridor.STATS_PATH.unlink(missing_ok=True)
corridor.segment_stats.cache_clear()
s = corridor.segment_stats()
print(f"segment features: {len(s)} segments, {int(s.history_days.iloc[0])} history days -> {corridor.STATS_PATH}")
print(json.dumps(corridor.evaluate(), indent=2))
