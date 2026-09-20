"""Import the organizer's HIDDEN TEST INPUT (NEURAX_SMART_CITIES_TESTING_NOISY_V2) alongside the training data.

    python scripts/import_test_data.py "<path to NEURAX_SMART_CITIES_TESTING_NOISY_V2>"
    python scripts/import_test_data.py            # re-import from data/raw_test

This is an ADDITIVE import. The training/validation tables are never touched, because:
  - the hidden test input carries NO labels (no incidents_*, no forecast_targets_*), so nothing can be trained or
    scored on it, and
  - every model, baseline and threshold in artifacts/ was fitted on the training split; deleting that split would
    leave the shipped models unreproducible.

Leakage stays safe by construction: data.full_panel() (the input to training and evaluation) is bounded to
TRAIN_START..VAL_END, so test rows can never reach a fit. Only runtime analysis reads them.

raw CSV -> data/raw_test/ -> data/processed/*_test.parquet -> DuckDB tables (traffic_test, context_test,
roadworks_test, evaluation_windows). Idempotent: every table is CREATE OR REPLACE.
"""
import shutil
import sys
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]
RAW_TEST = ROOT / "data" / "raw_test"
PROCESSED = ROOT / "data" / "processed"
DB_PATH = ROOT / "data" / "db" / "neurax.duckdb"

# source file -> (table name, timestamp columns). Files shared with training (network, nodes, signal_plans,
# turn_restrictions, od_demand_profiles, planning_candidates) are verified identical instead of re-imported.
TABLES = {
    "traffic_input.csv": ("traffic_test", ["timestamp"]),
    "context.csv": ("context_test", ["timestamp"]),
    "roadworks.csv": ("roadworks_test", ["start_time", "end_time"]),
    "evaluation_windows.csv": ("evaluation_windows", ["window_start", "window_end"]),
}
SHARED = ["network.csv", "nodes.csv", "signal_plans.csv", "turn_restrictions.csv", "od_demand_profiles.csv", "planning_candidates.csv"]


def stage(source: Path) -> None:
    """Copy the organizer CSVs into data/raw_test (gitignored, like data/raw). The source is never modified."""
    missing = [f for f in list(TABLES) + SHARED if not (source / f).exists()]
    if missing:
        sys.exit(f"Not a NEURAX hidden-test folder: missing {', '.join(missing)}")
    RAW_TEST.mkdir(parents=True, exist_ok=True)
    for f in sorted(source.glob("*")):
        if f.is_file():
            shutil.copy2(f, RAW_TEST / f.name)
    print(f"staged {source} -> {RAW_TEST}")


def check_same_network(con: duckdb.DuckDBPyConnection) -> None:
    """The shipped models are only valid for the graph they were fitted on. Refuse a different one."""
    for f in ("network.csv", "nodes.csv"):
        csv = (RAW_TEST / f).as_posix()
        stem = f[:-4]
        diff = con.execute(
            f"SELECT (SELECT count(*) FROM (SELECT * FROM read_csv_auto('{csv}', SAMPLE_SIZE=-1) EXCEPT SELECT * FROM {stem})) "
            f"+ (SELECT count(*) FROM (SELECT * FROM {stem} EXCEPT SELECT * FROM read_csv_auto('{csv}', SAMPLE_SIZE=-1)))"
        ).fetchone()[0]
        if diff:
            sys.exit(f"ABORT: {f} differs from the trained-on network in {diff} row(s). The existing models do not apply to a different graph.")
        print(f"  {stem}: identical to the trained-on network")


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    if args:
        stage(Path(args[0]).expanduser())
    if not RAW_TEST.exists():
        sys.exit(f"No hidden test data. Pass the dataset folder: python scripts/import_test_data.py <folder>")
    if not DB_PATH.exists():
        sys.exit("No database yet. Run scripts/ingest_data.py (training data) first.")

    PROCESSED.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(DB_PATH))
    print("verifying the network matches the trained-on network")
    check_same_network(con)

    for fname, (table, ts_cols) in TABLES.items():
        csv = RAW_TEST / fname
        out = PROCESSED / f"{table}.parquet"
        replace = "* REPLACE (" + ", ".join(f'CAST("{c}" AS TIMESTAMP) AS "{c}"' for c in ts_cols) + ")"
        con.execute(f"COPY (SELECT {replace} FROM read_csv_auto('{csv.as_posix()}', SAMPLE_SIZE=-1)) "
                    f"TO '{out.as_posix()}' (FORMAT PARQUET, COMPRESSION ZSTD, OVERWRITE_OR_IGNORE)")
        con.execute(f"CREATE OR REPLACE TABLE {table} AS SELECT * FROM read_parquet('{out.as_posix()}')")
        n = con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        print(f"  {fname} -> {table}: {n:,} rows")

    lo, hi = con.execute("SELECT min(timestamp), max(timestamp) FROM traffic_test").fetchone()
    train_hi = con.execute("SELECT max(timestamp) FROM traffic_validation").fetchone()[0]
    if lo <= train_hi:
        print(f"  WARNING: test rows start at {lo}, on or before the last training/validation row {train_hi}")
    con.execute("CHECKPOINT")
    con.close()
    print(f"\nHidden test input imported: {lo} .. {hi} (training/validation tables untouched).")


if __name__ == "__main__":
    main()
