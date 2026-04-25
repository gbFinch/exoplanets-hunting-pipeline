"""Tests for P1 BLS pipeline improvements (R7–R13)."""
from __future__ import annotations

import math
from pathlib import Path
from tempfile import NamedTemporaryFile, TemporaryDirectory

import numpy as np
import pytest

import lightkurve as lk

from exohunt.bls import BLSCandidate, run_bls_search
from exohunt.vetting import (
    CandidateVettingResult,
    _alias_harmonic_reference_rank,
    vet_bls_candidates,
)
from exohunt.config import (
    BLSConfig,
    ParameterConfig,
    RuntimeConfig,
    VettingConfig,
    _DEFAULTS,
    resolve_runtime_config,
)
from exohunt.plotting import save_candidate_diagnostics


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

def _make_candidate(
    rank: int = 1,
    period_days: float = 5.0,
    duration_hours: float = 2.0,
    depth: float = 0.001,
    power: float = 100.0,
    transit_time: float = 0.0,
    snr: float = 10.0,
    fap: float = float("nan"),
) -> BLSCandidate:
    return BLSCandidate(
        rank=rank,
        period_days=period_days,
        duration_hours=duration_hours,
        depth=depth,
        depth_ppm=depth * 1e6,
        power=power,
        transit_time=transit_time,
        transit_count_estimate=20.0,
        snr=snr,
        fap=fap,
    )


def _make_synthetic_lc(
    n_points: int = 2000,
    span_days: float = 50.0,
    period_days: float | None = None,
    depth: float = 0.0,
    secondary_depth: float = 0.0,
    depth_first_half_only: bool = False,
    depth_second_half_only: bool = False,
) -> lk.LightCurve:
    time = np.linspace(0.0, span_days, n_points)
    flux = np.ones(n_points, dtype=float)
    if period_days is not None and depth > 0:
        duration_days = 0.1 * period_days
        phase = ((time - 0.0) % period_days) / period_days
        in_transit = phase < (duration_days / period_days)
        if depth_first_half_only:
            in_transit = in_transit & (time < span_days / 2)
        elif depth_second_half_only:
            in_transit = in_transit & (time >= span_days / 2)
        flux[in_transit] -= depth
        if secondary_depth > 0:
            secondary_phase = ((time - 0.5 * period_days) % period_days) / period_days
            in_secondary = secondary_phase < (duration_days / period_days)
            flux[in_secondary] -= secondary_depth
    return lk.LightCurve(time=time, flux=flux)


# ===========================================================================
# R8 — Alias ratios
# ===========================================================================

class TestR8AliasRatios:
    # TC-U-01 | Covers: FR-6, AC-3
    def test_r8_alias_two_thirds_not_flagged(self):
        """2/3 ratio is NOT a periodogram alias — it indicates a real resonant
        planet (e.g. 3:2 mean-motion resonance), not a harmonic artifact."""
        a = _make_candidate(rank=1, period_days=3.0, power=100.0)
        b = _make_candidate(rank=2, period_days=2.0, power=50.0)
        result = _alias_harmonic_reference_rank(index=1, candidates=[a, b], tolerance_fraction=0.02)
        assert result == -1, "2/3 ratio should not be flagged (resonant planets)"

    # TC-U-02 | Covers: FR-6
    def test_r8_alias_three_halves_not_flagged(self):
        """3/2 ratio is NOT a periodogram alias — it indicates a real resonant
        planet (e.g. 3:2 mean-motion resonance), not a harmonic artifact."""
        a = _make_candidate(rank=1, period_days=2.0, power=100.0)
        b = _make_candidate(rank=2, period_days=3.0, power=50.0)
        result = _alias_harmonic_reference_rank(index=1, candidates=[a, b], tolerance_fraction=0.02)
        assert result == -1, "3/2 ratio should not be flagged (resonant planets)"


# ===========================================================================
# R11 — Config schema
# ===========================================================================

