"""Tests for iterative BLS search, transit mask computation, and cross-iteration uniqueness.

Implements test cases from 05-test-spec.md for the iterative BLS feature.
"""
from __future__ import annotations

import time as time_mod

import lightkurve as lk
import numpy as np
import pytest
from astropy.time import Time

from exohunt.bls import (
    BLSCandidate,
    _build_transit_mask,
    _cross_iteration_unique,
    run_bls_search,
    run_iterative_bls_search,
)
from exohunt.config import resolve_runtime_config
from exohunt.preprocess import prepare_lightcurve


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_candidate(**overrides) -> BLSCandidate:
    defaults = dict(
        rank=1, period_days=3.0, duration_hours=2.4, depth=0.005,
        depth_ppm=5000.0, power=50.0, transit_time=0.5,
        transit_count_estimate=30.0, snr=10.0,
    )
    defaults.update(overrides)
    return BLSCandidate(**defaults)


def _inject_box_transit(time, flux, period, t0, duration_days, depth):
    """Inject a box-shaped transit into flux array (in-place)."""
    for n in range(int((time[-1] - t0) / period) + 2):
        epoch = t0 + n * period
        mask = np.abs(time - epoch) < 0.5 * duration_days
        flux[mask] -= depth


def _synthetic_lc(n_points=18000, span_days=90.0, signals=None):
    """Return (time, flux) with optional injected transits."""
    time = np.linspace(0, span_days, n_points)
    flux = np.ones_like(time)
    rng = np.random.default_rng(42)
    flux += rng.normal(0, 1e-4, len(flux))
    for sig in (signals or []):
        _inject_box_transit(time, flux, sig["period"], sig.get("t0", 0.5),
                            sig["duration_days"], sig["depth"])
    return time, flux


def _make_bls_config(**overrides):
    cli = {"bls": {
        "iterative_masking": True,
        "iterative_passes": 3,
        "min_snr": 5.0,
        "iterative_top_n": 1,
        "transit_mask_padding_factor": 1.5,
    }}
    if overrides:
        cli["bls"].update(overrides)
    cfg = resolve_runtime_config(cli_overrides=cli)
    return cfg.bls


def _make_lk_lightcurve(time, flux):
    return lk.LightCurve(time=Time(time, format="btjd"), flux=flux)


# ---------------------------------------------------------------------------
# TC-U-01 | Covers: FR-3, AC-2
# ---------------------------------------------------------------------------
class TestBuildTransitMask:
    def test_single_candidate_marks_correct_points(self):
        time = np.arange(0, 100, 0.02)
        cand = _make_candidate(period_days=2.0, transit_time=0.5,
                               duration_hours=2.4)  # 0.1 days
        mask = _build_transit_mask(time, [cand], 1.5)
        assert mask.shape == time.shape
        assert mask.dtype == bool
        half_width = 0.5 * (2.4 / 24.0) * 1.5  # 0.075 days
        # Check a known transit epoch
        epoch_mask = np.abs(time - 0.5) < half_width
        assert np.all(mask[epoch_mask]), "Points near first epoch should be masked"
        # Check a point far from any transit
        mid = 1.5  # midpoint between epoch 0.5 and 2.5
        far_idx = np.argmin(np.abs(time - mid))
        assert not mask[far_idx], "Point far from transit should not be masked"
        assert 0 < mask.sum() < len(time)

    # TC-U-02 | Covers: FR-3
    def test_multiple_candidates(self):
        time = np.arange(0, 100, 0.02)
        c1 = _make_candidate(period_days=3.0, transit_time=0.5, duration_hours=2.4)
        c2 = _make_candidate(period_days=7.0, transit_time=1.0, duration_hours=2.4)
        mask_both = _build_transit_mask(time, [c1, c2], 1.5)
        mask_c1 = _build_transit_mask(time, [c1], 1.5)
        mask_c2 = _build_transit_mask(time, [c2], 1.5)
        assert mask_both.sum() >= mask_c1.sum()
        assert mask_both.sum() >= mask_c2.sum()
        assert mask_both.sum() > max(mask_c1.sum(), mask_c2.sum())

    # TC-U-03 | Covers: FR-3
    def test_empty_candidates_returns_all_false(self):
        time = np.arange(0, 100, 0.02)
        mask = _build_transit_mask(time, [], 1.5)
        assert not mask.any()

    # TC-E-01 | Covers: FR-3
    def test_very_short_duration(self):
        time = np.arange(0, 100, 0.02)
        cand = _make_candidate(duration_hours=0.01)
        mask = _build_transit_mask(time, [cand], 1.5)
        assert mask.shape == time.shape  # no crash


