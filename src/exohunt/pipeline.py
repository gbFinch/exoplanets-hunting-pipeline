from __future__ import annotations

import csv
import hashlib
import json
import logging
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter

import lightkurve as lk
import numpy as np

from exohunt.cache import (
    _cache_path,
    _load_npz_lightcurve,
    _load_segment_manifest,
    _prepared_cache_path,
    _safe_target_name,
    _save_npz_lightcurve,
    _segment_base_dir,
    _segment_prepared_cache_path,
    _segment_raw_cache_path,
    _write_segment_manifest,
)
from exohunt.bls import BLSCandidate, run_bls_search
from exohunt.ingest import _extract_segments, _parse_authors, _parse_sectors
from exohunt.models import LightCurveSegment
from exohunt.plotting import save_raw_vs_prepared_plot, save_raw_vs_prepared_plot_interactive
from exohunt.preprocess import compute_preprocessing_quality_metrics, prepare_lightcurve
from exohunt.progress import _render_progress


LOGGER = logging.getLogger(__name__)

_PREPROCESSING_METRICS_COLUMNS = [
    "n_points_raw",
    "n_points_prepared",
    "retained_cadence_fraction",
    "raw_rms",
    "prepared_rms",
    "raw_mad",
    "prepared_mad",
    "raw_trend_proxy",
    "prepared_trend_proxy",
    "rms_improvement_ratio",
    "mad_improvement_ratio",
    "trend_improvement_ratio",
]

_PREPROCESSING_SUMMARY_COLUMNS = [
    "run_utc",
    "target",
    "preprocess_mode",
    "data_source",
    "outlier_sigma",
    "flatten_window_length",
    "no_flatten",
    *_PREPROCESSING_METRICS_COLUMNS,
]

_CANDIDATE_COLUMNS = [
    "rank",
    "period_days",
    "duration_hours",
    "depth",
    "depth_ppm",
    "power",
    "transit_time",
    "transit_count_estimate",
]


def _metrics_cache_path(
    target: str,
    cache_dir: Path,
    preprocess_mode: str,
    outlier_sigma: float,
    flatten_window_length: int,
    no_flatten: bool,
    sectors: str | None,
    authors: str | None,
    raw_n_points: int,
    prepared_n_points: int,
    raw_time_min: float,
    raw_time_max: float,
    prepared_time_min: float,
    prepared_time_max: float,
) -> Path:
    payload = {
        "version": 1,
        "target": target,
        "preprocess_mode": preprocess_mode,
        "outlier_sigma": round(float(outlier_sigma), 6),
        "flatten_window_length": int(flatten_window_length),
        "no_flatten": bool(no_flatten),
        "sectors": sectors or "",
        "authors": authors or "",
        "raw_n_points": int(raw_n_points),
        "prepared_n_points": int(prepared_n_points),
        "raw_time_min": round(float(raw_time_min), 7),
        "raw_time_max": round(float(raw_time_max), 7),
        "prepared_time_min": round(float(prepared_time_min), 7),
        "prepared_time_max": round(float(prepared_time_max), 7),
    }
    encoded = json.dumps(payload, sort_keys=True).encode("utf-8")
    key = hashlib.sha1(encoded).hexdigest()[:16]
    return cache_dir / "metrics" / f"{_safe_target_name(target)}__metrics_{key}.json"


def _load_cached_metrics(metrics_cache_path: Path) -> dict[str, float | int] | None:
    if not metrics_cache_path.exists():
        return None
    try:
        payload = json.loads(metrics_cache_path.read_text(encoding="utf-8"))
    except Exception as exc:
        LOGGER.warning(
            "Metrics cache read failed for %s (%s); recomputing.", metrics_cache_path, exc
        )
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def _save_cached_metrics(metrics_cache_path: Path, metrics: dict[str, float | int]) -> None:
    metrics_cache_path.parent.mkdir(parents=True, exist_ok=True)
    metrics_cache_path.write_text(json.dumps(metrics, sort_keys=True, indent=2), encoding="utf-8")