class TestR11Config:
    # TC-U-03 | Covers: FR-17
    def test_r11_vetting_config_defaults(self):
        vc = VettingConfig(
            min_transit_count=2,
            odd_even_max_mismatch_fraction=0.30,
            alias_tolerance_fraction=0.02,
            secondary_eclipse_max_fraction=0.30,
            depth_consistency_max_fraction=0.50,
        )
        assert vc.min_transit_count == 2
        assert vc.secondary_eclipse_max_fraction == 0.30
        assert vc.depth_consistency_max_fraction == 0.50

    # TC-U-04 | Covers: FR-18
    def test_r11_parameter_config_defaults(self):
        pc = ParameterConfig(
            stellar_density_kg_m3=1408.0,
            duration_ratio_min=0.05,
            duration_ratio_max=1.8,
            apply_limb_darkening_correction=False,
            limb_darkening_u1=0.4,
            limb_darkening_u2=0.2,
            tic_density_lookup=False,
        )
        assert pc.stellar_density_kg_m3 == 1408.0
        assert pc.duration_ratio_min == 0.05
        assert pc.duration_ratio_max == 1.8

    # TC-U-05 | Covers: FR-2, FR-5, FR-19, FR-30
    def test_r11_bls_config_new_fields(self):
        cfg = resolve_runtime_config(preset_name="quicklook")
        assert hasattr(cfg.bls, "compute_fap")
        assert hasattr(cfg.bls, "fap_iterations")
        assert hasattr(cfg.bls, "iterative_masking")
        assert cfg.bls.compute_fap is False
        assert cfg.bls.fap_iterations == 1000
        assert cfg.bls.iterative_masking is False

    # TC-U-06 | Covers: FR-20, NFR-2
    def test_r11_defaults_include_new_sections(self):
        assert "vetting" in _DEFAULTS
        assert "parameters" in _DEFAULTS
        assert _DEFAULTS["vetting"]["min_transit_count"] == 2
        assert _DEFAULTS["vetting"]["secondary_eclipse_max_fraction"] == 0.30
        assert _DEFAULTS["vetting"]["depth_consistency_max_fraction"] == 0.50
        assert _DEFAULTS["parameters"]["stellar_density_kg_m3"] == 1408.0
        assert _DEFAULTS["bls"]["compute_fap"] is False
        assert _DEFAULTS["bls"]["iterative_masking"] is False

    # TC-I-01 | Covers: FR-21, AC-13
    @pytest.mark.parametrize("preset", ["quicklook", "science-default", "deep-search"])
    def test_r11_presets_produce_valid_config(self, preset):
        cfg = resolve_runtime_config(preset_name=preset)
        assert isinstance(cfg.vetting, VettingConfig)
        assert isinstance(cfg.parameters, ParameterConfig)
        assert cfg.vetting.min_transit_count >= 1
        assert cfg.parameters.stellar_density_kg_m3 > 0

    # TC-I-02 | Covers: FR-22, AC-9
    def test_r11_custom_vetting_override(self, tmp_path):
        toml_content = 'schema_version = 1\n[vetting]\nmin_transit_count = 5\n'
        config_file = tmp_path / "test.toml"
        config_file.write_text(toml_content)
        cfg = resolve_runtime_config(config_path=config_file)
        assert cfg.vetting.min_transit_count == 5
        assert cfg.vetting.odd_even_max_mismatch_fraction == 0.30  # default

    # TC-I-03 | Covers: FR-24, AC-8
    def test_r11_backward_compat_no_new_sections(self, tmp_path):
        toml_content = 'schema_version = 1\n[bls]\nenabled = true\nmode = "stitched"\nperiod_min_days = 0.5\nperiod_max_days = 20.0\nduration_min_hours = 0.5\nduration_max_hours = 10.0\nn_periods = 2000\nn_durations = 12\ntop_n = 5\nmin_snr = 7.0\n'
        config_file = tmp_path / "old.toml"
        config_file.write_text(toml_content)
        cfg = resolve_runtime_config(config_path=config_file)
        assert cfg.vetting.min_transit_count == 2
        assert cfg.parameters.stellar_density_kg_m3 == 1408.0

    # TC-U-15 | Covers: FR-23
    def test_r11_hardcoded_constants_removed(self):
        from exohunt import pipeline
        for name in [
            "_VETTING_MIN_TRANSIT_COUNT",
            "_VETTING_ODD_EVEN_MAX_MISMATCH_FRACTION",
            "_VETTING_ALIAS_TOLERANCE_FRACTION",
            "_PARAMETER_STELLAR_DENSITY_KG_M3",
            "_PARAMETER_DURATION_RATIO_MIN",
            "_PARAMETER_DURATION_RATIO_MAX",
        ]:
            assert not hasattr(pipeline, name), f"{name} should be removed from pipeline.py"


# ===========================================================================
# R7 — Bootstrap FAP
# ===========================================================================

