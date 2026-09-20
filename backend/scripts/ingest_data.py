"""raw CSV -> typed Parquet -> DuckDB -> parity check (runs the existing, verified pipeline scripts in order)."""
import runpy
from pathlib import Path

HERE = Path(__file__).resolve().parent

for step in ("preprocess_datasets.py", "import_datasets.py", "verify_import.py"):
    print(f"== {step}")
    runpy.run_path(str(HERE / step), run_name="__main__")