# ---------------------------------------------------------------------------
# TC-U-04 | Covers: FR-5, AC-3
# ---------------------------------------------------------------------------
class TestCrossIterationUnique:
    def test_rejects_duplicate_period(self):
        accepted = _make_candidate(period_days=5.0)
        new_cand = _make_candidate(period_days=5.04)  # 0.8% separation
        assert not _cross_iteration_unique(new_cand, [accepted], threshold=0.01)

    def test_accepts_distinct_period(self):
        accepted = _make_candidate(period_days=5.0)
        new_cand = _make_candidate(period_days=5.06)  # 1.2% separation
        assert _cross_iteration_unique(new_cand, [accepted], threshold=0.01)

    # TC-E-03 | Covers: FR-5
    def test_empty_accepted_list(self):
        cand = _make_candidate(period_days=5.0)
        assert _cross_iteration_unique(cand, [], threshold=0.01)


# ---------------------------------------------------------------------------
# TC-U-05 | Covers: FR-1, FR-2, FR-5, FR-11, AC-1, AC-8
# ---------------------------------------------------------------------------
class TestIterativeBLSSearch:
    def test_finds_two_signals(self):
        time, flux = _synthetic_lc(signals=[
            {"period": 3.0, "t0": 0.5, "duration_days": 0.08, "depth": 0.005},
            {"period": 7.0, "t0": 1.0, "duration_days": 0.10, "depth": 0.003},
        ])
        lc = _make_lk_lightcurve(time, flux)
        config = _make_bls_config(iterative_passes=3, min_snr=5.0)
        candidates = run_iterative_bls_search(lc, config)
        assert len(candidates) >= 2
        iterations = {c.iteration for c in candidates}
        assert len(iterations) >= 2, "Candidates should come from different iterations"
        periods = sorted(c.period_days for c in candidates)
        assert any(abs(p - 3.0) / 3.0 < 0.10 for p in periods), "Should find ~3.0d signal"
        assert any(abs(p - 7.0) / 7.0 < 0.10 for p in periods), "Should find ~7.0d signal"
        for c in candidates:
            assert hasattr(c, "iteration")
            assert isinstance(c.iteration, int)

    # TC-U-06 | Covers: FR-10, NFR-1, AC-5
    def test_single_pass_matches_baseline(self):
        time, flux = _synthetic_lc(signals=[
            {"period": 3.0, "t0": 0.5, "duration_days": 0.08, "depth": 0.005},
        ])
        lc = _make_lk_lightcurve(time, flux)
        config_iter = _make_bls_config(iterative_passes=1, min_snr=5.0)
        iter_candidates = run_iterative_bls_search(lc, config_iter)
        # Iterative returns ALL candidates from the pass (vetting is the
        # caller's responsibility). Baseline must use the same top_n.
        direct_candidates = run_bls_search(
            lc,
            period_min_days=config_iter.period_min_days,
            period_max_days=config_iter.period_max_days,
            duration_min_hours=config_iter.duration_min_hours,
            duration_max_hours=config_iter.duration_max_hours,
            n_periods=config_iter.n_periods,
            n_durations=config_iter.n_durations,
            top_n=config_iter.top_n,
            unique_period_separation_fraction=config_iter.unique_period_separation_fraction,
            min_snr=config_iter.min_snr,
        )
        assert len(iter_candidates) == len(direct_candidates)
        for ic, dc in zip(iter_candidates, direct_candidates):
            assert abs(ic.period_days - dc.period_days) < 1e-6
            assert ic.iteration == 0

    # TC-U-07 | Covers: FR-4
    def test_stops_on_low_snr(self):
        time, flux = _synthetic_lc(signals=[
            {"period": 3.0, "t0": 0.5, "duration_days": 0.08, "depth": 0.005},
        ])
        lc = _make_lk_lightcurve(time, flux)
        config = _make_bls_config(iterative_passes=5, min_snr=7.0)
        candidates = run_iterative_bls_search(lc, config)
        # With one injected signal, the loop should stop after masking it —
        # subsequent passes find nothing above min_snr. Verify by the number
        # of iterations represented, not the candidate count.
        iterations_seen = {c.iteration for c in candidates}
        assert len(iterations_seen) < 5, (
            f"Should stop early when no more signals above SNR; "
            f"saw iterations {sorted(iterations_seen)}"
        )

    # TC-U-08 | Covers: FR-4
    def test_early_termination_few_points(self):
        time, flux = _synthetic_lc(n_points=150, span_days=10.0, signals=[
            {"period": 2.0, "t0": 0.5, "duration_days": 0.5, "depth": 0.01},
        ])
        lc = _make_lk_lightcurve(time, flux)
        config = _make_bls_config(iterative_passes=3, min_snr=3.0,
                                  transit_mask_padding_factor=3.0)
        candidates = run_iterative_bls_search(lc, config)
        # Should not crash; may return 0 or 1 candidates
        assert isinstance(candidates, list)

    # TC-E-02 | Covers: FR-4
    def test_flat_noise_free_returns_empty(self):
        time = np.linspace(0, 90, 18000)
        flux = np.ones_like(time)
        lc = _make_lk_lightcurve(time, flux)
        config = _make_bls_config(iterative_passes=3, min_snr=7.0)
        candidates = run_iterative_bls_search(lc, config)
        assert candidates == []

    # Regression: TIC 317597583 scenario — iterative must NOT gate masking on
    # vetting. A dominant systematic that fails odd/even must still be masked
    # so weaker real signals surface in later passes.
    def test_masks_top_peak_regardless_of_vetting(self):
        # Pass 1 top peak: asymmetric P=4.0d signal (odd transits deeper than
        # even). Vetting would flag it. Pass 2, with the 4.0d signal masked,
        # must surface the clean weaker P=7.0d signal.
        time = np.linspace(0, 90, 20000)
        flux = np.ones_like(time)
        rng = np.random.default_rng(42)
        flux += rng.normal(0, 1e-4, len(flux))
        # Strong asymmetric signal — odd transits 2x deeper than even
        for n in range(int(90 / 4.0) + 2):
            epoch = 0.3 + n * 4.0
            depth = 0.006 if (n % 2 == 0) else 0.003
            flux[np.abs(time - epoch) < 0.05] -= depth
        # Cleaner weaker signal (lower BLS power, but symmetric)
        for n in range(int(90 / 7.0) + 2):
            epoch = 1.1 + n * 7.0
            flux[np.abs(time - epoch) < 0.04] -= 0.0008
        lc = _make_lk_lightcurve(time, flux)
        config = _make_bls_config(iterative_passes=3, iterative_top_n=1,
                                  min_snr=5.0)
        candidates = run_iterative_bls_search(lc, config)
        periods = [c.period_days for c in candidates]
        # Under old (vet-before-mask) semantics, the loop aborted on pass 1
        # because the asymmetric signal failed odd/even, never exposing 7.0d.
        assert any(abs(p - 7.0) / 7.0 < 0.05 for p in periods), (
            f"Expected ~7.0d signal after masking dominant 4.0d peak; "
            f"got periods={periods}"
        )