class TestR7BootstrapFAP:
    # TC-U-07 | Covers: FR-1
    def test_r7_bls_candidate_has_fap(self):
        c = _make_candidate(fap=0.05)
        assert c.fap == 0.05

    # TC-U-08 | Covers: FR-3, AC-2
    def test_r7_fap_computed_when_enabled(self):
        lc = _make_synthetic_lc(n_points=1000, span_days=100, period_days=5.0, depth=0.005)
        candidates = run_bls_search(
            lc, compute_fap=True, fap_iterations=50, min_snr=0.0, top_n=1,
            period_min_days=1.0, period_max_days=50.0,
        )
        assert len(candidates) >= 1
        for c in candidates:
            assert not math.isnan(c.fap), "FAP should be computed, not NaN"
            assert 0.0 <= c.fap <= 1.0

    # TC-U-09 | Covers: FR-4, AC-1
    def test_r7_fap_nan_when_disabled(self):
        lc = _make_synthetic_lc(n_points=1000, span_days=100, period_days=5.0, depth=0.005)
        candidates = run_bls_search(
            lc, compute_fap=False, min_snr=0.0, top_n=1,
            period_min_days=1.0, period_max_days=50.0,
        )
        assert len(candidates) >= 1
        for c in candidates:
            assert math.isnan(c.fap)

    # TC-E-03 | Covers: FR-3
    def test_r7_fap_flat_flux(self):
        lc = _make_synthetic_lc(n_points=500, span_days=50, depth=0.0)
        candidates = run_bls_search(
            lc, compute_fap=True, fap_iterations=10, min_snr=0.0, top_n=1,
            period_min_days=1.0, period_max_days=25.0,
        )
        # Should not crash; candidates may be empty or have NaN fap
        for c in candidates:
            assert isinstance(c.fap, float)


# ===========================================================================
# R9 — Secondary eclipse
# ===========================================================================

class TestR9SecondaryEclipse:
    # TC-U-10 | Covers: FR-7, FR-8, FR-9, AC-4
    def test_r9_secondary_eclipse_flagged(self):
        lc = _make_synthetic_lc(
            n_points=2000, span_days=50, period_days=5.0,
            depth=0.001, secondary_depth=0.0008,
        )
        candidate = _make_candidate(period_days=5.0, duration_hours=2.4, transit_time=0.0)
        results = vet_bls_candidates(
            lc, [candidate], secondary_eclipse_max_fraction=0.30,
        )
        r = results[1]
        assert r.pass_secondary_eclipse is False
        assert "secondary_eclipse" in r.vetting_reasons

    # TC-E-01 | Covers: FR-10, AC-5
    def test_r9_insufficient_data_passes(self):
        lc = _make_synthetic_lc(n_points=30, span_days=5)
        candidate = _make_candidate(period_days=5.0, duration_hours=2.4, transit_time=0.0)
        results = vet_bls_candidates(lc, [candidate])
        r = results[1]
        assert r.pass_secondary_eclipse is True
        assert math.isnan(r.secondary_eclipse_depth_fraction)

    # TC-E-04 | Covers: FR-9
    def test_r9_zero_primary_depth(self):
        lc = _make_synthetic_lc(n_points=2000, span_days=50, period_days=5.0, depth=0.0)
        candidate = _make_candidate(period_days=5.0, depth=0.0, duration_hours=2.4)
        results = vet_bls_candidates(lc, [candidate])
        r = results[1]
        assert r.pass_secondary_eclipse is True


# ===========================================================================
# R10 — Depth consistency
# ===========================================================================

class TestR10DepthConsistency:
    # TC-U-11 | Covers: FR-12, FR-13, FR-14, AC-6
    def test_r10_depth_inconsistency_flagged(self):
        lc = _make_synthetic_lc(
            n_points=2000, span_days=100, period_days=5.0,
            depth=0.001, depth_first_half_only=True,
        )
        candidate = _make_candidate(period_days=5.0, duration_hours=2.4, transit_time=0.0)
        results = vet_bls_candidates(
            lc, [candidate], depth_consistency_max_fraction=0.50,
        )
        r = results[1]
        assert r.pass_depth_consistency is False
        assert "depth_inconsistent" in r.vetting_reasons

    # TC-E-02 | Covers: FR-15, AC-7
    def test_r10_insufficient_half_data_passes(self):
        lc = _make_synthetic_lc(n_points=30, span_days=5)
        candidate = _make_candidate(period_days=5.0, duration_hours=2.4, transit_time=0.0)
        results = vet_bls_candidates(lc, [candidate])
        r = results[1]
        assert r.pass_depth_consistency is True
        assert math.isnan(r.depth_consistency_fraction)


# ===========================================================================
# R9/R10 — vetting_pass integration
# ===========================================================================

