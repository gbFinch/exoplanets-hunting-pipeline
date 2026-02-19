import csv
import json
from pathlib import Path

import numpy as np

from exohunt import comparison
from exohunt import pipeline
from exohunt.bls import BLSCandidate, refine_bls_candidates, run_bls_search
from exohunt.cache import (
    _cache_path,
    _prepared_cache_path,
    _segment_prepared_cache_path,
    _safe_target_name,
    _target_output_dir,
)
from exohunt.pipeline import fetch_and_plot
from exohunt.plotting import _apply_time_window, _downsample_minmax, save_candidate_diagnostics
from exohunt.preprocess import compute_preprocessing_quality_metrics
from exohunt.parameters import estimate_candidate_parameters
from exohunt.vetting import vet_bls_candidates


def test_safe_target_name():
    assert _safe_target_name("TIC 261136679") == "tic_261136679"


def test_cache_path():
    assert _cache_path("TIC 261136679", Path("cache")) == Path("cache/tic_261136679.npz")


def test_prepared_cache_path():
    path = _prepared_cache_path(
        target="TIC 261136679",
        cache_dir=Path("cache"),
        outlier_sigma=5.0,
        flatten_window_length=401,
        no_flatten=False,
    )
    assert path.parent == Path("cache")
    assert path.name.startswith("tic_261136679__prep_")
    assert path.suffix == ".npz"


def test_segment_prepared_cache_path():
    path = _segment_prepared_cache_path(
        target="TIC 261136679",
        cache_dir=Path("cache"),
        segment_id="sector_0014__idx_000",
        outlier_sigma=5.0,
        flatten_window_length=401,
        no_flatten=False,
    )
    assert path.parent == Path("cache/segments/tic_261136679")
    assert path.name.startswith("sector_0014__idx_000__prep_")
    assert path.suffix == ".npz"


def test_downsample_minmax_limits_points():
    time = np.arange(1000, dtype=float)
    flux = np.sin(time / 10.0)
    t_ds, f_ds = _downsample_minmax(time, flux, max_points=100)
    assert len(t_ds) <= 100
    assert len(t_ds) == len(f_ds)


def test_apply_time_window_filters_range():
    time = np.asarray([8290.0, 8300.0, 8310.0, 8320.0])
    flux = np.asarray([1.0, 2.0, 3.0, 4.0])
    t, f = _apply_time_window(time, flux, plot_time_start=8300.0, plot_time_end=8310.0)
    assert np.allclose(t, np.asarray([8300.0, 8310.0]))
    assert np.allclose(f, np.asarray([2.0, 3.0]))


class _ArrayValue:
    def __init__(self, values):
        self.value = np.asarray(values, dtype=float)


class _SimpleLC:
    def __init__(self, flux):
        self.flux = _ArrayValue(flux)


class _FakeLightCurve:
    def __init__(self, sector=14, author="SPOC"):
        self.time = _ArrayValue([1.0, 2.0, 3.0])
        self.flux = _ArrayValue([0.99, 1.01, 1.00])
        self.meta = {
            "origin": "test",
            "SECTOR": sector,
            "AUTHOR": author,
            "TIMEDEL": 0.0013888,
        }

    def remove_nans(self):
        return self

    def __truediv__(self, _value):
        return self

    def remove_outliers(self, sigma):
        assert sigma > 0
        return self

    def flatten(self, window_length):
        assert window_length >= 3
        return self


def test_fetch_and_plot_uses_cache(monkeypatch, tmp_path):
    target = "TIC 261136679"
    cache_dir = tmp_path / "cache"
    cache_file = _cache_path(target, cache_dir)
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    np.savez(cache_file, time=np.asarray([1.0, 2.0, 3.0]), flux=np.asarray([0.99, 1.01, 1.00]))

    def _unexpected_search(*args, **kwargs):
        raise AssertionError("search_lightcurve should not be called on cache hit")

    monkeypatch.setattr(pipeline.lk, "search_lightcurve", _unexpected_search)
    monkeypatch.chdir(tmp_path)

    output_path = fetch_and_plot(target, cache_dir=cache_dir, preprocess_mode="global")
    assert output_path is None
    assert (tmp_path / "outputs/metrics/preprocessing_summary.csv").exists()
    assert (
        tmp_path / "outputs/tic_261136679/metrics/preprocessing_summary.json"
    ).exists()


