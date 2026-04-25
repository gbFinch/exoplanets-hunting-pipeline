"""Characterization tests for refactoring safety.

Pin current behavior of bls.py and pipeline.py before structural refactoring
(R14: pipeline decomposition, R15: BLS DRY extraction).
These tests verify observable behavior, not implementation details.
"""
from __future__ import annotations

import numpy as np
import pytest

from exohunt.bls import BLSCandidate, compute_bls_periodogram, run_bls_search

from conftest import _test_config


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _ArrayValue:
    def __init__(self, values):
        self.value = np.asarray(values, dtype=float)


class _SimpleLC:
    def __init__(self, time, flux):
        self.time = _ArrayValue(time)
        self.flux = _ArrayValue(flux)

    def remove_nans(self):
        return self

    def remove_outliers(self, sigma=5.0):
        return self

    def flatten(self, window_length=401):
        return self

    def __truediv__(self, other):
        return _SimpleLC(self.time.value, self.flux.value / other)


def _make_transit_lc(n_points=2000, period=3.0, depth=0.01, duration_frac=0.02):
    """Create a synthetic light curve with a box transit signal."""
    rng = np.random.default_rng(42)
    time = np.linspace(0, 30, n_points)
    flux = np.ones(n_points) + rng.normal(0, 0.001, n_points)
    phase = (time % period) / period
    in_transit = phase < duration_frac
    flux[in_transit] -= depth
    return _SimpleLC(time, flux)


# ---------------------------------------------------------------------------
# Step 2: Characterize _prepare_bls_inputs extraction targets
# Protects: src/exohunt/bls.py run_bls_search() and compute_bls_periodogram()
# ---------------------------------------------------------------------------

class TestBLSSearchBehavior:
    """Characterize run_bls_search() behavior before DRY extraction."""

    def test_returns_candidates_for_strong_signal(self):
        # Characterizes behavior for: Step 2 (Extract _prepare_bls_inputs)
        lc = _make_transit_lc(period=3.0, depth=0.01)
        candidates = run_bls_search(lc, period_min_days=1.0, period_max_days=10.0, min_snr=3.0)
        assert isinstance(candidates, list)
        assert len(candidates) > 0
        assert all(isinstance(c, BLSCandidate) for c in candidates)

    def test_candidate_fields_populated(self):
        # Characterizes behavior for: Step 2 (Extract _prepare_bls_inputs)
        lc = _make_transit_lc()
        candidates = run_bls_search(lc, min_snr=3.0)
        c = candidates[0]
        assert c.rank == 1
        assert c.period_days > 0
        assert c.duration_hours > 0
        assert np.isfinite(c.depth)
        assert np.isfinite(c.depth_ppm)
        assert np.isfinite(c.power)
        assert np.isfinite(c.snr)
        assert c.snr >= 3.0

    def test_empty_for_too_few_points(self):
        # Characterizes behavior for: Step 2 (Extract _prepare_bls_inputs)
        lc = _SimpleLC(np.arange(10, dtype=float), np.ones(10))
        assert run_bls_search(lc, min_snr=0.0) == []

    def test_empty_for_zero_span_data(self):
        # Characterizes behavior for: Step 2 (Extract _prepare_bls_inputs)
        # All identical timestamps → span_days = 0 → early return
        lc = _SimpleLC(np.ones(60), np.ones(60))
        assert run_bls_search(lc, min_snr=0.0) == []

    def test_snr_threshold_filters_noise(self):
        # Characterizes behavior for: Step 2 (Extract _prepare_bls_inputs)
        rng = np.random.default_rng(99)
        time = np.linspace(0, 30, 2000)
        flux = np.ones(2000) + rng.normal(0, 0.001, 2000)
        lc = _SimpleLC(time, flux)
        # High SNR threshold should filter out noise peaks
        candidates = run_bls_search(lc, min_snr=20.0)
        assert candidates == []


class TestBLSPeriodogramBehavior:
    """Characterize compute_bls_periodogram() behavior before DRY extraction."""

    def test_returns_period_and_power_arrays(self):
        # Characterizes behavior for: Step 2 (Extract _prepare_bls_inputs)
        lc = _make_transit_lc()
        periods, power = compute_bls_periodogram(lc)
        assert isinstance(periods, np.ndarray)
        assert isinstance(power, np.ndarray)
        assert len(periods) == len(power)
        assert len(periods) > 0

    def test_empty_for_too_few_points(self):
        # Characterizes behavior for: Step 2 (Extract _prepare_bls_inputs)
        lc = _SimpleLC(np.arange(10, dtype=float), np.ones(10))
        periods, power = compute_bls_periodogram(lc)
        assert len(periods) == 0
        assert len(power) == 0

    def test_period_range_respected(self):
        # Characterizes behavior for: Step 2 (Extract _prepare_bls_inputs)
        lc = _make_transit_lc()
        periods, power = compute_bls_periodogram(
            lc, period_min_days=2.0, period_max_days=5.0
        )
        assert len(periods) > 0
        assert periods.min() >= 2.0 - 0.01
        assert periods.max() <= 5.0 + 0.01


