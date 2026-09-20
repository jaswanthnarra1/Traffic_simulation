"""Leakage, validity and pipeline tests. Run from backend/: python -m pytest -q"""
import hashlib
import json
import re
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from app import config as C, data, detection as D, engine, graph, simulation
from app.features import build_features, feature_names

APP = Path(__file__).resolve().parents[1] / "app"
SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def _code(path) -> str:
    """Source without docstrings and comments, so prose that *mentions* a file doesn't count as a read."""
    s = re.sub(r'""".*?"""', "", Path(path).read_text(encoding="utf-8"), flags=re.S)
    return "\n".join(line.split("#")[0] for line in s.splitlines())


def _src(*names):
    return {n: _code(APP / n) for n in names}


# TEST 1 -------------------------------------------------------------------------------------------
def test_forecast_targets_never_in_features():
    runtime = _src("features.py", "forecasting.py", "detection.py", "engine.py", "propagation.py",
                   "recommendations.py", "simulation.py", "data.py", "graph.py")
    for name, code in runtime.items():
        assert "forecast_targets" not in code, f"{name} references forecast_targets"
    # only evaluation code reads them
    readers = {p.name for p in list(APP.glob("*.py")) + list(SCRIPTS.glob("*.py")) if "forecast_targets" in _code(p)}
    # evaluation code + the data-layer scripts that copy / profile raw files
    assert readers <= {"evaluation.py", "robustness.py", "evaluate_models.py", "run_robustness.py",
                       "validate_datasets.py", "inspect_datasets.py",
                       "dataset_intelligence_analysis.py", "verify_import.py", "preprocess_datasets.py"}, readers
    assert not [f for f in feature_names() if f.startswith("target_") and "hour" not in f]


# TEST 2 -------------------------------------------------------------------------------------------
def test_future_traffic_cannot_enter_feature_windows():
    p = engine.window("2026-01-16 13:30:00")
    k = len(p.times) - 40
    f1 = build_features(p, 12)
    rng = np.random.default_rng(0)
    p2 = data.Panel(p.segs, p.times, {c: a.copy() for c, a in p.v.items()}, p.valid, p.report)
    for a in p2.v.values():
        a[:, k + 1:] = rng.uniform(0, 5000, a[:, k + 1:].shape)  # rewrite the future
    f2 = build_features(p2, 12)
    for name in f1:
        np.testing.assert_array_equal(np.asarray(f1[name])[:, :k + 1], np.asarray(f2[name])[:, :k + 1], err_msg=name)


def test_engine_window_ends_at_t():
    p = engine.window("2026-01-17 09:00:00")
    assert p.times[-1] == pd.Timestamp("2026-01-17 09:00:00")


# TEST 3 -------------------------------------------------------------------------------------------
def test_detector_never_reads_incident_labels(monkeypatch):
    orig = data.table

    def guarded(name):
        if name.startswith("incidents") or name == "scenario_examples":
            raise AssertionError(f"runtime read {name}")
        return orig(name)

    monkeypatch.setattr(data, "table", guarded)
    monkeypatch.setattr(data, "incidents", lambda split: (_ for _ in ()).throw(AssertionError("incidents read")))
    s = engine.Snapshot("2026-01-16 13:30:00")
    s.events()
    s.diagnosis("R0360")
    s.evidence("R0360")


def test_roadworks_only_active_inside_window():
    rw = data.roadworks().iloc[0]
    assert rw.work_id in set(data.active_roadworks(rw.start_time).work_id)
    assert rw.work_id not in set(data.active_roadworks(rw.end_time).work_id)
    assert rw.work_id not in set(data.active_roadworks(rw.start_time - pd.Timedelta(minutes=5)).work_id)


def test_bottleneck_detector_does_not_read_organizer_flag():
    code = _code(APP / "detection.py")
    assert "structural_bottleneck" not in code and "peak_capacity_factor" not in code


# TEST 4 -------------------------------------------------------------------------------------------
def test_scenario_examples_not_used_for_validation():
    for f in [APP / "evaluation.py", APP / "robustness.py", SCRIPTS / "train_models.py", SCRIPTS / "evaluate_models.py"]:
        assert "scenario_examples" not in _code(f), f.name


# TEST 5 -------------------------------------------------------------------------------------------
def test_turn_restrictions_block_illegal_transitions():
    idx = graph.seg_index()
    tr = data.table("turn_restrictions")
    off, peak = set(graph.turn_pairs("offpeak")), set(graph.turn_pairs("peak"))
    for r in tr.itertuples():
        pair = (idx[r.from_segment], idx[r.to_segment])
        if r.restriction in ("no_turn", "no_left"):
            assert pair not in off and pair not in peak
        else:  # time_window: enforced at peak only [DESIGN]
            assert pair not in peak and pair in off
    r = tr[tr.restriction == "no_left"].iloc[0]
    assert not graph.path_is_legal([r.from_segment, r.to_segment])