def _write_preprocessing_metrics(
    target: str,
    preprocess_mode: str,
    outlier_sigma: float,
    flatten_window_length: int,
    no_flatten: bool,
    data_source: str,
    metrics: dict[str, float | int | str],
) -> tuple[Path, Path]:
    output_dir = Path("outputs/metrics")
    output_dir.mkdir(parents=True, exist_ok=True)
    run_utc = datetime.now(tz=timezone.utc).isoformat()

    row = {
        "run_utc": run_utc,
        "target": target,
        "preprocess_mode": preprocess_mode,
        "data_source": data_source,
        "outlier_sigma": float(outlier_sigma),
        "flatten_window_length": int(flatten_window_length),
        "no_flatten": bool(no_flatten),
    }
    for key in _PREPROCESSING_METRICS_COLUMNS:
        if key not in metrics:
            raise KeyError(f"Missing preprocessing metric key: {key}")
        row[key] = metrics[key]

    csv_path = output_dir / "preprocessing_summary.csv"
    write_header = not csv_path.exists()
    with csv_path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=_PREPROCESSING_SUMMARY_COLUMNS)
        if write_header:
            writer.writeheader()
        writer.writerow(row)

    json_path = output_dir / f"{_safe_target_name(target)}_preprocessing_summary.json"
    json_path.write_text(json.dumps(row, indent=2, sort_keys=True), encoding="utf-8")
    return csv_path, json_path


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


def _candidate_output_key(
    target: str,
    preprocess_mode: str,
    outlier_sigma: float,
    flatten_window_length: int,
    no_flatten: bool,
    run_bls: bool,
    bls_period_min_days: float,
    bls_period_max_days: float,
    bls_duration_min_hours: float,
    bls_duration_max_hours: float,
    bls_n_periods: int,
    bls_n_durations: int,
    bls_top_n: int,
    sectors: str | None,
    authors: str | None,
    n_points_prepared: int,
    time_min: float,
    time_max: float,
) -> str:
    payload = {
        "version": 1,
        "target": target,
        "preprocess_mode": preprocess_mode,
        "outlier_sigma": round(float(outlier_sigma), 6),
        "flatten_window_length": int(flatten_window_length),
        "no_flatten": bool(no_flatten),
        "run_bls": bool(run_bls),
        "bls_period_min_days": round(float(bls_period_min_days), 6),
        "bls_period_max_days": round(float(bls_period_max_days), 6),
        "bls_duration_min_hours": round(float(bls_duration_min_hours), 6),
        "bls_duration_max_hours": round(float(bls_duration_max_hours), 6),
        "bls_n_periods": int(bls_n_periods),
        "bls_n_durations": int(bls_n_durations),
        "bls_top_n": int(bls_top_n),
        "sectors": sectors or "",
        "authors": authors or "",
        "n_points_prepared": int(n_points_prepared),
        "time_min": round(float(time_min), 7),
        "time_max": round(float(time_max), 7),
    }
    encoded = json.dumps(payload, sort_keys=True).encode("utf-8")
    return hashlib.sha1(encoded).hexdigest()[:12]