def test_fetch_and_plot_uses_prepared_cache(monkeypatch, tmp_path):
    target = "TIC 261136679"
    cache_dir = tmp_path / "cache"
    prepared_cache = _prepared_cache_path(
        target=target,
        cache_dir=cache_dir,
        outlier_sigma=5.0,
        flatten_window_length=401,
        no_flatten=False,
    )
    prepared_cache.parent.mkdir(parents=True, exist_ok=True)
    np.savez(
        prepared_cache,
        time=np.asarray([1.0, 2.0, 3.0]),
        flux=np.asarray([0.99, 1.01, 1.00]),
    )

    def _unexpected_search(*args, **kwargs):
        raise AssertionError("search_lightcurve should not be called on prepared cache hit")

    monkeypatch.setattr(pipeline.lk, "search_lightcurve", _unexpected_search)
    monkeypatch.chdir(tmp_path)

    output_path = fetch_and_plot(target, cache_dir=cache_dir, preprocess_mode="global")
    assert output_path is None
    assert (tmp_path / "outputs/metrics/preprocessing_summary.csv").exists()
    assert (
        tmp_path / "outputs/tic_261136679/metrics/preprocessing_summary.json"
    ).exists()


def test_fetch_and_plot_downloads_and_caches(monkeypatch, tmp_path):
    target = "TIC 261136679"
    cache_dir = tmp_path / "cache"
    fake_lc = _FakeLightCurve()

    class _FakeLCCollection:
        def __init__(self):
            self.items = [fake_lc]

        def __len__(self):
            return len(self.items)

        def stitch(self):
            return fake_lc

        def __iter__(self):
            return iter(self.items)

    class _FakeSearchResult:
        def __len__(self):
            return 1

        def download_all(self, quality_bitmask):
            assert quality_bitmask == "default"
            return _FakeLCCollection()

    def _fake_search(target_arg, mission, author=None, **kwargs):
        assert target_arg == target
        assert mission == "TESS"
        assert author in (None, "SPOC")
        assert kwargs.get("exptime", 120) == 120
        return _FakeSearchResult()

    monkeypatch.setattr(pipeline.lk, "search_lightcurve", _fake_search)
    monkeypatch.chdir(tmp_path)

    output_path = fetch_and_plot(target, cache_dir=cache_dir, preprocess_mode="per-sector")
    assert output_path is None
    segment_root = cache_dir / "segments" / "tic_261136679"
    assert segment_root.exists()
    assert (tmp_path / "outputs/metrics/preprocessing_summary.csv").exists()
    assert (
        tmp_path / "outputs/tic_261136679/metrics/preprocessing_summary.json"
    ).exists()


def test_fetch_and_plot_runs_bls_per_sector(monkeypatch, tmp_path):
    target = "TIC 261136679"
    cache_dir = tmp_path / "cache"
    lc1 = _FakeLightCurve(sector=14)
    lc2 = _FakeLightCurve(sector=15)

    class _FakeLCCollection:
        def __init__(self):
            self.items = [lc1, lc2]

        def __len__(self):
            return len(self.items)

        def stitch(self):
            return lc1

        def __iter__(self):
            return iter(self.items)

    class _FakeSearchResult:
        def __len__(self):
            return 2

        def download_all(self, quality_bitmask):
            assert quality_bitmask == "default"
            return _FakeLCCollection()

    def _fake_search(target_arg, mission, author=None, **kwargs):
        assert target_arg == target
        assert mission == "TESS"
        assert author in (None, "SPOC")
        assert kwargs.get("exptime", 120) == 120
        return _FakeSearchResult()

    call_count = {"n": 0}

    def _fake_bls_search(**kwargs):
        call_count["n"] += 1
        return []

    monkeypatch.setattr(pipeline.lk, "search_lightcurve", _fake_search)
    monkeypatch.setattr(pipeline, "run_bls_search", _fake_bls_search)
    monkeypatch.chdir(tmp_path)

    fetch_and_plot(
        target,
        cache_dir=cache_dir,
        preprocess_mode="per-sector",
        bls_mode="per-sector",
    )
    assert call_count["n"] == 2