# ---------------------------------------------------------------------------
# TC-U-09 | Covers: FR-6, AC-4
# ---------------------------------------------------------------------------
class TestIterativeFlattening:
    def test_reflatten_with_transit_mask(self):
        time, flux = _synthetic_lc(signals=[
            {"period": 3.0, "t0": 0.5, "duration_days": 0.08, "depth": 0.005},
            {"period": 7.0, "t0": 1.0, "duration_days": 0.10, "depth": 0.003},
        ])
        # Add a linear trend
        flux += np.linspace(0, 0.01, len(flux))
        lc = _make_lk_lightcurve(time, flux)
        bls_config = _make_bls_config(iterative_passes=3, min_snr=5.0)
        pp_config = resolve_runtime_config(cli_overrides={
            "preprocess": {"iterative_flatten": True, "transit_mask_padding_factor": 1.5}
        }).preprocess
        candidates = run_iterative_bls_search(lc, bls_config,
                                              preprocess_config=pp_config, lc=lc)
        assert isinstance(candidates, list)
        assert len(candidates) >= 1


# ---------------------------------------------------------------------------
# TC-U-10 | Covers: FR-7
# ---------------------------------------------------------------------------
class TestPrepareLightcurveTransitMask:
    def test_accepts_transit_mask_param(self):
        time = np.linspace(0, 30, 1000)
        flux = np.ones_like(time) * 1000.0
        rng = np.random.default_rng(99)
        flux += rng.normal(0, 1, len(flux))
        lc = lk.LightCurve(time=Time(time, format="btjd"), flux=flux)
        mask = np.zeros(len(time), dtype=bool)
        mask[100:150] = True
        result_lc, was_norm = prepare_lightcurve(lc, transit_mask=mask)
        assert result_lc is not None
        assert isinstance(was_norm, bool)


