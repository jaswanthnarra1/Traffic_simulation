"""Suite-wide fixtures.

Pins the demo moment. `config.DEMO_TIME` is deployment configuration (settable in .env to point a demo at any
moment, including the hidden test period), but several tests assert on one curated scenario — the R0360 incident
at 2026-01-16 13:30 in the held-out validation split. Without this pin, changing .env silently changes what the
suite tests and those assertions fail for a reason that has nothing to do with the code.
"""
import pytest

from app import config as C

DEMO_TIME = "2026-01-16 13:30:00"


@pytest.fixture(autouse=True)
def _pin_demo_time(monkeypatch):
    monkeypatch.setattr(C, "DEMO_TIME", DEMO_TIME)
