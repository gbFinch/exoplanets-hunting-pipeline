from __future__ import annotations

import logging
from pathlib import Path
from time import perf_counter

import lightkurve as lk
import numpy as np

from exohunt.cache import (
    _cache_path,
    _load_npz_lightcurve,
    _load_segment_manifest,
    _prepared_cache_path,
    _save_npz_lightcurve,
    _segment_base_dir,
    _segment_prepared_cache_path,
    _segment_raw_cache_path,
    _write_segment_manifest,
)
from exohunt.ingest import _extract_segments, _parse_authors, _parse_sectors
from exohunt.models import LightCurveSegment
from exohunt.plotting import save_raw_vs_prepared_plot, save_raw_vs_prepared_plot_interactive
from exohunt.preprocess import prepare_lightcurve
from exohunt.progress import _render_progress


LOGGER = logging.getLogger(__name__)


def _stitch_segments(lightcurves: list[lk.LightCurve]) -> tuple[lk.LightCurve, list[float]]:
    if not lightcurves:
        raise RuntimeError("No light-curve segments available to stitch.")
    ordered = sorted(lightcurves, key=lambda item: float(np.nanmin(item.time.value)))
    time_parts = []
    flux_parts = []
    boundaries: list[float] = []
    for idx, lc in enumerate(ordered):
        time_values = np.asarray(lc.time.value, dtype=float)
        flux_values = np.asarray(lc.flux.value, dtype=float)
        if time_values.size == 0:
            continue
        if idx > 0:
            boundaries.append(float(time_values[0]))
        time_parts.append(time_values)
        flux_parts.append(flux_values)
    if not time_parts:
        raise RuntimeError("All stitched segments were empty after preprocessing.")
    stitched = lk.LightCurve(time=np.concatenate(time_parts), flux=np.concatenate(flux_parts))
    return stitched, boundaries