def test_diversion_paths_are_legal_and_avoid_blocked_segment():
    for seg in ("R0360", "R0001", "R0200"):
        path = graph.legal_detour(seg, hour=8)
        assert path and seg not in path and graph.path_is_legal(path, hour=8)


def test_assigned_routes_respect_turn_restrictions():
    a = simulation.model(True)
    _, _, paths = a.equilibrium(a.od_q * 0.1, a.capacity())
    assert all(graph.path_is_legal([a.seg[i] for i in p], hour=8) for p in paths)


# TEST 6 -------------------------------------------------------------------------------------------
def test_simulation_preserves_network_validity():
    obs = engine.window("2026-01-16 08:00:00").v["flow_vph"][:, -1].astype(float)
    base = simulation.scenario(8, obs)
    inc = simulation.scenario(8, obs, {"R0360": 0.1})
    for r in (base, inc):
        assert np.isfinite(r["flow"]).all() and (r["flow"] >= 0).all()
        assert (r["time"] >= simulation.model(True).fft - 1e-12).all()
        assert (r["cap"] > 0).all()
    np.testing.assert_allclose(base["flow"], np.nan_to_num(obs))  # pivot point: reference reproduces observation
    cmp_ = simulation.compare(base, inc)
    assert cmp_["kpis"]["total_delay_veh_h"]["abs_change"] > 0  # losing capacity cannot reduce delay here
    with pytest.raises(ValueError):
        simulation.model(True).capacity(cap_delta={"R0001": -1e9})


def test_candidate_capacity_increase_helps_its_segment():
    obs = engine.window("2026-01-16 08:00:00").v["flow_vph"][:, -1].astype(float)
    base = simulation.scenario(8, obs, {"R0360": 0.5})
    cf = simulation.scenario(8, obs, {"R0360": 0.5}, cap_delta={"R0360": 900})
    i = graph.seg_index()["R0360"]
    assert cf["time"][i] <= base["time"][i]


# TEST 7 -------------------------------------------------------------------------------------------
def test_raw_datasets_unchanged():
    snap = json.loads((C.META / "raw_checksums.json").read_text())
    now = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in C.RAW.iterdir() if p.is_file()}
    assert now == snap


# sanitizer ------------------------------------------------------------------------------------------
def test_sanitizer_recovers_clean_grid_from_shuffle_duplicates_negatives():
    start, end = "2026-01-16 08:00:00", "2026-01-16 09:00:00"
    raw = data.load_traffic(start, end)
    clean = data.sanitize(raw, start, end)
    bad = pd.concat([raw, raw.sample(200, random_state=1)]).sample(frac=1, random_state=2)
    bad.loc[bad.index[:50], "speed_kmh"] = -5.0  # negatives on some rows (and their duplicates)
    got = data.sanitize(bad, start, end)
    assert got.report["duplicates_removed"] == 200
    assert got.report["negative_cells"] > 0
    assert np.nanmin(got.v["speed_kmh"]) >= 0
    same = np.isclose(got.v["flow_vph"], clean.v["flow_vph"], equal_nan=True)
    assert same.mean() > 0.99


def test_sanitizer_rejects_stuck_runs_but_not_saturation():
    start, end = "2026-01-16 08:00:00", "2026-01-16 10:00:00"
    raw = data.load_traffic(start, end)
    m = raw.segment_id == "R0002"
    first = raw[m].sort_values("timestamp").iloc[0]
    for col in data.VALUE_COLS:
        raw.loc[m, col] = first[col]
    p = data.sanitize(raw, start, end)
    assert p.report["stuck_cells"] >= 20
    assert data.quality_score(p, len(p.times) - 1)[graph.seg_index()["R0002"]] < 0.5


# features / detector ------------------------------------------------------------------------------
def test_feature_schema_matches_models():
    p = engine.window("2026-01-16 13:30:00")
    assert set(build_features(p, 3)) == set(feature_names())


def test_detector_flags_known_validation_incident():
    s = engine.get("2026-01-16 13:30:00")  # R0360 incident in incidents_validation (used here as a test oracle only)
    assert s.rows.set_index("segment_id").at["R0360", "anomalous"]
    assert s.diagnosis("R0360")["cause"] == "TRANSIENT_INCIDENT"


def test_state_thresholds():
    assert list(D.state_of(np.array([0.95, 0.8, 0.6, 0.3, np.nan]))) == ["NORMAL", "MODERATE", "HEAVY", "CRITICAL", "NO_DATA"]