def _write_bls_candidates(
    target: str,
    output_key: str,
    metadata: dict[str, str | int | float | bool],
    candidates: list[BLSCandidate],
) -> tuple[Path, Path]:
    output_dir = Path("outputs/candidates")
    output_dir.mkdir(parents=True, exist_ok=True)
    base_name = f"{_safe_target_name(target)}__bls_{output_key}"
    csv_path = output_dir / f"{base_name}.csv"
    json_path = output_dir / f"{base_name}.json"

    csv_columns = list(metadata.keys()) + _CANDIDATE_COLUMNS
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=csv_columns)
        writer.writeheader()
        for candidate in candidates:
            row = dict(metadata)
            row.update(asdict(candidate))
            writer.writerow(row)

    payload = {
        "metadata": metadata,
        "candidates": [asdict(candidate) for candidate in candidates],
    }
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return csv_path, json_path


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
    run_bls: bool = True,
    bls_period_min_days: float = 0.5,
    bls_period_max_days: float = 20.0,
    bls_duration_min_hours: float = 0.5,
    bls_duration_max_hours: float = 10.0,
    bls_n_periods: int = 2000,
    bls_n_durations: int = 12,
    bls_top_n: int = 5,
) -> Path | None:
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
                LOGGER.warning(
                    "Raw cache read failed for %s (%s); re-downloading.", raw_cache_path, exc
                )

        if lc is None and lc_prepared is None:
            LOGGER.info("Step 2/5: searching TESS products")
            step_started = perf_counter()
            search = lk.search_lightcurve(target, mission="TESS", author="SPOC")
            LOGGER.info(
                "Search complete in %.2fs (%d entries)", perf_counter() - step_started, len(search)
            )
            if len(search) == 0:
                raise RuntimeError(f"No TESS light curves found for target: {target}")
            if max_download_files is not None and len(search) > max_download_files:
                LOGGER.info(
                    "Limiting download to first %d entries (of %d).",
                    max_download_files,
                    len(search),
                )
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
            LOGGER.info(
                "Search complete in %.2fs (%d entries)", perf_counter() - step_started, len(search)
            )
            if len(search) == 0:
                raise RuntimeError(f"No TESS light curves found for target: {target}")
            if max_download_files is not None and len(search) > max_download_files:
                LOGGER.info(
                    "Limiting download to first %d entries (of %d).",
                    max_download_files,
                    len(search),
                )
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
            LOGGER.info(
                "Download complete in %.2fs (%d segments)",
                perf_counter() - step_started,
                len(raw_segments),
            )
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
    raw_time_min = float(np.nanmin(lc.time.value))
    raw_time_max = float(np.nanmax(lc.time.value))
    time_min = float(lc_prepared.time.value.min())
    time_max = float(lc_prepared.time.value.max())
    metrics_cache_path = _metrics_cache_path(
        target=target,
        cache_dir=cache_dir,
        preprocess_mode=preprocess_mode,
        outlier_sigma=outlier_sigma,
        flatten_window_length=flatten_window_length,
        no_flatten=no_flatten,
        sectors=sectors,
        authors=authors,
        raw_n_points=n_points_raw,
        prepared_n_points=n_points_prepared,
        raw_time_min=raw_time_min,
        raw_time_max=raw_time_max,
        prepared_time_min=time_min,
        prepared_time_max=time_max,
    )
    metrics_payload = _load_cached_metrics(metrics_cache_path)
    metrics_cache_hit = metrics_payload is not None
    if metrics_payload is None:
        preprocessing_metrics = compute_preprocessing_quality_metrics(lc, lc_prepared)
        metrics_payload = asdict(preprocessing_metrics)
        _save_cached_metrics(metrics_cache_path, metrics_payload)
    else:
        LOGGER.info("Preprocessing metrics cache hit: %s", metrics_cache_path)
    metrics_csv_path, metrics_json_path = _write_preprocessing_metrics(
        target=target,
        preprocess_mode=preprocess_mode,
        outlier_sigma=outlier_sigma,
        flatten_window_length=flatten_window_length,
        no_flatten=no_flatten,
        data_source=data_source,
        metrics=metrics_payload,
    )

    bls_candidates = []
    if run_bls:
        LOGGER.info("Step 5/6: running BLS transit search")
        step_started = perf_counter()
        bls_candidates = run_bls_search(
            lc_prepared=lc_prepared,
            period_min_days=bls_period_min_days,
            period_max_days=bls_period_max_days,
            duration_min_hours=bls_duration_min_hours,
            duration_max_hours=bls_duration_max_hours,
            n_periods=bls_n_periods,
            n_durations=bls_n_durations,
            top_n=bls_top_n,
        )
        LOGGER.info(
            "BLS complete in %.2fs (%d candidate%s)",
            perf_counter() - step_started,
            len(bls_candidates),
            "" if len(bls_candidates) == 1 else "s",
        )
    else:
        LOGGER.info("Step 5/6: skipping BLS transit search (--no-bls)")

    run_utc = datetime.now(tz=timezone.utc).isoformat()
    candidate_output_key = _candidate_output_key(
        target=target,
        preprocess_mode=preprocess_mode,
        outlier_sigma=outlier_sigma,
        flatten_window_length=flatten_window_length,
        no_flatten=no_flatten,
        run_bls=run_bls,
        bls_period_min_days=bls_period_min_days,
        bls_period_max_days=bls_period_max_days,
        bls_duration_min_hours=bls_duration_min_hours,
        bls_duration_max_hours=bls_duration_max_hours,
        bls_n_periods=bls_n_periods,
        bls_n_durations=bls_n_durations,
        bls_top_n=bls_top_n,
        sectors=sectors,
        authors=authors,
        n_points_prepared=n_points_prepared,
        time_min=time_min,
        time_max=time_max,
    )
    candidate_metadata: dict[str, str | int | float | bool] = {
        "run_utc": run_utc,
        "target": target,
        "preprocess_mode": preprocess_mode,
        "data_source": data_source,
        "outlier_sigma": float(outlier_sigma),
        "flatten_window_length": int(flatten_window_length),
        "no_flatten": bool(no_flatten),
        "sectors": sectors if sectors else "all",
        "authors": authors if authors else "all",
        "n_points_raw": int(n_points_raw),
        "n_points_prepared": int(n_points_prepared),
        "time_min_btjd": float(time_min),
        "time_max_btjd": float(time_max),
        "bls_enabled": bool(run_bls),
        "bls_period_min_days": float(bls_period_min_days),
        "bls_period_max_days": float(bls_period_max_days),
        "bls_duration_min_hours": float(bls_duration_min_hours),
        "bls_duration_max_hours": float(bls_duration_max_hours),
        "bls_n_periods": int(bls_n_periods),
        "bls_n_durations": int(bls_n_durations),
        "bls_top_n": int(bls_top_n),
    }
    candidate_csv_path, candidate_json_path = _write_bls_candidates(
        target=target,
        output_key=candidate_output_key,
        metadata=candidate_metadata,
        candidates=bls_candidates,
    )

    should_generate_plot = plot_time_start is not None or plot_time_end is not None
    output_path = None
    interactive_path = None
    if should_generate_plot:
        LOGGER.info("Step 6/6: generating plot")
        step_started = perf_counter()
        output_path = save_raw_vs_prepared_plot(
            target=target,
            lc_raw=lc,
            lc_prepared=lc_prepared,
            boundaries=boundaries,
            plot_time_start=plot_time_start,
            plot_time_end=plot_time_end,
        )
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
    else:
        LOGGER.info(
            "Step 6/6: skipping plot generation (set --plot-time-start/--plot-time-end to enable)"
        )

    LOGGER.info("--------------------------------")
    LOGGER.info("Target: %s", target)
    LOGGER.info("Preprocess mode: %s", preprocess_mode)
    LOGGER.info("Points (raw -> prepared): %d -> %d", n_points_raw, n_points_prepared)
    LOGGER.info(
        "Preprocessing metrics: RMS %.6g -> %.6g (x%.3f), MAD %.6g -> %.6g (x%.3f), Trend %.6g -> %.6g (x%.3f), Retained=%.3f",
        float(metrics_payload["raw_rms"]),
        float(metrics_payload["prepared_rms"]),
        float(metrics_payload["rms_improvement_ratio"]),
        float(metrics_payload["raw_mad"]),
        float(metrics_payload["prepared_mad"]),
        float(metrics_payload["mad_improvement_ratio"]),
        float(metrics_payload["raw_trend_proxy"]),
        float(metrics_payload["prepared_trend_proxy"]),
        float(metrics_payload["trend_improvement_ratio"]),
        float(metrics_payload["retained_cadence_fraction"]),
    )
    LOGGER.info("Time range (BTJD): %.5f -> %.5f", time_min, time_max)
    LOGGER.info("Data source: %s", data_source)
    LOGGER.info("Metrics cache: %s", "hit" if metrics_cache_hit else "miss")
    LOGGER.info("Raw cache file: %s", raw_cache_path)
    LOGGER.info("Prepared cache file: %s", prepared_cache_path)
    LOGGER.info(
        "Prep params: outlier_sigma=%.2f flatten_window_length=%d no_flatten=%s",
        outlier_sigma,
        flatten_window_length,
        no_flatten,
    )
    LOGGER.info(
        "Max download files: %s", max_download_files if max_download_files is not None else "all"
    )
    LOGGER.info("Sector filter: %s", sectors if sectors else "all")
    LOGGER.info("Author filter: %s", authors if authors else "all")
    LOGGER.info(
        "Plot time start (BJD-2450000): %s",
        plot_time_start if plot_time_start is not None else "auto",
    )
    LOGGER.info(
        "Plot time end (BJD-2450000): %s", plot_time_end if plot_time_end is not None else "auto"
    )
    LOGGER.info("Interactive HTML: %s", interactive_html)
    LOGGER.info("Interactive max points: %d", interactive_max_points)
    LOGGER.info(
        "BLS settings: enabled=%s period=[%.2f, %.2f]d duration=[%.2f, %.2f]h n_periods=%d n_durations=%d top_n=%d",
        run_bls,
        bls_period_min_days,
        bls_period_max_days,
        bls_duration_min_hours,
        bls_duration_max_hours,
        bls_n_periods,
        bls_n_durations,
        bls_top_n,
    )
    LOGGER.info("BLS candidates found: %d", len(bls_candidates))
    for candidate in bls_candidates:
        LOGGER.info(
            "  - BLS #%d: period=%.6fd duration=%.3fh depth=%.6g (%.1f ppm) power=%.6g transit_count_est=%.2f",
            candidate.rank,
            candidate.period_days,
            candidate.duration_hours,
            candidate.depth,
            candidate.depth_ppm,
            candidate.power,
            candidate.transit_count_estimate,
        )
    LOGGER.info("Total runtime: %.2fs", perf_counter() - started_at)
    if output_path is not None:
        LOGGER.info("Saved plot: %s", output_path)
    else:
        LOGGER.info("Saved plot: skipped")
    if interactive_path is not None:
        LOGGER.info("Saved interactive plot: %s", interactive_path)
    LOGGER.info("Saved preprocessing metrics CSV: %s", metrics_csv_path)
    LOGGER.info("Saved preprocessing metrics JSON: %s", metrics_json_path)
    LOGGER.info("Metrics cache file: %s", metrics_cache_path)
    LOGGER.info("Saved BLS candidates CSV: %s", candidate_csv_path)
    LOGGER.info("Saved BLS candidates JSON: %s", candidate_json_path)
    LOGGER.info("--------------------------------")

    return output_path