# ---------------------------------------------------------------------------
# TC-U-11 | Covers: FR-8, FR-9, FR-14, NFR-2, AC-7
# ---------------------------------------------------------------------------
class TestConfigDefaults:
    def test_new_fields_have_correct_defaults(self):
        cfg = resolve_runtime_config()
        assert cfg.bls.iterative_masking is False
        assert cfg.bls.iterative_passes == 1
        assert cfg.bls.subtraction_model == "box_mask"
        assert cfg.bls.iterative_top_n == 1
        assert cfg.bls.transit_mask_padding_factor == 1.5
        assert cfg.preprocess.iterative_flatten is False
        assert cfg.preprocess.transit_mask_padding_factor == 1.5


# ---------------------------------------------------------------------------
# TC-U-12 | Covers: FR-11, AC-8
# ---------------------------------------------------------------------------
class TestBLSCandidateIteration:
    def test_default_iteration_is_zero(self):
        c = BLSCandidate(
            rank=1, period_days=1.0, duration_hours=2.0, depth=0.001,
            depth_ppm=1000, power=10.0, transit_time=100.0,
            transit_count_estimate=30.0, snr=8.0,
        )
        assert c.iteration == 0

    def test_explicit_iteration(self):
        c = BLSCandidate(
            rank=1, period_days=1.0, duration_hours=2.0, depth=0.001,
            depth_ppm=1000, power=10.0, transit_time=100.0,
            transit_count_estimate=30.0, snr=8.0, iteration=2,
        )
        assert c.iteration == 2


# ---------------------------------------------------------------------------
# TC-P-01 | Covers: NFR-3
# ---------------------------------------------------------------------------
class TestPerformance:
    def test_single_bls_pass_under_10_seconds(self):
        time, flux = _synthetic_lc(n_points=18000, signals=[
            {"period": 3.0, "t0": 0.5, "duration_days": 0.08, "depth": 0.005},
        ])
        lc = _make_lk_lightcurve(time, flux)
        start = time_mod.time()
        run_bls_search(lc)
        elapsed = time_mod.time() - start
        assert elapsed < 10.0, f"BLS pass took {elapsed:.1f}s, expected <10s"