def test_fetch_and_plot_generates_plot_for_plot_sectors(monkeypatch, tmp_path):
    target = "TIC 261136679"
    cache_dir = tmp_path / "cache"
    lc1 = _FakeLightCurve(sector=14)
    lc2 = _FakeLightCurve(sector=15)

    class _FakeLCCollection:
        def __init__(self):
            self.items = [lc1, lc2]

        def __len__(self):
            return len(self.items)

        def stitch(self):
            return lc1

        def __iter__(self):
            return iter(self.items)

    class _FakeSearchResult:
        def __len__(self):
            return 2

        def download_all(self, quality_bitmask):
            assert quality_bitmask == "default"
            return _FakeLCCollection()

    def _fake_search(target_arg, mission, author=None, **kwargs):
        assert target_arg == target
        assert mission == "TESS"
        assert author in (None, "SPOC")
        assert kwargs.get("exptime", 120) == 120
        return _FakeSearchResult()

    monkeypatch.setattr(pipeline.lk, "search_lightcurve", _fake_search)
    monkeypatch.chdir(tmp_path)

    output_path = fetch_and_plot(
        target,
        cache_dir=cache_dir,
        preprocess_mode="per-sector",
        plot_sectors="14",
    )
    assert output_path is not None
    assert output_path.exists()


def test_compute_preprocessing_quality_metrics_improvement():
    raw = _SimpleLC([1.0, 1.04, 0.96, 1.03, 0.97, 1.02, 0.98])
    prepared = _SimpleLC([1.0, 1.01, 0.99, 1.0, 1.0, 1.01, 0.99])

    metrics = compute_preprocessing_quality_metrics(raw, prepared)

    assert metrics.prepared_rms < metrics.raw_rms
    assert metrics.prepared_mad < metrics.raw_mad
    assert metrics.rms_improvement_ratio > 1.0
    assert metrics.mad_improvement_ratio > 1.0
    assert metrics.retained_cadence_fraction == 1.0


def test_fetch_and_plot_reuses_metrics_cache(monkeypatch, tmp_path):
    target = "TIC 261136679"
    cache_dir = tmp_path / "cache"
    prepared_cache = _prepared_cache_path(
        target=target,
        cache_dir=cache_dir,
        outlier_sigma=5.0,
        flatten_window_length=401,
        no_flatten=False,
    )
    prepared_cache.parent.mkdir(parents=True, exist_ok=True)
    np.savez(
        prepared_cache,
        time=np.asarray([1.0, 2.0, 3.0]),
        flux=np.asarray([0.99, 1.01, 1.00]),
    )

    def _unexpected_search(*args, **kwargs):
        raise AssertionError("search_lightcurve should not be called on prepared cache hit")

    monkeypatch.setattr(pipeline.lk, "search_lightcurve", _unexpected_search)
    monkeypatch.chdir(tmp_path)

    first_output = fetch_and_plot(target, cache_dir=cache_dir, preprocess_mode="global")
    assert first_output is None

    def _should_not_compute(*args, **kwargs):
        raise AssertionError("compute_preprocessing_quality_metrics should not run on cache hit")

    monkeypatch.setattr(pipeline, "compute_preprocessing_quality_metrics", _should_not_compute)
    second_output = fetch_and_plot(target, cache_dir=cache_dir, preprocess_mode="global")
    assert second_output is None


def test_fetch_and_plot_generates_plot_when_time_window_provided(monkeypatch, tmp_path):
    target = "TIC 261136679"
    cache_dir = tmp_path / "cache"
    cache_file = _cache_path(target, cache_dir)
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    np.savez(cache_file, time=np.asarray([1.0, 2.0, 3.0]), flux=np.asarray([0.99, 1.01, 1.00]))

    def _unexpected_search(*args, **kwargs):
        raise AssertionError("search_lightcurve should not be called on cache hit")

    monkeypatch.setattr(pipeline.lk, "search_lightcurve", _unexpected_search)
    monkeypatch.chdir(tmp_path)

    output_path = fetch_and_plot(
        target,
        cache_dir=cache_dir,
        preprocess_mode="global",
        plot_time_start=1.0,
        plot_time_end=3.0,
    )
    assert output_path is not None
    assert output_path.exists()
    assert output_path.parent == Path("outputs/tic_261136679/plots")


