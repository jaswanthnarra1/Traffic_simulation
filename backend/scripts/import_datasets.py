"""
Load data/processed/*.parquet into the project's DuckDB database
(data/db/neurax.duckdb), one table per dataset.

Idempotent by construction: every table is CREATE OR REPLACE from the parquet
file, so re-running this script any number of times produces the same table
contents, never accumulates duplicates, and requires no separate "have I
already imported this" bookkeeping.

Table naming = source filename stem (e.g. traffic_train.parquet -> table
traffic_train). No renaming games; makes the mapping in RESEARCH.md trivial
to verify.
"""
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]
PROCESSED = ROOT / "data" / "processed"
DB_DIR = ROOT / "data" / "db"
DB_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DB_DIR / "neurax.duckdb"


def main():
    con = duckdb.connect(str(DB_PATH))
    parquet_files = sorted(PROCESSED.glob("*.parquet"))
    for path in parquet_files:
        table = path.stem
        con.execute(
            f"CREATE OR REPLACE TABLE {table} AS SELECT * FROM read_parquet('{path.as_posix()}')"
        )
        n = con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        print(f"{table}: {n:,} rows")

    con.execute("CHECKPOINT")
    con.close()
    print(f"\nImported {len(parquet_files)} tables into {DB_PATH}")


if __name__ == "__main__":
    main()