class TestVettingPassIntegration:
    # TC-U-14 | Covers: FR-11, FR-16
    def test_vetting_pass_false_when_secondary_fails(self):
        lc = _make_synthetic_lc(
            n_points=2000, span_days=50, period_days=5.0,
            depth=0.001, secondary_depth=0.0005,
        )
        candidate = _make_candidate(period_days=5.0, duration_hours=2.4, transit_time=0.0)
        results = vet_bls_candidates(
            lc, [candidate], secondary_eclipse_max_fraction=0.01,
        )
        r = results[1]
        assert r.vetting_pass is False


# ===========================================================================
# R12 — Diagnostic annotations
# ===========================================================================

class TestR12Diagnostics:
    # TC-U-16 | Covers: FR-25, FR-26, FR-27, FR-28, AC-10
    def test_r12_diagnostics_with_annotations(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        lc = _make_synthetic_lc(n_points=500, span_days=50, period_days=5.0, depth=0.001)
        candidate = _make_candidate(period_days=5.0, duration_hours=2.4, transit_time=0.0)
        periods = np.linspace(1, 20, 200)
        power = np.random.default_rng(42).random(200)
        vetting_result = CandidateVettingResult(
            pass_min_transit_count=True, pass_odd_even_depth=True,
            pass_alias_harmonic=True, pass_secondary_eclipse=True,
            pass_depth_consistency=True, vetting_pass=True,
            transit_count_observed=10, odd_depth_ppm=100.0, even_depth_ppm=100.0,
            odd_even_depth_mismatch_fraction=0.0, secondary_eclipse_depth_fraction=0.0,
            depth_consistency_fraction=0.0, alias_harmonic_with_rank=-1,
            vetting_reasons="pass", odd_even_status="pass",
        )
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        result = save_candidate_diagnostics(
            target="TIC 999999999", output_key="test",
            lc_prepared=lc, candidates=[candidate],
            period_grid_days=periods, power_grid=power,
            vetting_results={1: vetting_result},
            run_dir=run_dir,
        )
        assert len(result) == 1
        for periodogram_path, phasefold_path in result:
            assert periodogram_path.exists()
            assert phasefold_path.exists()

    # TC-U-17 | Covers: FR-29
    def test_r12_diagnostics_backward_compatible(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        lc = _make_synthetic_lc(n_points=500, span_days=50, period_days=5.0, depth=0.001)
        candidate = _make_candidate(period_days=5.0, duration_hours=2.4, transit_time=0.0)
        periods = np.linspace(1, 20, 200)
        power = np.random.default_rng(42).random(200)
        run_dir = tmp_path / "run"
        run_dir.mkdir(exist_ok=True)
        result = save_candidate_diagnostics(
            target="TIC 999999998", output_key="test",
            lc_prepared=lc, candidates=[candidate],
            period_grid_days=periods, power_grid=power,
            run_dir=run_dir,
        )
        assert len(result) == 1

    # TC-E-05 | Covers: FR-29
    def test_r12_diagnostics_empty_vetting_dict(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        lc = _make_synthetic_lc(n_points=500, span_days=50, period_days=5.0, depth=0.001)
        candidate = _make_candidate(period_days=5.0, duration_hours=2.4, transit_time=0.0)
        periods = np.linspace(1, 20, 200)
        power = np.random.default_rng(42).random(200)
        run_dir = tmp_path / "run"
        run_dir.mkdir(exist_ok=True)
        result = save_candidate_diagnostics(
            target="TIC 999999997", output_key="test",
            lc_prepared=lc, candidates=[candidate],
            period_grid_days=periods, power_grid=power,
            vetting_results={},
            run_dir=run_dir,
        )
        assert len(result) == 1


# ===========================================================================
# R13 — Iterative masking config
# ===========================================================================

class TestR13IterativeMasking:
    # TC-U-18 (partial) | Covers: FR-30
    def test_r13_config_flag_exists(self):
        cfg = resolve_runtime_config(preset_name="quicklook")
        assert cfg.bls.iterative_masking is False


# ===========================================================================
# NFR-4 — No new dependencies
# ===========================================================================

class TestNFR:
    # TC-U-20 | Covers: NFR-4
    def test_no_new_dependencies(self):
        try:
            import tomllib
        except ModuleNotFoundError:
            import tomli as tomllib
        pyproject = Path(__file__).resolve().parent.parent / "pyproject.toml"
        with pyproject.open("rb") as f:
            data = tomllib.load(f)
        deps = set(data["project"]["dependencies"])
        expected = {"numpy", "matplotlib", "astropy", "lightkurve", "pandas", "transitleastsquares", "triceratops"}
        assert deps == expected, f"Unexpected dependencies: {deps - expected}"
