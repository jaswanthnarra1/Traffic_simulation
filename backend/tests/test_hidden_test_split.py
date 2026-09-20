"""Hidden test input (NEURAX_SMART_CITIES_TESTING_NOISY_V2): it must be analysable but never fittable.

These tests skip when the split has not been imported (scripts/import_test_data.py), so the suite still runs on a
clone that only has the training data.
"""
import numpy as np
import pandas as pd
import pytest

from app import config as C, data, detection as D, engine

pytestmark = pytest.mark.skipif(not data.has_test(), reason="hidden test input not imported")


def test_fitting_panel_never_reaches_the_test_period():
    """THE leakage guarantee: full_panel() feeds training, evaluation and robustness. It must stop at validation."""
    f = data.full_panel()
    assert f.times[-1] == pd.Timestamp(C.VAL_END)
    assert f.times.max() < pd.Timestamp(C.TEST_START)


def test_runtime_panel_covers_the_test_period_and_extends_the_fitting_one():
    r, f = data.runtime_panel(), data.full_panel()
    assert r.times[-1] == pd.Timestamp(C.TEST_END) and r.times[0] == f.times[0]
    assert len(r.times) > len(f.times) and r.segs == f.segs
    assert data.data_range()[1] == pd.Timestamp(C.TEST_END)


def test_no_gap_or_overlap_between_the_splits():
    """The union in load_traffic() is only valid because the splits are disjoint and contiguous in time."""
    t = data.runtime_panel().times
    assert len(t) == len(set(t)), "duplicate timestamps across splits"
    assert set(np.diff(t.values).astype("timedelta64[m]").astype(int)) == {C.STEP_MIN}


def test_the_sanitizer_absorbs_the_injected_noise():
    """The organizer injected negatives, nulls, duplicates and impossible occupancy into traffic_input.csv."""
    raw = data.q("SELECT count(*) n, sum(CASE WHEN speed_kmh < 0 THEN 1 ELSE 0 END) neg, "
                 "sum(CASE WHEN speed_kmh IS NULL THEN 1 ELSE 0 END) nul, "
                 "count(*) - count(DISTINCT (timestamp, segment_id)) dup FROM traffic_test").iloc[0]
    assert raw.neg > 0 and raw.nul > 0 and raw.dup > 0, "expected the noisy test input"
    p = data.slice_panel(data.runtime_panel(), C.TEST_START, C.TEST_END)
    sp = p.v["speed_kmh"]
    assert np.nanmin(sp) >= 0, "a negative reading survived sanitising"
    assert p.valid.mean() > 0.5 and np.isnan(sp).mean() < 0.05


def test_the_trained_detector_runs_on_the_test_period_and_stays_selective():
    """Flags exist but stay rare: the README warns not every anomaly is a real incident."""
    p = data.runtime_panel()
    meta = D.detector_meta()
    score = D.score_variant(D.components(p, D.load_baseline()), meta["variant"])
    test = p.times >= pd.Timestamp(C.TEST_START)
    flagged = (score[:, test] >= meta["threshold"])
    assert flagged.any(), "detector found nothing at all in 8 days of test data"
    assert flagged.mean() < 0.01, "detector is firing on everything"


def test_evaluation_windows_are_usable_and_inside_the_test_period():
    w = data.table("evaluation_windows")
    assert len(w) == 36 and set(w.columns) >= {"scenario_id", "window_start", "window_end", "required_outputs"}
    assert w.window_start.min() >= pd.Timestamp(C.TEST_START) and w.window_end.max() <= pd.Timestamp(C.TEST_END)
    assert (w.window_end > w.window_start).all()


def test_a_snapshot_inside_a_scenario_window_produces_the_required_outputs():
    w = data.table("evaluation_windows").iloc[0]
    s = engine.get(data.floor_time(w.window_start + (w.window_end - w.window_start) / 2))
    assert len(s.rows) == 436 and s.rows.state.notna().all()                 # state
    assert s.forecast is not None                                            # forecast
    seg = s.rows.segment_id.iloc[0]
    assert set(s.forecast_for(seg)) >= {"speed", "flow", "congestion"}
    assert s.diagnosis(seg)["cause"]                                         # incident reasoning
    assert isinstance(s.propagation_for(seg), list)                          # impact