def test_preprocessing_summary_csv_column_order_stable(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    metrics_ordered = {
        "n_points_raw": 10,
        "n_points_prepared": 9,
        "retained_cadence_fraction": 0.9,
        "raw_rms": 1.0,
        "prepared_rms": 0.5,
        "raw_mad": 0.8,
        "prepared_mad": 0.4,
        "raw_trend_proxy": 2.0,
        "prepared_trend_proxy": 0.1,
        "rms_improvement_ratio": 2.0,
        "mad_improvement_ratio": 2.0,
        "trend_improvement_ratio": 20.0,
    }
    metrics_reversed = dict(reversed(list(metrics_ordered.items())))

    pipeline._write_preprocessing_metrics(
        target="TIC 1",
        preprocess_mode="global",
        outlier_sigma=5.0,
        flatten_window_length=401,
        no_flatten=False,
        data_source="raw-cache",
        metrics=metrics_ordered,
    )
    pipeline._write_preprocessing_metrics(
        target="TIC 1",
        preprocess_mode="global",
        outlier_sigma=5.0,
        flatten_window_length=401,
        no_flatten=False,
        data_source="raw-cache",
        metrics=metrics_reversed,
    )

    csv_path = tmp_path / "outputs/metrics/preprocessing_summary.csv"
    with csv_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
        assert reader.fieldnames == pipeline._PREPROCESSING_SUMMARY_COLUMNS

    target_csv_path = tmp_path / "outputs/tic_1/metrics/preprocessing_summary.csv"
    with target_csv_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        target_rows = list(reader)
        assert reader.fieldnames == pipeline._PREPROCESSING_SUMMARY_COLUMNS

    assert len(target_rows) == 2
    assert len(rows) == 2
    assert float(rows[0]["raw_rms"]) == 1.0
    assert float(rows[0]["prepared_rms"]) == 0.5
    assert float(rows[1]["raw_rms"]) == 1.0
    assert float(rows[1]["prepared_rms"]) == 0.5


def test_build_preprocessing_comparison_report(tmp_path):
    metrics_csv = tmp_path / "outputs/metrics/preprocessing_summary.csv"
    metrics_csv.parent.mkdir(parents=True, exist_ok=True)
    metrics_csv.write_text(
        "\n".join(
            [
                "run_utc,target,preprocess_mode,data_source,outlier_sigma,flatten_window_length,no_flatten,n_points_raw,n_points_prepared,retained_cadence_fraction,raw_rms,prepared_rms,raw_mad,prepared_mad,raw_trend_proxy,prepared_trend_proxy,rms_improvement_ratio,mad_improvement_ratio,trend_improvement_ratio",
                "2026-02-18T00:00:00+00:00,TIC 1,per-sector,segment-cache,5.0,401,False,100,100,1.0,1,0.02,1,0.02,1,0.01,50,50,100",
                "2026-02-18T00:01:00+00:00,TIC 1,per-sector,segment-cache,5.0,1604,False,100,100,1.0,1,0.04,1,0.04,1,0.005,25,25,200",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    segment_root = tmp_path / "cache/segments/tic_1"
    segment_root.mkdir(parents=True, exist_ok=True)
    manifest_payload = {
        "target": "TIC 1",
        "segments": [
            {
                "segment_id": "sector_0001__idx_000",
                "sector": 1,
                "author": "SPOC",
                "cadence": 0.0013889,
            }
        ],
    }
    (segment_root / "manifest.json").write_text(json.dumps(manifest_payload), encoding="utf-8")
    np.savez(
        segment_root / "sector_0001__idx_000__raw.npz",
        time=np.asarray([0.0, 27.0]),
        flux=np.asarray([1.0, 1.0]),
    )

    report_path = comparison.build_preprocessing_comparison_report(
        metrics_csv_path=metrics_csv,
        cache_dir=tmp_path / "cache",
        report_path=tmp_path / "outputs/reports/preprocessing-method-comparison.md",
    )
    assert report_path.exists()
    report = report_path.read_text(encoding="utf-8")
    assert "short-cadence" in report
    assert "standard-span" in report
    assert "`sigma=5,window=401,flatten=on`" in report


class _BLSLC:
    def __init__(self, time, flux):
        self.time = _ArrayValue(time)
        self.flux = _ArrayValue(flux)


def test_run_bls_search_detects_injected_period():
    period_days = 3.2
    duration_days = 3.0 / 24.0
    depth = 0.01
    time = np.arange(0.0, 60.0, 0.02)
    phase = np.mod(time - 0.4 * period_days, period_days)
    in_transit = (phase < duration_days) | (phase > period_days - duration_days)
    flux = np.ones_like(time)
    flux[in_transit] -= depth
    lc = _BLSLC(time=time, flux=flux)

    candidates = run_bls_search(
        lc_prepared=lc,
        period_min_days=0.5,
        period_max_days=10.0,
        duration_min_hours=1.0,
        duration_max_hours=6.0,
        n_periods=1200,
        n_durations=10,
        top_n=3,
    )

    assert len(candidates) >= 1
    best = candidates[0]
    assert abs(best.period_days - period_days) / period_days < 0.03
    assert best.depth_ppm > 5000


def test_run_bls_search_short_series_returns_empty():
    lc = _BLSLC(time=np.asarray([1.0, 2.0, 3.0]), flux=np.asarray([1.0, 1.0, 1.0]))
    assert run_bls_search(lc_prepared=lc) == []


def test_refine_bls_candidates_improves_period_estimate():
    true_period = 3.14159
    duration_days = 2.5 / 24.0
    time = np.arange(0.0, 120.0, 0.02)
    phase = np.mod(time - 0.35 * true_period, true_period)
    in_transit = (phase < duration_days) | (phase > true_period - duration_days)
    flux = np.ones_like(time)
    flux[in_transit] -= 0.008
    lc = _BLSLC(time=time, flux=flux)

    coarse = run_bls_search(
        lc_prepared=lc,
        period_min_days=0.5,
        period_max_days=10.0,
        duration_min_hours=1.0,
        duration_max_hours=6.0,
        n_periods=250,
        n_durations=8,
        top_n=1,
    )
    assert coarse
    coarse_err = abs(coarse[0].period_days - true_period)
    refined = refine_bls_candidates(
        lc_prepared=lc,
        candidates=coarse,
        period_min_days=0.5,
        period_max_days=10.0,
        duration_min_hours=1.0,
        duration_max_hours=6.0,
        n_periods=12000,
        n_durations=20,
        window_fraction=0.03,
    )
    assert refined
    refined_err = abs(refined[0].period_days - true_period)
    assert refined_err <= coarse_err


def test_vet_bls_candidates_flags_alias_harmonic():
    lc = _BLSLC(time=np.arange(0.0, 40.0, 0.02), flux=np.ones(2000))
    candidates = [
        BLSCandidate(
            rank=1,
            period_days=2.0,
            duration_hours=3.0,
            depth=0.001,
            depth_ppm=1000.0,
            power=10.0,
            transit_time=0.2,
            transit_count_estimate=20.0,
        ),
        BLSCandidate(
            rank=2,
            period_days=4.0,
            duration_hours=3.0,
            depth=0.0008,
            depth_ppm=800.0,
            power=5.0,
            transit_time=0.2,
            transit_count_estimate=10.0,
        ),
    ]
    vet = vet_bls_candidates(lc_prepared=lc, candidates=candidates)
    assert vet[1].pass_alias_harmonic
    assert not vet[2].pass_alias_harmonic
    assert "alias_or_harmonic" in vet[2].vetting_reasons


def test_vet_bls_candidates_flags_odd_even_mismatch():
    period_days = 2.0
    duration_days = 2.5 / 24.0
    time = np.arange(0.0, 80.0, 0.01)
    flux = np.ones_like(time)
    cycles = np.round((time - 0.4) / period_days).astype(int)
    centers = 0.4 + cycles * period_days
    in_window = np.abs(time - centers) <= 0.5 * duration_days
    odd = (cycles % 2) == 1
    flux[in_window & odd] -= 0.004
    flux[in_window & ~odd] -= 0.010
    lc = _BLSLC(time=time, flux=flux)
    candidate = BLSCandidate(
        rank=1,
        period_days=period_days,
        duration_hours=duration_days * 24.0,
        depth=0.008,
        depth_ppm=8000.0,
        power=8.0,
        transit_time=0.4,
        transit_count_estimate=40.0,
    )
    vet = vet_bls_candidates(lc_prepared=lc, candidates=[candidate])
    assert not vet[1].pass_odd_even_depth
    assert vet[1].odd_even_depth_mismatch_fraction > 0.3


def test_estimate_candidate_parameters_returns_radius_ratio_and_duration_check():
    candidate = BLSCandidate(
        rank=1,
        period_days=3.2,
        duration_hours=2.5,
        depth=1.21e-4,
        depth_ppm=121.0,
        power=0.02,
        transit_time=0.3,
        transit_count_estimate=10.0,
    )
    estimates = estimate_candidate_parameters([candidate])
    assert 1 in estimates
    estimate = estimates[1]
    assert abs(estimate.radius_ratio_rp_over_rs - 0.011) < 1e-6
    assert estimate.radius_earth_radii_solar_assumption > 1.0
    assert estimate.duration_expected_hours_central_solar_density > 0.0
    assert estimate.duration_ratio_observed_to_expected > 0.0
    assert isinstance(estimate.pass_duration_plausibility, bool)
    assert "depth~(Rp/Rs)^2" in estimate.parameter_assumptions
    assert "Preliminary estimate only" in estimate.parameter_uncertainty_caveats


def test_save_candidate_diagnostics_writes_assets(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    lc = _BLSLC(
        time=np.linspace(0.0, 10.0, 1000),
        flux=1.0 + 1e-4 * np.sin(np.linspace(0.0, 20.0 * np.pi, 1000)),
    )
    candidates = [
        BLSCandidate(
            rank=1,
            period_days=2.5,
            duration_hours=3.0,
            depth=1.0e-4,
            depth_ppm=100.0,
            power=1.0e-3,
            transit_time=0.2,
            transit_count_estimate=4.0,
        )
    ]
    written = save_candidate_diagnostics(
        target="TIC 1",
        output_key="xyz",
        lc_prepared=lc,
        candidates=candidates,
        period_grid_days=np.linspace(0.5, 5.0, 300),
        power_grid=np.linspace(0.0, 0.01, 300),
    )
    assert len(written) == 1
    periodogram_path, phasefold_path = written[0]
    assert periodogram_path.exists()
    assert phasefold_path.exists()
    assert periodogram_path.parent == _target_output_dir("TIC 1") / "diagnostics"
    assert phasefold_path.parent == _target_output_dir("TIC 1") / "diagnostics"


def test_write_bls_candidates_outputs_structured_files(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    candidates = [
        BLSCandidate(
            rank=1,
            period_days=3.2,
            duration_hours=2.5,
            depth=1.2e-4,
            depth_ppm=120.0,
            power=0.03,
            transit_time=100.0,
            transit_count_estimate=25.0,
        )
    ]
    metadata = {
        "run_utc": "2026-02-18T00:00:00+00:00",
        "target": "TIC 1",
        "preprocess_mode": "per-sector",
        "data_source": "segment-cache",
        "outlier_sigma": 5.0,
        "flatten_window_length": 401,
        "no_flatten": False,
        "sectors": "all",
        "authors": "SPOC",
        "n_points_raw": 1000,
        "n_points_prepared": 990,
        "time_min_btjd": 0.0,
        "time_max_btjd": 30.0,
        "bls_enabled": True,
        "bls_period_min_days": 0.5,
        "bls_period_max_days": 20.0,
        "bls_duration_min_hours": 0.5,
        "bls_duration_max_hours": 10.0,
        "bls_n_periods": 2000,
        "bls_n_durations": 12,
        "bls_top_n": 5,
        "parameter_estimation_enabled": True,
        "parameter_stellar_density_kg_m3": 1408.0,
        "parameter_duration_ratio_min": 0.05,
        "parameter_duration_ratio_max": 1.8,
    }

    csv_path, json_path = pipeline._write_bls_candidates(
        target="TIC 1",
        output_key="abc123",
        metadata=metadata,
        candidates=candidates,
        parameter_estimates_by_rank=estimate_candidate_parameters(candidates),
    )

    assert csv_path.exists()
    assert json_path.exists()
    assert csv_path.parent == _target_output_dir("TIC 1") / "candidates"
    assert json_path.parent == _target_output_dir("TIC 1") / "candidates"
    with csv_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
    assert len(rows) == 1
    assert float(rows[0]["period_days"]) == 3.2
    assert float(rows[0]["radius_ratio_rp_over_rs"]) > 0.0
    assert rows[0]["parameter_assumptions"] != ""
    assert rows[0]["parameter_uncertainty_caveats"] != ""
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["metadata"]["target"] == "TIC 1"
    assert payload["metadata"]["parameter_estimation_enabled"]
    assert payload["candidates"][0]["rank"] == 1
    assert payload["candidates"][0]["radius_ratio_rp_over_rs"] > 0.0
