"""
Convert raw CSVs into typed, columnar Parquet under data/processed/.

Validation (validate_datasets.py) found zero affected rows across every check
run against this dataset version, so no row-level cleanup/imputation happens
here — this step is purely type normalization + a stable on-disk format for
fast repeated loading (Parquet vs re-parsing 200MB+ of CSV every run).

If a future dataset revision DOES have validation findings, extend the
per-file transform below and document the before/after counts here.

Reproducible/idempotent: always overwrites data/processed/*.parquet from
data/raw/*.csv; never mutates data/raw.
"""
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
PROCESSED = ROOT / "data" / "processed"
PROCESSED.mkdir(parents=True, exist_ok=True)

# file -> explicit timestamp columns to parse (DuckDB read_csv_auto already
# infers most types correctly since the source data is clean; only timestamp
# columns need forcing because a couple of files have them alongside plain
# numeric/text columns that must NOT be reinterpreted).
TIMESTAMP_COLUMNS = {
    "traffic_train.csv": ["timestamp"],
    "traffic_validation.csv": ["timestamp"],
    "forecast_targets_train.csv": ["timestamp"],
    "forecast_targets_validation.csv": ["timestamp"],
    "context_train.csv": ["timestamp"],
    "context_validation.csv": ["timestamp"],
    "incidents_train.csv": ["start_time", "end_time"],
    "incidents_validation.csv": ["start_time", "end_time"],
    "roadworks_train.csv": ["start_time", "end_time"],
    "roadworks_validation.csv": ["start_time", "end_time"],
    "scenario_examples.csv": ["start_time", "end_time"],
}

ALL_CSVS = sorted(RAW.glob("*.csv"))


def main():
    con = duckdb.connect()
    for path in ALL_CSVS:
        out = PROCESSED / (path.stem + ".parquet")
        ts_cols = TIMESTAMP_COLUMNS.get(path.name, [])
        if ts_cols:
            select_cols = f"* REPLACE (" + ", ".join(
                f'CAST("{c}" AS TIMESTAMP) AS "{c}"' for c in ts_cols
            ) + ")"
        else:
            select_cols = "*"
        con.execute(
            f"""
            COPY (
                SELECT {select_cols}
                FROM read_csv_auto('{path.as_posix()}', SAMPLE_SIZE=-1)
            ) TO '{out.as_posix()}' (FORMAT PARQUET, COMPRESSION ZSTD, OVERWRITE_OR_IGNORE)
            """
        )
        print(f"{path.name} -> {out.name}")

    print(f"\nDone. {len(ALL_CSVS)} files written to {PROCESSED}")


if __name__ == "__main__":
    main()
