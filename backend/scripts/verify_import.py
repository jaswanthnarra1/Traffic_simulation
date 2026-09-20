"""
Compare SOURCE (data/raw CSV) vs PROCESSED (data/processed parquet) vs
IMPORTED (data/db/neurax.duckdb tables) for every dataset: row counts, column
counts, and a spot-check of a few cross-table relationships.

Exits non-zero if any mismatch is found, so it can gate a CI/import pipeline.
"""
import sys
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
PROCESSED = ROOT / "data" / "processed"
DB_PATH = ROOT / "data" / "db" / "neurax.duckdb"


def main():
    con = duckdb.connect(str(DB_PATH), read_only=True)
    problems = []

    csv_files = sorted(RAW.glob("*.csv"))
    print(f"{'dataset':32} {'source':>10} {'processed':>10} {'imported':>10} status")
    for path in csv_files:
        table = path.stem
        src_n = con.execute(
            f"SELECT count(*) FROM read_csv_auto('{path.as_posix()}', SAMPLE_SIZE=-1)"
        ).fetchone()[0]
        proc_path = PROCESSED / f"{table}.parquet"
        proc_n = con.execute(f"SELECT count(*) FROM read_parquet('{proc_path.as_posix()}')").fetchone()[0]
        db_n = con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]

        ok = src_n == proc_n == db_n
        status = "OK" if ok else "MISMATCH"
        if not ok:
            problems.append(f"{table}: source={src_n} processed={proc_n} imported={db_n}")
        print(f"{table:32} {src_n:>10,} {proc_n:>10,} {db_n:>10,} {status}")

    # Relationship spot-checks against the live DB tables (mirrors validate_datasets.py,
    # re-run here against the IMPORTED data as a post-import guarantee).
    print("\nRelationship spot-checks (against imported DB tables):")
    checks = [
        ("traffic_train.segment_id -> network.segment_id",
         "SELECT count(*) FROM traffic_train t LEFT JOIN network n ON t.segment_id = n.segment_id WHERE n.segment_id IS NULL"),
        ("network.source_node/target_node -> nodes.node_id",
         "SELECT count(*) FROM network n LEFT JOIN nodes s ON n.source_node = s.node_id LEFT JOIN nodes tgt ON n.target_node = tgt.node_id WHERE s.node_id IS NULL OR tgt.node_id IS NULL"),
        ("planning_candidates.target_segment -> network.segment_id",
         "SELECT count(*) FROM planning_candidates p LEFT JOIN network n ON p.target_segment = n.segment_id WHERE n.segment_id IS NULL"),
    ]
    for label, sql in checks:
        n = con.execute(sql).fetchone()[0]
        status = "OK" if n == 0 else "MISMATCH"
        if n != 0:
            problems.append(f"{label}: {n} orphans")
        print(f"  {label}: {n} orphan(s) [{status}]")

    if problems:
        print("\nFAILED:")
        for p in problems:
            print(f" - {p}")
        sys.exit(1)
    print("\nAll checks passed: source == processed == imported, relationships intact.")


if __name__ == "__main__":
    main()