def fetch_and_plot(
    target: str,
    cache_dir: Path,
    refresh_cache: bool = False,
    outlier_sigma: float = 5.0,
    flatten_window_length: int = 401,
    max_download_files: int | None = None,
    no_flatten: bool = False,
    preprocess_mode: str = "per-sector",
    sectors: str | None = None,
    authors: str | None = None,
    interactive_html: bool = False,
    interactive_max_points: int = 200_000,
    plot_time_start: float | None = None,
    plot_time_end: float | None = None,
) -> Path:
    started_at = perf_counter()
    selected_sectors = _parse_sectors(sectors)
    selected_authors = _parse_authors(authors)
    boundaries: list[float] = []
    data_source = "download"

    if preprocess_mode == "global":
        raw_cache_path = _cache_path(target, cache_dir)
        prepared_cache_path = _prepared_cache_path(
            target=target,
            cache_dir=cache_dir,
            outlier_sigma=outlier_sigma,
            flatten_window_length=flatten_window_length,
            no_flatten=no_flatten,
        )

        lc = None
        lc_prepared = None
        LOGGER.info("Step 1/5: checking cache")
        if prepared_cache_path.exists() and not refresh_cache:
            try:
                step_started = perf_counter()
                lc_prepared = _load_npz_lightcurve(prepared_cache_path)
                data_source = "prepared-cache"
                LOGGER.info(
                    "Prepared cache hit: loaded %s in %.2fs",
                    prepared_cache_path,
                    perf_counter() - step_started,
                )
            except Exception as exc:
                LOGGER.warning(
                    "Prepared cache read failed for %s (%s); recomputing.",
                    prepared_cache_path,
                    exc,
                )

        if raw_cache_path.exists() and not refresh_cache:
            try:
                step_started = perf_counter()
                lc = _load_npz_lightcurve(raw_cache_path)
                if data_source == "download":
                    data_source = "raw-cache"
                LOGGER.info(
                    "Raw cache hit: loaded %s in %.2fs",
                    raw_cache_path,
                    perf_counter() - step_started,
                )
            except Exception as exc:
                LOGGER.warning("Raw cache read failed for %s (%s); re-downloading.", raw_cache_path, exc)

        if lc is None and lc_prepared is None:
            LOGGER.info("Step 2/5: searching TESS products")
            step_started = perf_counter()
            search = lk.search_lightcurve(target, mission="TESS", author="SPOC")
            LOGGER.info("Search complete in %.2fs (%d entries)", perf_counter() - step_started, len(search))
            if len(search) == 0:
                raise RuntimeError(f"No TESS light curves found for target: {target}")
            if max_download_files is not None and len(search) > max_download_files:
                LOGGER.info("Limiting download to first %d entries (of %d).", max_download_files, len(search))
                search = search[:max_download_files]

            LOGGER.info("Step 3/5: downloading and stitching light curves")
            step_started = perf_counter()
            lcs = search.download_all(quality_bitmask="default")
            if lcs is None or len(lcs) == 0:
                raise RuntimeError(f"Failed to download TESS light curve for target: {target}")
            lc = lcs.stitch().remove_nans()
            LOGGER.info("Download+stitch complete in %.2fs", perf_counter() - step_started)

            LOGGER.info("Writing raw cache: %s", raw_cache_path)
            _save_npz_lightcurve(raw_cache_path, lc)

        if lc_prepared is None:
            LOGGER.info("Step 4/5: preprocessing light curve")
            step_started = perf_counter()
            lc_prepared = prepare_lightcurve(
                lc,
                outlier_sigma=outlier_sigma,
                flatten_window_length=flatten_window_length,
                apply_flatten=not no_flatten,
            )
            LOGGER.info("Preprocessing complete in %.2fs", perf_counter() - step_started)
            LOGGER.info("Writing prepared cache: %s", prepared_cache_path)
            _save_npz_lightcurve(prepared_cache_path, lc_prepared)
        elif lc is None:
            lc = lc_prepared
    else:
        LOGGER.info("Step 1/5: checking per-segment cache manifest")
        raw_segments: list[LightCurveSegment] = []
        prepared_segments: list[LightCurveSegment] = []
        manifest_rows = [] if refresh_cache else _load_segment_manifest(target, cache_dir)

        for row in manifest_rows:
            segment_id = str(row.get("segment_id"))
            sector = int(row.get("sector", -1))
            author = str(row.get("author", "UNKNOWN")).upper()
            cadence = float(row.get("cadence", np.nan))
            if selected_sectors is not None and sector not in selected_sectors:
                continue
            if selected_authors is not None and author not in selected_authors:
                continue
            raw_path = _segment_raw_cache_path(target, cache_dir, segment_id)
            prep_path = _segment_prepared_cache_path(
                target,
                cache_dir,
                segment_id,
                outlier_sigma=outlier_sigma,
                flatten_window_length=flatten_window_length,
                no_flatten=no_flatten,
            )
            try:
                if prep_path.exists():
                    prepared_segments.append(
                        LightCurveSegment(
                            segment_id=segment_id,
                            sector=sector,
                            author=author,
                            cadence=cadence,
                            lc=_load_npz_lightcurve(prep_path),
                        )
                    )
                if raw_path.exists():
                    raw_segments.append(
                        LightCurveSegment(
                            segment_id=segment_id,
                            sector=sector,
                            author=author,
                            cadence=cadence,
                            lc=_load_npz_lightcurve(raw_path),
                        )
                    )
            except Exception as exc:
                LOGGER.warning("Segment cache read failed (%s): %s", segment_id, exc)

        if not raw_segments:
            LOGGER.info("Step 2/5: searching TESS products")
            step_started = perf_counter()
            search = lk.search_lightcurve(target, mission="TESS", author="SPOC")
            LOGGER.info("Search complete in %.2fs (%d entries)", perf_counter() - step_started, len(search))
            if len(search) == 0:
                raise RuntimeError(f"No TESS light curves found for target: {target}")
            if max_download_files is not None and len(search) > max_download_files:
                LOGGER.info("Limiting download to first %d entries (of %d).", max_download_files, len(search))
                search = search[:max_download_files]

            LOGGER.info("Step 3/5: downloading segment light curves")
            step_started = perf_counter()
            lcs = search.download_all(quality_bitmask="default")
            if lcs is None or len(lcs) == 0:
                raise RuntimeError(f"Failed to download TESS light curve for target: {target}")
            raw_segments = _extract_segments(
                lcs,
                selected_sectors=selected_sectors,
                selected_authors=selected_authors,
            )
            if not raw_segments:
                raise RuntimeError("No segments remain after sector/author filters.")
            LOGGER.info("Download complete in %.2fs (%d segments)", perf_counter() - step_started, len(raw_segments))
            _write_segment_manifest(target, cache_dir, raw_segments)
            for segment in raw_segments:
                raw_path = _segment_raw_cache_path(target, cache_dir, segment.segment_id)
                _save_npz_lightcurve(raw_path, segment.lc)
            data_source = "download"
        else:
            data_source = "segment-cache"
            LOGGER.info("Loaded %d raw segments from cache", len(raw_segments))

        if len(prepared_segments) != len(raw_segments):
            LOGGER.info("Step 4/5: preprocessing segment light curves")
            prep_map = {segment.segment_id: segment for segment in prepared_segments}
            rebuilt_prepared: list[LightCurveSegment] = []
            total_segments = len(raw_segments)
            for idx, segment in enumerate(raw_segments, start=1):
                cached = prep_map.get(segment.segment_id)
                if cached is not None:
                    rebuilt_prepared.append(cached)
                    _render_progress("Prepared segments", idx, total_segments)
                    continue
                prepared_lc = prepare_lightcurve(
                    segment.lc,
                    outlier_sigma=outlier_sigma,
                    flatten_window_length=flatten_window_length,
                    apply_flatten=not no_flatten,
                )
                prep_segment = LightCurveSegment(
                    segment_id=segment.segment_id,
                    sector=segment.sector,
                    author=segment.author,
                    cadence=segment.cadence,
                    lc=prepared_lc,
                )
                rebuilt_prepared.append(prep_segment)
                prep_path = _segment_prepared_cache_path(
                    target,
                    cache_dir,
                    segment.segment_id,
                    outlier_sigma=outlier_sigma,
                    flatten_window_length=flatten_window_length,
                    no_flatten=no_flatten,
                )
                _save_npz_lightcurve(prep_path, prepared_lc)
                _render_progress("Prepared segments", idx, total_segments)
            prepared_segments = rebuilt_prepared
        else:
            LOGGER.info("Step 4/5: skipping preprocessing (prepared segment cache hit)")

        lc, boundaries = _stitch_segments([segment.lc for segment in raw_segments])
        lc_prepared, _ = _stitch_segments([segment.lc for segment in prepared_segments])

        raw_cache_path = _segment_base_dir(target, cache_dir)
        prepared_cache_path = _segment_base_dir(target, cache_dir)

    n_points_raw = len(lc.time.value)
    n_points_prepared = len(lc_prepared.time.value)
    time_min = float(lc_prepared.time.value.min())
    time_max = float(lc_prepared.time.value.max())

    LOGGER.info("Step 5/5: generating plot")
    step_started = perf_counter()
    output_path = save_raw_vs_prepared_plot(
        target=target,
        lc_raw=lc,
        lc_prepared=lc_prepared,
        boundaries=boundaries,
        plot_time_start=plot_time_start,
        plot_time_end=plot_time_end,
    )
    interactive_path = None
    if interactive_html:
        interactive_path = save_raw_vs_prepared_plot_interactive(
            target=target,
            lc_raw=lc,
            lc_prepared=lc_prepared,
            boundaries=boundaries,
            max_points=interactive_max_points,
            plot_time_start=plot_time_start,
            plot_time_end=plot_time_end,
        )
    LOGGER.info("Plot complete in %.2fs", perf_counter() - step_started)

    LOGGER.info("--------------------------------")
    LOGGER.info("Target: %s", target)
    LOGGER.info("Preprocess mode: %s", preprocess_mode)
    LOGGER.info("Points (raw -> prepared): %d -> %d", n_points_raw, n_points_prepared)
    LOGGER.info("Time range (BTJD): %.5f -> %.5f", time_min, time_max)
    LOGGER.info("Data source: %s", data_source)
    LOGGER.info("Raw cache file: %s", raw_cache_path)
    LOGGER.info("Prepared cache file: %s", prepared_cache_path)
    LOGGER.info(
        "Prep params: outlier_sigma=%.2f flatten_window_length=%d no_flatten=%s",
        outlier_sigma,
        flatten_window_length,
        no_flatten,
    )
    LOGGER.info("Max download files: %s", max_download_files if max_download_files is not None else "all")
    LOGGER.info("Sector filter: %s", sectors if sectors else "all")
    LOGGER.info("Author filter: %s", authors if authors else "all")
    LOGGER.info("Plot time start (BJD-2450000): %s", plot_time_start if plot_time_start is not None else "auto")
    LOGGER.info("Plot time end (BJD-2450000): %s", plot_time_end if plot_time_end is not None else "auto")
    LOGGER.info("Interactive HTML: %s", interactive_html)
    LOGGER.info("Interactive max points: %d", interactive_max_points)
    LOGGER.info("Total runtime: %.2fs", perf_counter() - started_at)
    LOGGER.info("Saved plot: %s", output_path)
    if interactive_path is not None:
        LOGGER.info("Saved interactive plot: %s", interactive_path)
    LOGGER.info("--------------------------------")

    return output_path
