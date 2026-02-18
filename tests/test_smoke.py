from pathlib import Path

import numpy as np

from exohunt import pipeline
from exohunt.cache import (
    _cache_path,
    _prepared_cache_path,
    _segment_prepared_cache_path,
    _safe_target_name,
)
from exohunt.pipeline import fetch_and_plot
from exohunt.plotting import _apply_time_window, _downsample_minmax


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


class _FakeLightCurve:
    def __init__(self):
        self.time = _ArrayValue([1.0, 2.0, 3.0])
        self.flux = _ArrayValue([0.99, 1.01, 1.00])
        self.meta = {"origin": "test", "SECTOR": 14, "AUTHOR": "SPOC", "TIMEDEL": 0.0013888}

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
    assert output_path.exists()


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
    assert output_path.exists()


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

    def _fake_search(target_arg, mission, author=None):
        assert target_arg == target
        assert mission == "TESS"
        assert author in (None, "SPOC")
        return _FakeSearchResult()

    monkeypatch.setattr(pipeline.lk, "search_lightcurve", _fake_search)
    monkeypatch.chdir(tmp_path)

    output_path = fetch_and_plot(target, cache_dir=cache_dir, preprocess_mode="per-sector")
    assert output_path.exists()
    segment_root = cache_dir / "segments" / "tic_261136679"
    assert segment_root.exists()