# ---------------------------------------------------------------------------
# Steps 4-6: Characterize fetch_and_plot() pipeline behavior
# Protects: src/exohunt/pipeline.py fetch_and_plot()
# ---------------------------------------------------------------------------

class TestFetchAndPlotBehavior:
    """Characterize fetch_and_plot() observable behavior before decomposition."""

    def test_returns_path_on_cache_hit(self, monkeypatch, tmp_path, test_run_dir):
        # Characterizes behavior for: Steps 4-6 (pipeline decomposition)
        # Protects: fetch_and_plot() return type and output file creation
        from exohunt import pipeline
        from exohunt.pipeline import fetch_and_plot
        from exohunt.cache import _cache_path

        target = "TIC 999999999"
        cache_dir = tmp_path / "cache"
        cache_file = _cache_path(target, cache_dir)
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        np.savez(cache_file, time=np.arange(100, dtype=float), flux=np.ones(100))

        def _no_search(*a, **kw):
            raise AssertionError("should not search")

        monkeypatch.setattr(pipeline.lk, "search_lightcurve", _no_search)
        monkeypatch.chdir(tmp_path)

        result = fetch_and_plot(target, config=_test_config(preprocess_mode="stitched"), run_dir=test_run_dir, cache_dir=cache_dir)
        assert result is None or isinstance(result, type(tmp_path))

    def test_produces_metrics_files(self, monkeypatch, tmp_path, test_run_dir):
        # Characterizes behavior for: Steps 4-6 (pipeline decomposition)
        # Protects: metrics CSV and JSON output
        from exohunt import pipeline
        from exohunt.pipeline import fetch_and_plot
        from exohunt.cache import _cache_path

        target = "TIC 888888888"
        cache_dir = tmp_path / "cache"
        cache_file = _cache_path(target, cache_dir)
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        np.savez(cache_file, time=np.arange(100, dtype=float), flux=np.ones(100))

        monkeypatch.setattr(pipeline.lk, "search_lightcurve",
                            lambda *a, **kw: (_ for _ in ()).throw(AssertionError("no search")))
        monkeypatch.chdir(tmp_path)

        fetch_and_plot(target, config=_test_config(preprocess_mode="stitched"), run_dir=test_run_dir, cache_dir=cache_dir)
        assert (test_run_dir / "preprocessing_summary.csv").exists()
        assert (test_run_dir / "tic_888888888/metrics/preprocessing_summary.json").exists()

    def test_produces_manifest_files(self, monkeypatch, tmp_path, test_run_dir):
        # Characterizes behavior for: Steps 4-6 (pipeline decomposition)
        # Protects: manifest JSON and index CSV output
        from exohunt import pipeline
        from exohunt.pipeline import fetch_and_plot
        from exohunt.cache import _cache_path

        target = "TIC 777777777"
        cache_dir = tmp_path / "cache"
        cache_file = _cache_path(target, cache_dir)
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        np.savez(cache_file, time=np.arange(100, dtype=float), flux=np.ones(100))

        monkeypatch.setattr(pipeline.lk, "search_lightcurve",
                            lambda *a, **kw: (_ for _ in ()).throw(AssertionError("no search")))
        monkeypatch.chdir(tmp_path)

        fetch_and_plot(target, config=_test_config(preprocess_mode="stitched"), run_dir=test_run_dir, cache_dir=cache_dir)
        manifest_dir = test_run_dir / "tic_777777777/manifests"
        assert manifest_dir.exists()
        assert len(list(manifest_dir.glob("*__manifest_*.json"))) == 1
        assert (test_run_dir / "run_manifest_index.csv").exists()

    def test_produces_candidate_files_when_bls_enabled(self, monkeypatch, tmp_path, test_run_dir):
        # Characterizes behavior for: Steps 4-6 (pipeline decomposition)
        # Protects: candidate CSV/JSON output
        from exohunt import pipeline
        from exohunt.pipeline import fetch_and_plot
        from exohunt.cache import _cache_path

        target = "TIC 666666666"
        cache_dir = tmp_path / "cache"
        cache_file = _cache_path(target, cache_dir)
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        # Need enough points for BLS to run
        rng = np.random.default_rng(42)
        time = np.linspace(0, 30, 2000)
        flux = np.ones(2000) + rng.normal(0, 0.001, 2000)
        np.savez(cache_file, time=time, flux=flux)

        monkeypatch.setattr(pipeline.lk, "search_lightcurve",
                            lambda *a, **kw: (_ for _ in ()).throw(AssertionError("no search")))
        monkeypatch.chdir(tmp_path)

        fetch_and_plot(target, config=_test_config(preprocess_mode="stitched", run_bls=True), run_dir=test_run_dir, cache_dir=cache_dir)
        candidates_dir = test_run_dir / "tic_666666666/candidates"
        assert candidates_dir.exists()
        csv_files = list(candidates_dir.glob("*.csv"))
        json_files = list(candidates_dir.glob("*.json"))
        assert len(csv_files) >= 1
        assert len(json_files) >= 1
