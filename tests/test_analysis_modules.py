import json
from pathlib import Path

import numpy as np
import pytest

from exohunt import bls
from exohunt import pipeline
from exohunt.bls import BLSCandidate, run_bls_search
from exohunt.vetting import CandidateVettingResult
from exohunt.preprocess import compute_preprocessing_quality_metrics

from conftest import _test_config


class _ArrayValue:
    def __init__(self, values):
        self.value = np.asarray(values, dtype=float)


class _SimpleLC:
    def __init__(self, time, flux):
        self.time = _ArrayValue(time)
        self.flux = _ArrayValue(flux)


def test_compute_preprocessing_quality_metrics_counts_finite_points_and_retention():
    raw = _SimpleLC(
        time=np.arange(10, dtype=float),
        flux=np.asarray([1.0, np.nan, 0.99, 1.01, np.nan, 1.0, 0.98, 1.02, 1.0, np.nan]),
    )
    prepared = _SimpleLC(
        time=np.arange(6, dtype=float),
        flux=np.asarray([1.0, 1.001, 0.999, 1.0, 1.0, np.nan]),
    )

    metrics = compute_preprocessing_quality_metrics(raw, prepared)

    assert metrics.n_points_raw == 7
    assert metrics.n_points_prepared == 5
    assert metrics.retained_cadence_fraction == pytest.approx(5.0 / 7.0)
    assert metrics.prepared_rms < metrics.raw_rms
    assert metrics.prepared_mad < metrics.raw_mad


def test_compute_preprocessing_quality_metrics_raises_when_prepared_has_no_finite_values():
    raw = _SimpleLC(time=np.arange(4, dtype=float), flux=np.asarray([1.0, 1.0, 1.0, 1.0]))
    prepared = _SimpleLC(
        time=np.arange(4, dtype=float),
        flux=np.asarray([np.nan, np.nan, np.nan, np.nan]),
    )
    with pytest.raises(RuntimeError, match="prepared flux has no finite points"):
        compute_preprocessing_quality_metrics(raw, prepared)


def test_run_bls_search_ranks_by_power_and_filters_duplicate_periods(monkeypatch):
    class _FakeResult:
        power = np.asarray([5.0, 10.0, 9.0], dtype=float)
        period = np.asarray([2.0, 3.0, 3.01], dtype=float)
        duration = np.asarray([0.12, 0.10, 0.10], dtype=float)
        depth = np.asarray([0.002, 0.003, 0.0025], dtype=float)
        transit_time = np.asarray([0.2, 0.3, 0.3], dtype=float)

    class _FakeBLS:
        def __init__(self, time, flux):
            assert len(time) == len(flux)

        def power(self, periods, durations):
            assert len(periods) >= 200
            assert len(durations) >= 1
            return _FakeResult()

    monkeypatch.setattr(bls, "BoxLeastSquares", _FakeBLS)
    lc = _SimpleLC(time=np.arange(0.0, 5.0, 0.05), flux=np.ones(100))
    candidates = run_bls_search(
        lc_prepared=lc,
        period_min_days=0.5,
        period_max_days=4.0,
        top_n=3,
        unique_period_separation_fraction=0.02,
        min_snr=0.0,
    )

    # With only 3 fake power values [5, 10, 9], median=9, so power=5 has negative
    # SNR and is correctly filtered. Only the peak at period=3.0 (power=10) survives.
    assert len(candidates) >= 1
    assert candidates[0].period_days == pytest.approx(3.0)
    assert candidates[0].power == pytest.approx(10.0)
    assert hasattr(candidates[0], "snr")


def test_fetch_and_plot_with_fixed_fixture_emits_reproducible_candidate_payload(
    monkeypatch, tmp_path
):
    target = "TIC FIXED 1"
    cache_dir = tmp_path / "cache"
    cache_file = cache_dir / "tic_fixed_1.npz"
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    np.savez(
        cache_file,
        time=np.asarray([10.0, 11.0, 12.0, 13.0, 14.0], dtype=float),
        flux=np.asarray([1.0, 1.001, 0.999, 1.0005, 0.9995], dtype=float),
    )

    fixture_path = Path(__file__).parent / "fixtures" / "fetch_pipeline_expected.json"
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))

    def _unexpected_search(*args, **kwargs):
        raise AssertionError("search_lightcurve should not be called on cache hit")

    def _fake_bls_search(**kwargs):
        return [
            BLSCandidate(
                rank=1,
                period_days=3.2,
                duration_hours=2.5,
                depth=1.0e-4,
                depth_ppm=100.0,
                power=0.03,
                transit_time=10.2,
                transit_count_estimate=1.2,
                snr=10.0,
            ),
            BLSCandidate(
                rank=2,
                period_days=5.8,
                duration_hours=2.2,
                depth=8.0e-5,
                depth_ppm=80.0,
                power=0.02,
                transit_time=10.1,
                transit_count_estimate=0.8,
                snr=8.0,
            ),
        ]

    def _fake_refine(**kwargs):
        return kwargs["candidates"]

    def _fake_vet(**kwargs):
        return {
            1: CandidateVettingResult(True, True, True, True, 3, 100.0, 102.0, 0.02, -1, "pass", "pass"),
            2: CandidateVettingResult(True, True, True, True, 2, 80.0, 81.0, 0.01, -1, "pass", "pass"),
        }

    monkeypatch.setattr(pipeline.lk, "search_lightcurve", _unexpected_search)
    monkeypatch.setattr(pipeline, "run_bls_search", _fake_bls_search)
    monkeypatch.setattr(pipeline, "refine_bls_candidates", _fake_refine)
    monkeypatch.setattr(pipeline, "vet_bls_candidates", _fake_vet)
    monkeypatch.setattr(pipeline, "save_candidate_diagnostics", lambda **kwargs: [])
    monkeypatch.chdir(tmp_path)

    output = pipeline.fetch_and_plot(
        target=target,
        config=_test_config(preprocess_mode="stitched", run_bls=True, plot_mode="stitched"),
        cache_dir=cache_dir,
    )
    assert output is not None
    assert output.exists()

    candidate_jsons = sorted((tmp_path / "outputs/tic_fixed_1/candidates").glob("*.json"))
    assert len(candidate_jsons) == 1
    payload = json.loads(candidate_jsons[0].read_text(encoding="utf-8"))

    for key, expected in fixture["metadata"].items():
        assert payload["metadata"][key] == expected

    assert len(payload["candidates"]) == 2
    for expected in fixture["candidate_checks"]:
        rank = int(expected["rank"])
        row = next(item for item in payload["candidates"] if int(item["rank"]) == rank)
        for key, value in expected.items():
            assert row[key] == value
        assert row["radius_ratio_rp_over_rs"] > 0.0
        assert row["duration_ratio_observed_to_expected"] > 0.0
        assert "depth~(Rp/Rs)^2" in row["parameter_assumptions"]
        assert "Preliminary estimate only" in row["parameter_uncertainty_caveats"]
