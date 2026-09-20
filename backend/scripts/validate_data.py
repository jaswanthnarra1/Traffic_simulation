"""Validate raw data (existing relational/sanity checks) and record SHA-256 of every raw file.

The checksum file is what proves the organizer data is never modified (tests/test_leakage.py re-hashes).
"""
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import config as C  # noqa: E402
import validate_datasets  # noqa: E402


def checksums() -> dict:
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(C.RAW.iterdir()) if p.is_file()}


def main():
    validate_datasets.main()
    out = C.META / "raw_checksums.json"
    if out.exists() and "--refresh-checksums" not in sys.argv:
        old = json.loads(out.read_text())
        changed = [k for k, v in checksums().items() if old.get(k) != v]
        print("raw files unchanged since checksum snapshot" if not changed else f"RAW FILES CHANGED: {changed}")
        sys.exit(1 if changed else 0)
    out.write_text(json.dumps(checksums(), indent=2))
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
