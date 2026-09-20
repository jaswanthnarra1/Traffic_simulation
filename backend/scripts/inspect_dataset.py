"""Inspect a candidate traffic/road dataset BEFORE importing it: schema, missing values, duplicates, and which columns look like
coordinates / timestamps / road ids / traffic fields. Nothing is imported; use the report to write a column mapping.

    python scripts/inspect_dataset.py path/to/file.csv [--mapping mapping.json]

A mapping file renames the dataset's columns to the project's (see docs/TRAFFIC_CORRIDOR_ANALYSIS.md, "Dataset format")
and is checked for missing required targets."""
import argparse
import json
import re
import sys

import pandas as pd

GUESS = {
    "latitude": r"^(lat|latitude|y)$|_lat$|^lat_", "longitude": r"^(lon|lng|long|longitude|x)$|_lon$|_lng$|^lon_",
    "timestamp": r"time|date|ts$", "road_id": r"seg|road_id|link|edge|way", "flow": r"flow|volume|count|vph",
    "speed": r"speed|kmh", "delay": r"delay", "capacity": r"capac", "lanes": r"lane",
}
REQUIRED = ["segment_id", "timestamp", "flow_vph", "speed_kmh"]      # minimum for the per-segment history the corridor analysis builds


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument("--mapping", help="JSON {their_column: project_column}")
    a = ap.parse_args()
    df = pd.read_csv(a.path, low_memory=False)
    print(f"{a.path}: {len(df):,} rows x {len(df.columns)} columns")
    print(f"duplicate rows: {int(df.duplicated().sum()):,}")
    guesses = {}
    for c in df.columns:
        kinds = [k for k, rx in GUESS.items() if re.search(rx, c.lower())]
        miss = df[c].isna().mean() * 100
        print(f"  {c:28s} {str(df[c].dtype):10s} missing {miss:5.1f}%  unique {df[c].nunique():>9,}  {('~ ' + '/'.join(kinds)) if kinds else ''}")
        for k in kinds:
            guesses.setdefault(k, []).append(c)
    for k in ("latitude", "longitude"):
        for c in guesses.get(k, []):
            if pd.api.types.is_numeric_dtype(df[c]):
                print(f"  {c}: range {df[c].min():.5f} .. {df[c].max():.5f}")
    for c in guesses.get("timestamp", [])[:1]:
        t = pd.to_datetime(df[c], errors="coerce")
        print(f"  {c}: {t.min()} .. {t.max()} ({t.isna().mean() * 100:.1f}% unparseable)")
    if a.mapping:
        m = json.load(open(a.mapping))
        target = set(m.values())
        gone = [k for k in m if k not in df.columns]
        need = [r for r in REQUIRED if r not in target]
        print(f"mapping: {len(m)} columns; missing source columns: {gone or 'none'}; required targets not mapped: {need or 'none'}")
        return 1 if gone or need else 0
    print("No mapping given: write one and re-run with --mapping to validate it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
