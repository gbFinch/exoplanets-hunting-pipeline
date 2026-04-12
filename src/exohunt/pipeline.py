from __future__ import annotations

import csv
import hashlib
import json
import logging
from dataclasses import asdict
from dataclasses import dataclass
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version as package_version
from pathlib import Path
import platform
import sys
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
    _target_artifact_dir,
    _write_segment_manifest,
)
from exohunt.bls import (
    BLSCandidate,
    compute_bls_periodogram,
    refine_bls_candidates,
    run_bls_search,
    run_iterative_bls_search,
)
from exohunt.ingest import _extract_segments, _parse_authors
from exohunt.models import LightCurveSegment
from exohunt.plotting import (
    save_candidate_diagnostics,
    save_raw_vs_prepared_plot,
    save_raw_vs_prepared_plot_interactive,
)
from exohunt.preprocess import compute_preprocessing_quality_metrics, prepare_lightcurve
from exohunt.progress import _render_progress
from exohunt.parameters import CandidateParameterEstimate, estimate_candidate_parameters
from exohunt.vetting import CandidateVettingResult, vet_bls_candidates


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
    "preprocess_enabled",
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
    "snr",
    "fap",
    "iteration",
    "radius_ratio_rp_over_rs",
    "radius_earth_radii_solar_assumption",
    "duration_expected_hours_central_solar_density",
    "duration_ratio_observed_to_expected",
    "pass_duration_plausibility",
    "parameter_assumptions",
    "parameter_uncertainty_caveats",
    "pass_min_transit_count",
    "pass_odd_even_depth",
    "pass_alias_harmonic",
    "pass_secondary_eclipse",
    "pass_depth_consistency",
    "vetting_pass",
    "transit_count_observed",
    "odd_depth_ppm",
    "even_depth_ppm",
    "odd_even_depth_mismatch_fraction",
    "secondary_eclipse_depth_fraction",
    "depth_consistency_fraction",
    "alias_harmonic_with_rank",
    "vetting_reasons",
    "odd_even_status",
]

_MANIFEST_INDEX_COLUMNS = [
    "run_started_utc",
    "run_finished_utc",
    "target",
    "manifest_run_key",
    "comparison_key",
    "config_hash",
    "data_fingerprint_hash",
    "preprocess_mode",
    "data_source",
    "n_points_raw",
    "n_points_prepared",
    "time_min_btjd",
    "time_max_btjd",
    "bls_enabled",
    "bls_mode",
    "candidate_csv_count",
    "candidate_json_count",
    "diagnostic_asset_count",
    "manifest_path",
]

_BATCH_STATUS_COLUMNS = [
    "run_utc",
    "target",
    "status",
    "error",
    "runtime_seconds",
    "output_path",
]


_ALLOWED_TWO_TRACK_MODES = {"stitched", "per-sector"}
# Theory (milestone 20): fixed operational paths are non-decisions for most users;
# keeping them internal lowers cognitive load without removing debug flexibility.
_DEFAULT_CACHE_DIR = Path("outputs/cache/lightcurves")


def _resolve_preprocess_mode(mode: str) -> str:
    # Theory (milestone 19): shared, centralized mode normalization avoids
    # semantic drift across preprocess/plot/BLS controls.
    value = mode.strip().lower()
    if value == "global":
        LOGGER.warning("Preprocess mode 'global' is deprecated; using 'stitched'.")
        return "stitched"
    if value not in _ALLOWED_TWO_TRACK_MODES:
        raise RuntimeError(
            f"Unsupported preprocess mode: {mode}. Expected one of: stitched, per-sector."
        )
    return value


def _resolve_two_track_mode(mode: str, *, label: str) -> str:
    value = mode.strip().lower()
    if value not in _ALLOWED_TWO_TRACK_MODES:
        raise RuntimeError(f"Unsupported {label}: {mode}. Expected one of: stitched, per-sector.")
    return value


@dataclass(frozen=True)
class BatchTargetStatus:
    run_utc: str
    target: str
    status: str
    error: str
    runtime_seconds: float
    output_path: str


def _hash_payload(payload: dict[str, object]) -> str:
    encoded = json.dumps(payload, sort_keys=True).encode("utf-8")
    return hashlib.sha1(encoded).hexdigest()[:16]


def _safe_package_version(name: str) -> str:
    try:
        return package_version(name)
    except PackageNotFoundError:
        return "not-installed"
    except Exception:
        return "unknown"


def _runtime_version_map() -> dict[str, str]:
    return {
        "python": platform.python_version(),
        "exohunt": _safe_package_version("exohunt"),
        "numpy": _safe_package_version("numpy"),
        "astropy": _safe_package_version("astropy"),
        "lightkurve": _safe_package_version("lightkurve"),
        "matplotlib": _safe_package_version("matplotlib"),
        "pandas": _safe_package_version("pandas"),
        "plotly": _safe_package_version("plotly"),
    }


def _write_manifest_index_row(path: Path, row: dict[str, str | int | float | bool]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    write_header = not path.exists()
    with path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=_MANIFEST_INDEX_COLUMNS)
        if write_header:
            writer.writeheader()
        writer.writerow(row)


def _write_run_manifest(
    *,
    target: str,
    run_started_utc: str,
    run_finished_utc: str,
    runtime_seconds: float,
    config_payload: dict[str, str | int | float | bool],
    data_payload: dict[str, str | int | float | bool],
    artifacts_payload: dict[str, object],
) -> tuple[Path, Path, Path]:
    """Persist run manifest for reproducibility and run-to-run comparison.

    Theory: reproducibility depends on three dimensions: settings, input-data
    summary, and software environment. Hashing settings+data creates a stable
    comparison key for grouping reruns target-by-target, while per-run manifests
    preserve exact timestamps and produced artifacts.
    """
    config_hash = _hash_payload(dict(config_payload))
    data_fingerprint_hash = _hash_payload(dict(data_payload))
    comparison_key = _hash_payload(
        {
            "target": target,
            "config_hash": config_hash,
            "data_fingerprint_hash": data_fingerprint_hash,
        }
    )
    manifest_run_key = _hash_payload(
        {"comparison_key": comparison_key, "run_started_utc": run_started_utc}
    )

    target_manifest_dir = _target_artifact_dir(target, "manifests")
    target_manifest_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = (
        target_manifest_dir / f"{_safe_target_name(target)}__manifest_{manifest_run_key}.json"
    )

    manifest_payload = {
        "schema_version": 1,
        "target": target,
        "run": {
            "run_started_utc": run_started_utc,
            "run_finished_utc": run_finished_utc,
            "runtime_seconds": float(runtime_seconds),
        },
        "comparison": {
            "comparison_key": comparison_key,
            "config_hash": config_hash,
            "data_fingerprint_hash": data_fingerprint_hash,
        },
        "config": config_payload,
        "data_summary": data_payload,
        "artifacts": artifacts_payload,
        "versions": _runtime_version_map(),
        "platform": {
            "python_executable": sys.executable,
            "platform": platform.platform(),
        },
    }
    manifest_path.write_text(
        json.dumps(manifest_payload, indent=2, sort_keys=True), encoding="utf-8"
    )

    index_row: dict[str, str | int | float | bool] = {
        "run_started_utc": run_started_utc,
        "run_finished_utc": run_finished_utc,
        "target": target,
        "manifest_run_key": manifest_run_key,
        "comparison_key": comparison_key,
        "config_hash": config_hash,
        "data_fingerprint_hash": data_fingerprint_hash,
        "preprocess_mode": str(config_payload["preprocess_mode"]),
        "data_source": str(data_payload["data_source"]),
        "n_points_raw": int(data_payload["n_points_raw"]),
        "n_points_prepared": int(data_payload["n_points_prepared"]),
        "time_min_btjd": float(data_payload["time_min_btjd"]),
        "time_max_btjd": float(data_payload["time_max_btjd"]),
        "bls_enabled": bool(config_payload["run_bls"]),
        "bls_mode": str(config_payload["bls_mode"]),
        "candidate_csv_count": int(artifacts_payload["candidate_csv_count"]),
        "candidate_json_count": int(artifacts_payload["candidate_json_count"]),
        "diagnostic_asset_count": int(artifacts_payload["diagnostic_asset_count"]),
        "manifest_path": str(manifest_path),
    }

    global_index_path = Path("outputs/manifests/run_manifest_index.csv")
    target_index_path = target_manifest_dir / "run_manifest_index.csv"
    _write_manifest_index_row(global_index_path, index_row)
    _write_manifest_index_row(target_index_path, index_row)
    return manifest_path, global_index_path, target_index_path


def _metrics_cache_path(
    target: str,
    cache_dir: Path,
    preprocess_mode: str,
    preprocess_enabled: bool,
    outlier_sigma: float,
    flatten_window_length: int,
    no_flatten: bool,
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
        "preprocess_enabled": bool(preprocess_enabled),
        "outlier_sigma": round(float(outlier_sigma), 6),
        "flatten_window_length": int(flatten_window_length),
        "no_flatten": bool(no_flatten),
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


def _save_cached_metrics(metrics_cache_path: Path, metrics: dict[str, float | int], *, no_cache: bool = False) -> None:
    if no_cache:
        return
    metrics_cache_path.parent.mkdir(parents=True, exist_ok=True)
    metrics_cache_path.write_text(json.dumps(metrics, sort_keys=True, indent=2), encoding="utf-8")


def _write_preprocessing_metrics(
    target: str,
    preprocess_mode: str,
    preprocess_enabled: bool,
    outlier_sigma: float,
    flatten_window_length: int,
    no_flatten: bool,
    data_source: str,
    metrics: dict[str, float | int | str],
) -> tuple[Path, Path]:
    aggregate_output_dir = Path("outputs/metrics")
    aggregate_output_dir.mkdir(parents=True, exist_ok=True)
    target_output_dir = _target_artifact_dir(target, "metrics")
    target_output_dir.mkdir(parents=True, exist_ok=True)
    run_utc = datetime.now(tz=timezone.utc).isoformat()

    row = {
        "run_utc": run_utc,
        "target": target,
        "preprocess_mode": preprocess_mode,
        "preprocess_enabled": bool(preprocess_enabled),
        "data_source": data_source,
        "outlier_sigma": float(outlier_sigma),
        "flatten_window_length": int(flatten_window_length),
        "no_flatten": bool(no_flatten),
    }
    for key in _PREPROCESSING_METRICS_COLUMNS:
        if key not in metrics:
            raise KeyError(f"Missing preprocessing metric key: {key}")
        row[key] = metrics[key]

    csv_path = aggregate_output_dir / "preprocessing_summary.csv"
    write_header = not csv_path.exists()
    with csv_path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=_PREPROCESSING_SUMMARY_COLUMNS)
        if write_header:
            writer.writeheader()
        writer.writerow(row)

    target_csv_path = target_output_dir / "preprocessing_summary.csv"
    target_write_header = not target_csv_path.exists()
    with target_csv_path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=_PREPROCESSING_SUMMARY_COLUMNS)
        if target_write_header:
            writer.writeheader()
        writer.writerow(row)

    json_path = target_output_dir / "preprocessing_summary.json"
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
    preprocess_enabled: bool,
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
    authors: str | None,
    n_points_prepared: int,
    time_min: float,
    time_max: float,
) -> str:
    payload = {
        "version": 1,
        "target": target,
        "preprocess_mode": preprocess_mode,
        "preprocess_enabled": bool(preprocess_enabled),
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
    vetting_by_rank: dict[int, CandidateVettingResult] | None = None,
    parameter_estimates_by_rank: dict[int, CandidateParameterEstimate] | None = None,
) -> tuple[Path, Path]:
    output_dir = _target_artifact_dir(target, "candidates")
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
            parameter_estimate = (parameter_estimates_by_rank or {}).get(int(candidate.rank))
            if parameter_estimate is not None:
                row.update(asdict(parameter_estimate))
            else:
                row.update(
                    {
                        "radius_ratio_rp_over_rs": None,
                        "radius_earth_radii_solar_assumption": None,
                        "duration_expected_hours_central_solar_density": None,
                        "duration_ratio_observed_to_expected": None,
                        "pass_duration_plausibility": None,
                        "parameter_assumptions": "",
                        "parameter_uncertainty_caveats": "",
                    }
                )
            vetting = (vetting_by_rank or {}).get(int(candidate.rank))
            if vetting is not None:
                row.update(asdict(vetting))
            else:
                row.update(
                    {
                        "pass_min_transit_count": None,
                        "pass_odd_even_depth": None,
                        "pass_alias_harmonic": None,
                        "pass_secondary_eclipse": None,
                        "pass_depth_consistency": None,
                        "vetting_pass": None,
                        "transit_count_observed": None,
                        "odd_depth_ppm": None,
                        "even_depth_ppm": None,
                        "odd_even_depth_mismatch_fraction": None,
                        "secondary_eclipse_depth_fraction": None,
                        "depth_consistency_fraction": None,
                        "alias_harmonic_with_rank": None,
                        "vetting_reasons": "",
                    }
                )
            writer.writerow(row)

    payload = {
        "metadata": metadata,
        "candidates": [],
    }
    for candidate in candidates:
        row = asdict(candidate)
        parameter_estimate = (parameter_estimates_by_rank or {}).get(int(candidate.rank))
        if parameter_estimate is not None:
            row.update(asdict(parameter_estimate))
        vetting = (vetting_by_rank or {}).get(int(candidate.rank))
        if vetting is not None:
            row.update(asdict(vetting))
        payload["candidates"].append(row)
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return csv_path, json_path


def _default_batch_state_path(targets_file: Path | None = None) -> Path:
    if targets_file is None:
        return Path("outputs/batch/run_state.json")
    return Path("outputs/batch") / f"{targets_file.stem}__state.json"


def _default_batch_status_path(targets_file: Path | None = None) -> Path:
    if targets_file is None:
        return Path("outputs/batch/run_status.csv")
    return Path("outputs/batch") / f"{targets_file.stem}__status.csv"


def _load_batch_state(state_path: Path) -> dict[str, object]:
    if not state_path.exists():
        return {
            "schema_version": 1,
            "created_utc": datetime.now(tz=timezone.utc).isoformat(),
            "last_updated_utc": "",
            "completed_targets": [],
            "failed_targets": [],
            "errors": {},
        }
    payload = json.loads(state_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"Invalid batch state payload: {state_path}")
    payload.setdefault("schema_version", 1)
    payload.setdefault("created_utc", datetime.now(tz=timezone.utc).isoformat())
    payload.setdefault("last_updated_utc", "")
    payload.setdefault("completed_targets", [])
    payload.setdefault("failed_targets", [])
    payload.setdefault("errors", {})
    return payload


def _save_batch_state(state_path: Path, payload: dict[str, object]) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    payload["last_updated_utc"] = datetime.now(tz=timezone.utc).isoformat()
    state_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _write_batch_status_report(
    status_path: Path,
    statuses: list[BatchTargetStatus],
) -> tuple[Path, Path]:
    status_path.parent.mkdir(parents=True, exist_ok=True)
    with status_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=_BATCH_STATUS_COLUMNS)
        writer.writeheader()
        for item in statuses:
            writer.writerow(asdict(item))
    json_path = status_path.with_suffix(".json")
    json_path.write_text(
        json.dumps([asdict(item) for item in statuses], indent=2, sort_keys=True), encoding="utf-8"
    )
    return status_path, json_path


def run_batch_analysis(
    targets: list[str],
    *,
    cache_dir: Path | None = None,
    refresh_cache: bool = False,
    outlier_sigma: float = 5.0,
    flatten_window_length: int = 401,
    max_download_files: int | None = None,
    preprocess_enabled: bool = True,
    no_flatten: bool = False,
    preprocess_mode: str = "per-sector",
    authors: str | None = None,
    interactive_html: bool = False,
    interactive_max_points: int = 200_000,
    plot_enabled: bool = True,
    plot_mode: str = "stitched",
    run_bls: bool = True,
    bls_period_min_days: float = 0.5,
    bls_period_max_days: float = 20.0,
    bls_duration_min_hours: float = 0.5,
    bls_duration_max_hours: float = 10.0,
    bls_n_periods: int = 2000,
    bls_n_durations: int = 12,
    bls_top_n: int = 5,
    bls_mode: str = "stitched",
    bls_search_method: str = "bls",
    bls_min_snr: float = 7.0,
    config_schema_version: int = 1,
    config_preset_id: str | None = None,
    config_preset_version: int | None = None,
    config_preset_hash: str | None = None,
    resume: bool = False,
    no_cache: bool = False,
    state_path: Path | None = None,
    status_path: Path | None = None,
) -> tuple[Path, Path, Path]:
    """Run analysis for many targets with failure isolation and resumable state.

    Theory: batch workflows should make forward progress even when individual
    targets fail. Persisting per-target completion state enables resumability,
    while a status report captures deterministic run outcomes for auditing.
    """
    unique_targets = [item.strip() for item in targets if item.strip()]
    cache_dir = cache_dir or _DEFAULT_CACHE_DIR
    deduped_targets: list[str] = []
    seen: set[str] = set()
    for target in unique_targets:
        if target in seen:
            continue
        deduped_targets.append(target)
        seen.add(target)

    state_path = state_path or _default_batch_state_path()
    status_path = status_path or _default_batch_status_path()
    state_payload = _load_batch_state(state_path)
    completed = set(str(item) for item in state_payload.get("completed_targets", []))
    failed = set(str(item) for item in state_payload.get("failed_targets", []))
    errors = dict(state_payload.get("errors", {}))

    statuses: list[BatchTargetStatus] = []
    run_utc = datetime.now(tz=timezone.utc).isoformat()
    total = len(deduped_targets)
    for idx, target in enumerate(deduped_targets, start=1):
        if resume and target in completed:
            statuses.append(
                BatchTargetStatus(
                    run_utc=run_utc,
                    target=target,
                    status="skipped_completed",
                    error="",
                    runtime_seconds=0.0,
                    output_path="",
                )
            )
            _render_progress("Batch targets", idx, total)
            continue

        target_started = perf_counter()
        max_retries = 3
        try:
            for attempt in range(max_retries + 1):
                try:
                    output_path = fetch_and_plot(
                        target=target,
                        cache_dir=cache_dir,
                        refresh_cache=refresh_cache,
                        outlier_sigma=outlier_sigma,
                        flatten_window_length=flatten_window_length,
                        max_download_files=max_download_files,
                        preprocess_enabled=preprocess_enabled,
                        no_flatten=no_flatten,
                        preprocess_mode=preprocess_mode,
                        authors=authors,
                        interactive_html=interactive_html,
                        interactive_max_points=interactive_max_points,
                        plot_enabled=plot_enabled,
                        plot_mode=plot_mode,
                        run_bls=run_bls,
                        bls_period_min_days=bls_period_min_days,
                        bls_period_max_days=bls_period_max_days,
                        bls_duration_min_hours=bls_duration_min_hours,
                        bls_duration_max_hours=bls_duration_max_hours,
                        bls_n_periods=bls_n_periods,
                        bls_n_durations=bls_n_durations,
                        bls_top_n=bls_top_n,
                        bls_mode=bls_mode,
                        bls_search_method=bls_search_method,
                        bls_min_snr=bls_min_snr,
                        config_schema_version=config_schema_version,
                        config_preset_id=config_preset_id,
                        config_preset_version=config_preset_version,
                        config_preset_hash=config_preset_hash,
                        no_cache=no_cache,
                    )
                    break  # success
                except (OSError, ConnectionError, TimeoutError) as net_exc:
                    if attempt < max_retries:
                        wait = 30 * (2 ** attempt)
                        LOGGER.warning(
                            "Network error on %s (attempt %d/%d), retrying in %ds: %s",
                            target, attempt + 1, max_retries, wait, net_exc,
                        )
                        import time as _time
                        _time.sleep(wait)
                    else:
                        raise
        except Exception as exc:
            failed.add(target)
            errors[target] = str(exc)
            statuses.append(
                BatchTargetStatus(
                    run_utc=run_utc,
                    target=target,
                    status="failed",
                    error=str(exc),
                    runtime_seconds=float(perf_counter() - target_started),
                    output_path="",
                )
            )
            LOGGER.exception("Batch target failed: %s (%s)", target, exc)
        else:
            completed.add(target)
            failed.discard(target)
            errors.pop(target, None)
            statuses.append(
                BatchTargetStatus(
                    run_utc=run_utc,
                    target=target,
                    status="success",
                    error="",
                    runtime_seconds=float(perf_counter() - target_started),
                    output_path=str(output_path) if output_path is not None else "",
                )
            )
        finally:
            state_payload["completed_targets"] = sorted(completed)
            state_payload["failed_targets"] = sorted(failed)
            state_payload["errors"] = errors
            _save_batch_state(state_path, state_payload)
            _render_progress("Batch targets", idx, total)

    status_csv, status_json = _write_batch_status_report(status_path, statuses)
    LOGGER.info("Batch run complete: %d targets", total)
    LOGGER.info("Batch state: %s", state_path)
    LOGGER.info("Batch status CSV: %s", status_csv)
    LOGGER.info("Batch status JSON: %s", status_json)
    return state_path, status_csv, status_json


# ---------------------------------------------------------------------------
# Stage I/O dataclasses
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class IngestResult:
    """Output of the ingest stage."""
    lc: lk.LightCurve
    lc_prepared: lk.LightCurve
    boundaries: list[float]
    data_source: str
    raw_cache_path: Path
    prepared_cache_path: Path
    prepared_segments_for_bls: list[LightCurveSegment]
    raw_segments_for_plot: list[LightCurveSegment]
    prepared_segments_for_plot: list[LightCurveSegment]
    tpf: object | None = None  # TargetPixelFile for centroid vetting


@dataclass(frozen=True)
class SearchResult:
    """Output of the search + output stage."""
    bls_candidates: list[BLSCandidate]
    candidate_output_key: str | None
    candidate_csv_paths: list[Path]
    candidate_json_paths: list[Path]
    diagnostic_assets: list[tuple[Path, Path]]
    stitched_vetting_by_rank: dict[int, CandidateVettingResult]


@dataclass(frozen=True)
class PlotResult:
    """Output of the plotting stage."""
    output_paths: list[Path]
    interactive_paths: list[Path]



def _ingest_stage(
    *,
    target: str,
    cache_dir: Path,
    refresh_cache: bool,
    outlier_sigma: float,
    flatten_window_length: int,
    max_download_files: int | None,
    preprocess_enabled: bool,
    no_flatten: bool,
    preprocess_mode: str,
    selected_authors: set[str] | None,
    authors: str | None,
    run_bls: bool,
    bls_duration_max_hours: float,
    no_cache: bool = False,
) -> IngestResult:
    """Ingest light curve data: cache check, download, preprocess."""
    boundaries: list[float] = []
    data_source = "download"
    prepared_segments_for_bls: list[LightCurveSegment] = []
    raw_segments_for_plot: list[LightCurveSegment] = []
    prepared_segments_for_plot: list[LightCurveSegment] = []

    if preprocess_mode == "stitched":
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
        if preprocess_enabled and prepared_cache_path.exists() and not refresh_cache:
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
            search = lk.search_lightcurve(target, mission="TESS", author="SPOC", exptime=120)
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
            _save_npz_lightcurve(raw_cache_path, lc, no_cache=no_cache)

        if preprocess_enabled:
            if lc_prepared is None:
                LOGGER.info("Step 4/5: preprocessing light curve")
                step_started = perf_counter()
                lc_prepared = prepare_lightcurve(
                    lc,
                    outlier_sigma=outlier_sigma,
                    flatten_window_length=flatten_window_length,
                    apply_flatten=not no_flatten,
                    max_transit_duration_hours=bls_duration_max_hours if run_bls else 0.0,
                )
                # Fix: Change 9/10 — Unpack normalization flag (P2)
                if isinstance(lc_prepared, tuple):
                    lc_prepared, _normalized = lc_prepared
                LOGGER.info("Preprocessing complete in %.2fs", perf_counter() - step_started)
                LOGGER.info("Writing prepared cache: %s", prepared_cache_path)
                _save_npz_lightcurve(prepared_cache_path, lc_prepared, no_cache=no_cache)
            elif lc is None:
                lc = lc_prepared
        else:
            LOGGER.info("Step 4/5: skipping preprocessing (preprocess disabled)")
            if lc is None and lc_prepared is not None:
                lc = lc_prepared
            lc_prepared = lc
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
            if selected_authors is not None and author not in selected_authors:
                continue
            raw_path = _segment_raw_cache_path(target, cache_dir, segment_id)
            try:
                if preprocess_enabled:
                    prep_path = _segment_prepared_cache_path(
                        target,
                        cache_dir,
                        segment_id,
                        outlier_sigma=outlier_sigma,
                        flatten_window_length=flatten_window_length,
                        no_flatten=no_flatten,
                    )
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
            search = lk.search_lightcurve(target, mission="TESS", author="SPOC", exptime=120)
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
                selected_authors=selected_authors,
            )
            if not raw_segments:
                raise RuntimeError("No segments remain after author filters.")
            LOGGER.info(
                "Download complete in %.2fs (%d segments)",
                perf_counter() - step_started,
                len(raw_segments),
            )
            _write_segment_manifest(target, cache_dir, raw_segments, no_cache=no_cache)
            for segment in raw_segments:
                raw_path = _segment_raw_cache_path(target, cache_dir, segment.segment_id)
                _save_npz_lightcurve(raw_path, segment.lc, no_cache=no_cache)
            data_source = "download"
        else:
            data_source = "segment-cache"
            LOGGER.info("Loaded %d raw segments from cache", len(raw_segments))

        if not preprocess_enabled:
            LOGGER.info("Step 4/5: skipping preprocessing (preprocess disabled)")
            prepared_segments = list(raw_segments)
        elif len(prepared_segments) != len(raw_segments):
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
                    max_transit_duration_hours=bls_duration_max_hours if run_bls else 0.0,
                )
                # Fix: Change 9/10 — Unpack normalization flag (P2)
                if isinstance(prepared_lc, tuple):
                    prepared_lc, _seg_normalized = prepared_lc
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
                _save_npz_lightcurve(prep_path, prepared_lc, no_cache=no_cache)
                _render_progress("Prepared segments", idx, total_segments)
            prepared_segments = rebuilt_prepared
        else:
            LOGGER.info("Step 4/5: skipping preprocessing (prepared segment cache hit)")

        lc, boundaries = _stitch_segments([segment.lc for segment in raw_segments])
        lc_prepared, _ = _stitch_segments([segment.lc for segment in prepared_segments])
        prepared_segments_for_bls = list(prepared_segments)
        raw_segments_for_plot = list(raw_segments)
        prepared_segments_for_plot = list(prepared_segments)

        raw_cache_path = _segment_base_dir(target, cache_dir)
        prepared_cache_path = _segment_base_dir(target, cache_dir)


    # Download one TPF for centroid vetting (best done during ingest
    # to avoid a second round of MAST queries during vetting)
    tpf = None
    try:
        sr_tpf = lk.search_targetpixelfile(target, mission="TESS", author="SPOC")
        if len(sr_tpf) > 0:
            tpf = sr_tpf[0].download()
            LOGGER.info("Downloaded TPF for centroid vetting (%d cadences)", len(tpf.time))
    except Exception as exc:
        LOGGER.warning("TPF download failed during ingest: %s", exc)

    return IngestResult(
        lc=lc, lc_prepared=lc_prepared, boundaries=boundaries,
        data_source=data_source, raw_cache_path=raw_cache_path,
        prepared_cache_path=prepared_cache_path,
        prepared_segments_for_bls=prepared_segments_for_bls,
        raw_segments_for_plot=raw_segments_for_plot,
        prepared_segments_for_plot=prepared_segments_for_plot,
        tpf=tpf,
    )


def _search_and_output_stage(
    *,
    target: str,
    lc_prepared: lk.LightCurve,
    prepared_segments_for_bls: list[LightCurveSegment],
    preprocess_mode: str,
    preprocess_enabled: bool,
    data_source: str,
    outlier_sigma: float,
    flatten_window_length: int,
    no_flatten: bool,
    authors: str | None,
    n_points_raw: int,
    n_points_prepared: int,
    time_min: float,
    time_max: float,
    run_bls: bool,
    bls_period_min_days: float,
    bls_period_max_days: float,
    bls_duration_min_hours: float,
    bls_duration_max_hours: float,
    bls_n_periods: int,
    bls_n_durations: int,
    bls_top_n: int,
    bls_mode: str,
    bls_search_method: str,
    bls_min_snr: float,
    vetting_min_transit_count: int,
    vetting_odd_even_max_mismatch_fraction: float,
    vetting_alias_tolerance_fraction: float,
    vetting_secondary_eclipse_max_fraction: float,
    vetting_depth_consistency_max_fraction: float,
    parameter_stellar_density_kg_m3: float,
    parameter_duration_ratio_min: float,
    parameter_duration_ratio_max: float,
    parameter_apply_limb_darkening_correction: bool = False,
    parameter_limb_darkening_u1: float = 0.4,
    parameter_limb_darkening_u2: float = 0.2,
    parameter_tic_density_lookup: bool = False,
    bls_unique_period_separation_fraction: float = 0.05,
    bls_iterative_masking: bool = False,
    bls_iterative_passes: int = 1,
    bls_iterative_top_n: int = 1,
    bls_transit_mask_padding_factor: float = 1.5,
    bls_subtraction_model: str = "box_mask",
    preprocess_iterative_flatten: bool = False,
    preprocess_transit_mask_padding_factor: float = 1.5,
    tpf: object | None = None,
) -> SearchResult:
    """Run BLS search, vetting, parameter estimation, and write candidates."""
    bls_candidates = []
    candidate_output_key: str | None = None
    candidate_csv_paths: list[Path] = []
    candidate_json_paths: list[Path] = []
    diagnostic_assets: list[tuple[Path, Path]] = []
    stitched_vetting_by_rank: dict[int, CandidateVettingResult] = {}
    run_utc = datetime.now(tz=timezone.utc).isoformat()

    # Query stellar parameters for TLS
    stellar_params = None
    known = []
    if bls_search_method == "tls":
        from exohunt.stellar import query_stellar_params
        tic_num = int(target.replace("TIC ", "").strip())
        stellar_params = query_stellar_params(tic_num)

    # Pre-mask known planet transits so the first search pass finds new signals
    if bls_search_method == "tls":
        from exohunt.ephemeris import query_all_ephemerides
        tic_num = int(target.replace("TIC ", "").strip())
        known = query_all_ephemerides(tic_num)
        if known:
            time_arr = np.asarray(lc_prepared.time.value, dtype=float)
            flux_arr = np.asarray(lc_prepared.flux.value, dtype=float)
            n_masked = 0
            for eph in known:
                t0_btjd = eph.t0_bjd - 2457000.0
                half_w = 0.5 * (eph.duration_hours / 24.0) * 1.5  # 1.5x padding
                period = eph.period_days
                if period <= 0:
                    continue
                t_min, t_max = float(np.nanmin(time_arr)), float(np.nanmax(time_arr))
                n_start = int(np.floor((t_min - t0_btjd) / period)) - 1
                n_end = int(np.ceil((t_max - t0_btjd) / period)) + 1
                for n in range(n_start, n_end + 1):
                    epoch = t0_btjd + n * period
                    hit = np.abs(time_arr - epoch) < half_w
                    n_masked += int(np.sum(hit & np.isfinite(flux_arr)))
                    flux_arr[hit] = np.nan
            lc_prepared = lk.LightCurve(time=lc_prepared.time, flux=flux_arr)
            LOGGER.info(
                "Pre-masked %d cadences for %d known planet(s): %s",
                n_masked, len(known),
                ", ".join(f"{e.name} P={e.period_days:.2f}d" for e in known),
            )

    if run_bls:
        LOGGER.info("Step 5/7: running BLS transit search")
        step_started = perf_counter()
        if bls_mode == "per-sector" and prepared_segments_for_bls:
            total_candidates = 0
            for segment in prepared_segments_for_bls:
                segment_candidates = run_bls_search(
                    lc_prepared=segment.lc,
                    period_min_days=bls_period_min_days,
                    period_max_days=bls_period_max_days,
                    duration_min_hours=bls_duration_min_hours,
                    duration_max_hours=bls_duration_max_hours,
                    n_periods=bls_n_periods,
                    n_durations=bls_n_durations,
                    top_n=bls_top_n,
                    min_snr=bls_min_snr,
                    unique_period_separation_fraction=bls_unique_period_separation_fraction,
                )
                # Fix: Change 11 — Refine per-sector candidates (O2)
                if segment_candidates:
                    segment_candidates = refine_bls_candidates(
                        lc_prepared=segment.lc,
                        candidates=segment_candidates,
                        period_min_days=bls_period_min_days,
                        period_max_days=bls_period_max_days,
                        duration_min_hours=bls_duration_min_hours,
                        duration_max_hours=bls_duration_max_hours,
                        n_periods=max(12000, bls_n_periods * 6),
                        n_durations=max(20, bls_n_durations),
                        window_fraction=0.02,
                    )
                total_candidates += len(segment_candidates)
                segment_time = np.asarray(segment.lc.time.value, dtype=float)
                finite_segment_time = segment_time[np.isfinite(segment_time)]
                if len(finite_segment_time):
                    seg_t_min = float(np.nanmin(finite_segment_time))
                    seg_t_max = float(np.nanmax(finite_segment_time))
                else:
                    seg_t_min = float("nan")
                    seg_t_max = float("nan")
                segment_metadata = {
                    "run_utc": run_utc,
                    "target": target,
                    "segment_id": segment.segment_id,
                    "sector": int(segment.sector),
                    "author": segment.author,
                    "cadence_days": float(segment.cadence),
                    "preprocess_mode": preprocess_mode,
                    "preprocess_enabled": bool(preprocess_enabled),
                    "data_source": data_source,
                    "outlier_sigma": float(outlier_sigma),
                    "flatten_window_length": int(flatten_window_length),
                    "no_flatten": bool(no_flatten),
                    "authors": authors if authors else "all",
                    "n_points_raw": int(n_points_raw),
                    "n_points_prepared": int(len(segment.lc.time.value)),
                    "time_min_btjd": seg_t_min,
                    "time_max_btjd": seg_t_max,
                    "bls_enabled": True,
                    "bls_mode": bls_mode,
                    "bls_period_min_days": float(bls_period_min_days),
                    "bls_period_max_days": float(bls_period_max_days),
                    "bls_duration_min_hours": float(bls_duration_min_hours),
                    "bls_duration_max_hours": float(bls_duration_max_hours),
                    "bls_n_periods": int(bls_n_periods),
                    "bls_n_durations": int(bls_n_durations),
                    "bls_top_n": int(bls_top_n),
                    "parameter_estimation_enabled": True,
                    "parameter_stellar_density_kg_m3": float(parameter_stellar_density_kg_m3),
                    "parameter_duration_ratio_min": float(parameter_duration_ratio_min),
                    "parameter_duration_ratio_max": float(parameter_duration_ratio_max),
                }
                segment_key = _candidate_output_key(
                    target=target,
                    preprocess_mode=preprocess_mode,
                    preprocess_enabled=preprocess_enabled,
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
                    authors=segment.author,
                    n_points_prepared=len(segment.lc.time.value),
                    time_min=seg_t_min if np.isfinite(seg_t_min) else 0.0,
                    time_max=seg_t_max if np.isfinite(seg_t_max) else 0.0,
                )
                csv_path, json_path = _write_bls_candidates(
                    target=target,
                    output_key=f"{segment.segment_id}_{segment_key}",
                    metadata=segment_metadata,
                    candidates=segment_candidates,
                    vetting_by_rank=vet_bls_candidates(
                        lc_prepared=segment.lc,
                        candidates=segment_candidates,
                        min_transit_count=vetting_min_transit_count,
                        odd_even_mismatch_max_fraction=vetting_odd_even_max_mismatch_fraction,
                        alias_tolerance_fraction=vetting_alias_tolerance_fraction,
                        secondary_eclipse_max_fraction=vetting_secondary_eclipse_max_fraction,
                        depth_consistency_max_fraction=vetting_depth_consistency_max_fraction,
                    ),
                    parameter_estimates_by_rank=estimate_candidate_parameters(
                        candidates=segment_candidates,
                        stellar_density_kg_m3=parameter_stellar_density_kg_m3,
                        duration_ratio_min=parameter_duration_ratio_min,
                        duration_ratio_max=parameter_duration_ratio_max,
                        apply_limb_darkening_correction=parameter_apply_limb_darkening_correction,
                        limb_darkening_u1=parameter_limb_darkening_u1,
                        limb_darkening_u2=parameter_limb_darkening_u2,
                        tic_density_lookup=parameter_tic_density_lookup,
                        tic_id=target.replace("TIC ", "").strip() if parameter_tic_density_lookup else None,
                    ),
                )
                candidate_csv_paths.append(csv_path)
                candidate_json_paths.append(json_path)

                if segment_candidates:
                    period_grid_days, period_power = compute_bls_periodogram(
                        lc_prepared=segment.lc,
                        period_min_days=bls_period_min_days,
                        period_max_days=bls_period_max_days,
                        duration_min_hours=bls_duration_min_hours,
                        duration_max_hours=bls_duration_max_hours,
                        n_periods=bls_n_periods,
                        n_durations=bls_n_durations,
                    )
                    diagnostic_assets.extend(
                        save_candidate_diagnostics(
                            target=target,
                            output_key=f"{segment.segment_id}_{segment_key}",
                            lc_prepared=segment.lc,
                            candidates=segment_candidates,
                            period_grid_days=period_grid_days,
                            power_grid=period_power,
                        )
                    )

            LOGGER.info(
                "BLS complete in %.2fs (%d segment candidate%s)",
                perf_counter() - step_started,
                total_candidates,
                "" if total_candidates == 1 else "s",
            )
        else:
            if bls_mode == "per-sector" and not prepared_segments_for_bls:
                LOGGER.warning(
                    "BLS mode 'per-sector' requested but no prepared segments are available; falling back to stitched."
                )
            if bls_iterative_masking and bls_iterative_passes > 1:
                from exohunt.config import BLSConfig, PreprocessConfig

                iter_bls_cfg = BLSConfig(
                    enabled=True, mode=bls_mode,
                    search_method=bls_search_method,
                    period_min_days=bls_period_min_days,
                    period_max_days=bls_period_max_days,
                    duration_min_hours=bls_duration_min_hours,
                    duration_max_hours=bls_duration_max_hours,
                    n_periods=bls_n_periods, n_durations=bls_n_durations,
                    top_n=bls_top_n, min_snr=bls_min_snr,
                    compute_fap=False, fap_iterations=0,
                    iterative_masking=True,
                    unique_period_separation_fraction=bls_unique_period_separation_fraction,
                    iterative_passes=bls_iterative_passes,
                    subtraction_model=bls_subtraction_model,
                    iterative_top_n=bls_iterative_top_n,
                    transit_mask_padding_factor=bls_transit_mask_padding_factor,
                )
                iter_pp_cfg = PreprocessConfig(
                    enabled=True, mode="per-sector",
                    outlier_sigma=outlier_sigma,
                    flatten_window_length=flatten_window_length,
                    flatten=not no_flatten,
                    iterative_flatten=preprocess_iterative_flatten,
                    transit_mask_padding_factor=preprocess_transit_mask_padding_factor,
                )
                bls_candidates = run_iterative_bls_search(
                    lc_prepared=lc_prepared,
                    config=iter_bls_cfg,
                    preprocess_config=iter_pp_cfg,
                    lc=lc_prepared if preprocess_iterative_flatten else None,
                    stellar_params=stellar_params,
                )
            else:
                if bls_search_method == "tls":
                    from exohunt.tls import run_tls_search
                    bls_candidates = run_tls_search(
                        lc_prepared=lc_prepared,
                        period_min_days=bls_period_min_days,
                        period_max_days=bls_period_max_days,
                        top_n=bls_top_n,
                        min_sde=bls_min_snr,
                        unique_period_separation_fraction=bls_unique_period_separation_fraction,
                        stellar_params=stellar_params,
                    )
                else:
                    bls_candidates = run_bls_search(
                        lc_prepared=lc_prepared,
                        period_min_days=bls_period_min_days,
                        period_max_days=bls_period_max_days,
                        duration_min_hours=bls_duration_min_hours,
                        duration_max_hours=bls_duration_max_hours,
                        n_periods=bls_n_periods,
                        n_durations=bls_n_durations,
                        top_n=bls_top_n,
                        min_snr=bls_min_snr,
                        unique_period_separation_fraction=bls_unique_period_separation_fraction,
                    )
            if bls_candidates:
                refined_candidates = refine_bls_candidates(
                    lc_prepared=lc_prepared,
                    candidates=bls_candidates,
                    period_min_days=bls_period_min_days,
                    period_max_days=bls_period_max_days,
                    duration_min_hours=bls_duration_min_hours,
                    duration_max_hours=bls_duration_max_hours,
                    n_periods=max(12000, bls_n_periods * 6),
                    n_durations=max(20, bls_n_durations),
                    window_fraction=0.02,
                )
                if refined_candidates:
                    bls_candidates = refined_candidates
                # Assign globally unique ranks before vetting so candidates
                # from different iterations don't collide in the rank-keyed
                # vetting dict.
                for _i, _c in enumerate(bls_candidates):
                    object.__setattr__(_c, "rank", _i + 1)
                stitched_vetting_by_rank = vet_bls_candidates(
                    lc_prepared=lc_prepared,
                    candidates=bls_candidates,
                    min_transit_count=vetting_min_transit_count,
                    odd_even_mismatch_max_fraction=vetting_odd_even_max_mismatch_fraction,
                    alias_tolerance_fraction=vetting_alias_tolerance_fraction,
                    secondary_eclipse_max_fraction=vetting_secondary_eclipse_max_fraction,
                    depth_consistency_max_fraction=vetting_depth_consistency_max_fraction,
                )
                # Centroid vetting for candidates that passed rule-based checks
                if bls_search_method == "tls":
                    passing = [
                        c for c in bls_candidates
                        if stitched_vetting_by_rank.get(c.rank)
                        and stitched_vetting_by_rank[c.rank].vetting_pass
                    ]
                    if passing:
                        from exohunt.centroid import run_centroid_vetting
                        tic_num = int(target.replace("TIC ", "").strip())
                        centroid_input = [
                            {"rank": c.rank, "period_days": c.period_days,
                             "transit_time": c.transit_time, "duration_hours": c.duration_hours}
                            for c in passing
                        ]
                        centroid_results = run_centroid_vetting(tic_num, centroid_input, tpf=tpf)
                        for rank, cr in centroid_results.items():
                            vr = stitched_vetting_by_rank.get(rank)
                            if vr and not cr.passed and cr.status == "fail":
                                # Override vetting_pass and append reason
                                stitched_vetting_by_rank[rank] = CandidateVettingResult(
                                    pass_min_transit_count=vr.pass_min_transit_count,
                                    pass_odd_even_depth=vr.pass_odd_even_depth,
                                    pass_alias_harmonic=vr.pass_alias_harmonic,
                                    pass_secondary_eclipse=vr.pass_secondary_eclipse,
                                    pass_depth_consistency=vr.pass_depth_consistency,
                                    vetting_pass=False,
                                    transit_count_observed=vr.transit_count_observed,
                                    odd_depth_ppm=vr.odd_depth_ppm,
                                    even_depth_ppm=vr.even_depth_ppm,
                                    odd_even_depth_mismatch_fraction=vr.odd_even_depth_mismatch_fraction,
                                    secondary_eclipse_depth_fraction=vr.secondary_eclipse_depth_fraction,
                                    depth_consistency_fraction=vr.depth_consistency_fraction,
                                    alias_harmonic_with_rank=vr.alias_harmonic_with_rank,
                                    vetting_reasons=vr.vetting_reasons + ";centroid_shift",
                                    odd_even_status=vr.odd_even_status,
                                )
                # Sub-harmonic check against known TOI/confirmed planet periods
                if known:
                    _HARMONIC_RATIOS = (2, 3, 4, 5, 6, 7, 8, 9, 10)
                    known_periods = [e.period_days for e in known]
                    for c in bls_candidates:
                        vr = stitched_vetting_by_rank.get(c.rank)
                        if not vr or not vr.vetting_pass:
                            continue
                        for kp in known_periods:
                            for n in _HARMONIC_RATIOS:
                                if abs(c.period_days - kp / n) / c.period_days < 0.03:
                                    LOGGER.info(
                                        "Candidate P=%.3fd is 1/%d sub-harmonic of known P=%.3fd",
                                        c.period_days, n, kp,
                                    )
                                    stitched_vetting_by_rank[c.rank] = CandidateVettingResult(
                                        pass_min_transit_count=vr.pass_min_transit_count,
                                        pass_odd_even_depth=vr.pass_odd_even_depth,
                                        pass_alias_harmonic=False,
                                        pass_secondary_eclipse=vr.pass_secondary_eclipse,
                                        pass_depth_consistency=vr.pass_depth_consistency,
                                        vetting_pass=False,
                                        transit_count_observed=vr.transit_count_observed,
                                        odd_depth_ppm=vr.odd_depth_ppm,
                                        even_depth_ppm=vr.even_depth_ppm,
                                        odd_even_depth_mismatch_fraction=vr.odd_even_depth_mismatch_fraction,
                                        secondary_eclipse_depth_fraction=vr.secondary_eclipse_depth_fraction,
                                        depth_consistency_fraction=vr.depth_consistency_fraction,
                                        alias_harmonic_with_rank=vr.alias_harmonic_with_rank,
                                        vetting_reasons=vr.vetting_reasons.replace("pass", "") + f"toi_subharmonic_1/{n}_of_{kp:.1f}d",
                                        odd_even_status=vr.odd_even_status,
                                    )
                                    break
                            else:
                                continue
                            break
            LOGGER.info(
                "BLS complete in %.2fs (%d candidate%s)",
                perf_counter() - step_started,
                len(bls_candidates),
                "" if len(bls_candidates) == 1 else "s",
            )
    else:
        LOGGER.info("Step 5/7: skipping BLS transit search (--no-bls)")
    if bls_mode != "per-sector" or not prepared_segments_for_bls:
        candidate_output_key = _candidate_output_key(
            target=target,
            preprocess_mode=preprocess_mode,
            preprocess_enabled=preprocess_enabled,
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
            authors=authors,
            n_points_prepared=n_points_prepared,
            time_min=time_min,
            time_max=time_max,
        )
        candidate_metadata: dict[str, str | int | float | bool] = {
            "run_utc": run_utc,
            "target": target,
            "preprocess_mode": preprocess_mode,
            "preprocess_enabled": bool(preprocess_enabled),
            "data_source": data_source,
            "outlier_sigma": float(outlier_sigma),
            "flatten_window_length": int(flatten_window_length),
            "no_flatten": bool(no_flatten),
            "authors": authors if authors else "all",
            "n_points_raw": int(n_points_raw),
            "n_points_prepared": int(n_points_prepared),
            "time_min_btjd": float(time_min),
            "time_max_btjd": float(time_max),
            "bls_enabled": bool(run_bls),
            "bls_mode": bls_mode,
            "bls_period_min_days": float(bls_period_min_days),
            "bls_period_max_days": float(bls_period_max_days),
            "bls_duration_min_hours": float(bls_duration_min_hours),
            "bls_duration_max_hours": float(bls_duration_max_hours),
            "bls_n_periods": int(bls_n_periods),
            "bls_n_durations": int(bls_n_durations),
            "bls_top_n": int(bls_top_n),
            "bls_refined_local": bool(run_bls and bls_mode == "stitched"),
            "parameter_estimation_enabled": bool(run_bls),
            "parameter_stellar_density_kg_m3": float(parameter_stellar_density_kg_m3),
            "parameter_duration_ratio_min": float(parameter_duration_ratio_min),
            "parameter_duration_ratio_max": float(parameter_duration_ratio_max),
        }
        candidate_csv_path, candidate_json_path = _write_bls_candidates(
            target=target,
            output_key=candidate_output_key,
            metadata=candidate_metadata,
            candidates=bls_candidates,
            vetting_by_rank=stitched_vetting_by_rank,
            parameter_estimates_by_rank=estimate_candidate_parameters(
                candidates=bls_candidates,
                stellar_density_kg_m3=parameter_stellar_density_kg_m3,
                duration_ratio_min=parameter_duration_ratio_min,
                duration_ratio_max=parameter_duration_ratio_max,
                apply_limb_darkening_correction=parameter_apply_limb_darkening_correction,
                limb_darkening_u1=parameter_limb_darkening_u1,
                limb_darkening_u2=parameter_limb_darkening_u2,
                tic_density_lookup=parameter_tic_density_lookup,
                tic_id=target.replace("TIC ", "").strip() if parameter_tic_density_lookup else None,
            ),
        )
        candidate_csv_paths.append(candidate_csv_path)
        candidate_json_paths.append(candidate_json_path)

        # Write per-iteration artifact files when iterative BLS was used (FR-12)
        if bls_iterative_masking and bls_iterative_passes > 1 and bls_candidates:
            iterations_seen = sorted({c.iteration for c in bls_candidates})
            for iter_n in iterations_seen:
                iter_cands = [c for c in bls_candidates if c.iteration == iter_n]
                iter_metadata = dict(candidate_metadata)
                iter_metadata["bls_iteration"] = iter_n
                _, iter_json = _write_bls_candidates(
                    target=target,
                    output_key=f"iter_{iter_n}_{candidate_output_key}",
                    metadata=iter_metadata,
                    candidates=iter_cands,
                    vetting_by_rank=stitched_vetting_by_rank,
                    parameter_estimates_by_rank=estimate_candidate_parameters(
                        candidates=iter_cands,
                        stellar_density_kg_m3=parameter_stellar_density_kg_m3,
                        duration_ratio_min=parameter_duration_ratio_min,
                        duration_ratio_max=parameter_duration_ratio_max,
                        apply_limb_darkening_correction=parameter_apply_limb_darkening_correction,
                        limb_darkening_u1=parameter_limb_darkening_u1,
                        limb_darkening_u2=parameter_limb_darkening_u2,
                        tic_density_lookup=parameter_tic_density_lookup,
                        tic_id=target.replace("TIC ", "").strip() if parameter_tic_density_lookup else None,
                    ),
                )
                candidate_json_paths.append(iter_json)
            LOGGER.info(
                "Wrote %d per-iteration candidate artifact(s)",
                len(iterations_seen),
            )

        if run_bls and bls_candidates:
            LOGGER.info("Step 6/7: generating candidate diagnostics")
            step_started = perf_counter()
            period_grid_days, period_power = compute_bls_periodogram(
                lc_prepared=lc_prepared,
                period_min_days=bls_period_min_days,
                period_max_days=bls_period_max_days,
                duration_min_hours=bls_duration_min_hours,
                duration_max_hours=bls_duration_max_hours,
                n_periods=bls_n_periods,
                n_durations=bls_n_durations,
            )
            diagnostic_assets = save_candidate_diagnostics(
                target=target,
                output_key=candidate_output_key,
                lc_prepared=lc_prepared,
                candidates=bls_candidates,
                period_grid_days=period_grid_days,
                power_grid=period_power,
            )
            LOGGER.info(
                "Candidate diagnostics complete in %.2fs (%d candidate asset set%s)",
                perf_counter() - step_started,
                len(diagnostic_assets),
                "" if len(diagnostic_assets) == 1 else "s",
            )
        elif run_bls:
            LOGGER.info("Step 6/7: skipping candidate diagnostics (no BLS candidates)")
        else:
            LOGGER.info("Step 6/7: skipping candidate diagnostics (BLS disabled)")
    else:
        LOGGER.info("Step 6/7: diagnostics generated per sector during BLS step")

    # Theory (milestone 18): mode-based plotting removes several loosely coupled
    # axis/sector flags and makes output intent explicit and reproducible.

    # TRICERATOPS statistical validation for passing candidates (opt-in, expensive)
    if (bls_search_method == "tls" and bls_candidates and stitched_vetting_by_rank
            and bls_mode != "per-sector"):
        passing = [
            c for c in bls_candidates
            if stitched_vetting_by_rank.get(c.rank)
            and stitched_vetting_by_rank[c.rank].vetting_pass
        ]
        if passing:
            from exohunt.validation import validate_candidate
            tic_num = int(target.replace("TIC ", "").strip())
            time_arr = np.asarray(lc_prepared.time.value, dtype=float)
            flux_arr = np.asarray(lc_prepared.flux.value, dtype=float)
            flux_err = float(np.nanstd(flux_arr[np.isfinite(flux_arr)])) if np.any(np.isfinite(flux_arr)) else 0.001
            # Determine sectors from light curve metadata
            sectors = [14]  # fallback
            try:
                import lightkurve as _lk
                sr = _lk.search_lightcurve(f"TIC {tic_num}", mission="TESS", author="SPOC")
                if len(sr) > 0:
                    sectors = sorted({int(s.split()[-1]) for s in sr.mission})
            except Exception:
                pass
            validation_results = {}
            for c in passing:
                LOGGER.info("TRICERATOPS validation for rank %d P=%.3fd...", c.rank, c.period_days)
                vr = validate_candidate(
                    tic_id=tic_num, sectors=sectors,
                    time=time_arr, flux=flux_arr, flux_err=flux_err,
                    period_days=c.period_days, depth_ppm=c.depth_ppm,
                    N=1_000_000,
                )
                validation_results[c.rank] = {
                    "fpp": vr.fpp, "nfpp": vr.nfpp,
                    "validated": vr.validated, "status": vr.status,
                }
            if validation_results:
                val_path = _target_artifact_dir(target, "candidates") / f"{_safe_target_name(target)}__validation.json"
                val_path.write_text(json.dumps(validation_results, indent=2), encoding="utf-8")
                LOGGER.info("TRICERATOPS results written to %s", val_path)

    return SearchResult(
        bls_candidates=bls_candidates,
        candidate_output_key=candidate_output_key,
        candidate_csv_paths=candidate_csv_paths,
        candidate_json_paths=candidate_json_paths,
        diagnostic_assets=diagnostic_assets,
        stitched_vetting_by_rank=stitched_vetting_by_rank,
    )


def _plotting_stage(
    *,
    target: str,
    lc: lk.LightCurve,
    lc_prepared: lk.LightCurve,
    boundaries: list[float],
    plot_enabled: bool,
    plot_mode: str,
    preprocess_mode: str,
    interactive_html: bool,
    interactive_max_points: int,
    raw_segments_for_plot: list[LightCurveSegment],
    prepared_segments_for_plot: list[LightCurveSegment],
    smoothing_window: int = 5,
) -> PlotResult:
    """Generate static and interactive plots."""
    output_paths: list[Path] = []
    interactive_paths: list[Path] = []
    if plot_enabled:
        LOGGER.info("Step 7/7: generating plot(s)")
        step_started = perf_counter()
        if plot_mode == "stitched":
            output_paths.append(
                save_raw_vs_prepared_plot(
                    target=target,
                    lc_raw=lc,
                    lc_prepared=lc_prepared,
                    boundaries=boundaries,
                    output_key="stitched",
                    smoothing_window=smoothing_window,
                )
            )
            if interactive_html:
                interactive_paths.append(
                    save_raw_vs_prepared_plot_interactive(
                        target=target,
                        lc_raw=lc,
                        lc_prepared=lc_prepared,
                        boundaries=boundaries,
                        max_points=interactive_max_points,
                        output_key="stitched",
                    )
                )
        elif plot_mode == "per-sector":
            if (
                preprocess_mode != "per-sector"
                or not raw_segments_for_plot
                or not prepared_segments_for_plot
            ):
                raise RuntimeError(
                    "Plot mode 'per-sector' requires preprocess mode 'per-sector' with segment data."
                )
            prepared_by_id = {segment.segment_id: segment for segment in prepared_segments_for_plot}
            ordered_raw = sorted(
                raw_segments_for_plot, key=lambda item: (int(item.sector), item.segment_id)
            )
            for raw_segment in ordered_raw:
                prepared_segment = prepared_by_id.get(raw_segment.segment_id)
                if prepared_segment is None:
                    continue
                output_paths.append(
                    save_raw_vs_prepared_plot(
                        target=target,
                        lc_raw=raw_segment.lc,
                        lc_prepared=prepared_segment.lc,
                        boundaries=[],
                        output_key=raw_segment.segment_id,
                        smoothing_window=smoothing_window,
                    )
                )
                if interactive_html:
                    interactive_paths.append(
                        save_raw_vs_prepared_plot_interactive(
                            target=target,
                            lc_raw=raw_segment.lc,
                            lc_prepared=prepared_segment.lc,
                            boundaries=[],
                            max_points=interactive_max_points,
                            output_key=raw_segment.segment_id,
                        )
                    )
        else:
            raise RuntimeError(f"Unsupported plot mode: {plot_mode}")
        LOGGER.info(
            "Plot complete in %.2fs (%d file%s)",
            perf_counter() - step_started,
            len(output_paths),
            "" if len(output_paths) == 1 else "s",
        )
    else:
        LOGGER.info("Step 7/7: skipping plot generation (plot.enabled=false)")


    return PlotResult(output_paths=output_paths, interactive_paths=interactive_paths)


def _manifest_stage(
    *,
    target: str,
    started_at: float,
    run_started_utc: str,
    refresh_cache: bool,
    outlier_sigma: float,
    flatten_window_length: int,
    preprocess_enabled: bool,
    no_flatten: bool,
    preprocess_mode: str,
    authors: str | None,
    interactive_html: bool,
    interactive_max_points: int,
    plot_enabled: bool,
    plot_mode: str,
    run_bls: bool,
    bls_mode: str,
    bls_period_min_days: float,
    bls_period_max_days: float,
    bls_duration_min_hours: float,
    bls_duration_max_hours: float,
    bls_n_periods: int,
    bls_n_durations: int,
    bls_top_n: int,
    config_schema_version: int,
    config_preset_id: str | None,
    config_preset_version: int | None,
    config_preset_hash: str | None,
    data_source: str,
    n_points_raw: int,
    n_points_prepared: int,
    time_min: float,
    time_max: float,
    raw_cache_path: Path,
    prepared_cache_path: Path,
    metrics_csv_path: Path,
    metrics_json_path: Path,
    metrics_cache_path: Path,
    metrics_cache_hit: bool,
    metrics_payload: dict,
    search_result: SearchResult,
    plot_result: PlotResult,
) -> None:
    """Write run manifest and log summary."""
    bls_candidates = search_result.bls_candidates
    candidate_output_key = search_result.candidate_output_key
    candidate_csv_paths = search_result.candidate_csv_paths
    candidate_json_paths = search_result.candidate_json_paths
    diagnostic_assets = search_result.diagnostic_assets
    stitched_vetting_by_rank = search_result.stitched_vetting_by_rank
    output_paths = plot_result.output_paths
    interactive_paths = plot_result.interactive_paths

    run_finished_utc = datetime.now(tz=timezone.utc).isoformat()
    runtime_seconds = perf_counter() - started_at
    config_payload: dict[str, str | int | float | bool] = {
        "target": target,
        "refresh_cache": bool(refresh_cache),
        "outlier_sigma": float(outlier_sigma),
        "flatten_window_length": int(flatten_window_length),
        "preprocess_enabled": bool(preprocess_enabled),
        "no_flatten": bool(no_flatten),
        "preprocess_mode": preprocess_mode,
        "authors": authors if authors else "all",
        "interactive_html": bool(interactive_html),
        "interactive_max_points": int(interactive_max_points),
        "plot_enabled": bool(plot_enabled),
        "plot_mode": plot_mode,
        "run_bls": bool(run_bls),
        "bls_mode": bls_mode,
        "bls_period_min_days": float(bls_period_min_days),
        "bls_period_max_days": float(bls_period_max_days),
        "bls_duration_min_hours": float(bls_duration_min_hours),
        "bls_duration_max_hours": float(bls_duration_max_hours),
        "bls_n_periods": int(bls_n_periods),
        "bls_n_durations": int(bls_n_durations),
        "bls_top_n": int(bls_top_n),
        "config_schema_version": int(config_schema_version),
        "config_preset_id": config_preset_id if config_preset_id else "none",
        "config_preset_version": int(config_preset_version) if config_preset_version else 0,
        "config_preset_hash": config_preset_hash if config_preset_hash else "",
    }
    data_payload: dict[str, str | int | float | bool] = {
        "target": target,
        "data_source": data_source,
        "n_points_raw": int(n_points_raw),
        "n_points_prepared": int(n_points_prepared),
        "time_min_btjd": float(time_min),
        "time_max_btjd": float(time_max),
        "raw_cache_path": str(raw_cache_path),
        "prepared_cache_path": str(prepared_cache_path),
    }
    artifacts_payload: dict[str, object] = {
        "metrics_csv_path": str(metrics_csv_path),
        "metrics_json_path": str(metrics_json_path),
        "metrics_cache_path": str(metrics_cache_path),
        "plot_path_count": int(len(output_paths)),
        "plot_paths": [str(path) for path in output_paths],
        "interactive_plot_path_count": int(len(interactive_paths)),
        "interactive_plot_paths": [str(path) for path in interactive_paths],
        "candidate_output_key": candidate_output_key if candidate_output_key is not None else "",
        "candidate_csv_count": int(len(candidate_csv_paths)),
        "candidate_json_count": int(len(candidate_json_paths)),
        "candidate_csv_paths": [str(path) for path in candidate_csv_paths],
        "candidate_json_paths": [str(path) for path in candidate_json_paths],
        "diagnostic_asset_count": int(len(diagnostic_assets)),
        "diagnostic_assets": [
            {"periodogram_path": str(periodogram), "phasefold_path": str(phasefold)}
            for periodogram, phasefold in diagnostic_assets
        ],
    }
    manifest_path, manifest_global_index_path, manifest_target_index_path = _write_run_manifest(
        target=target,
        run_started_utc=run_started_utc,
        run_finished_utc=run_finished_utc,
        runtime_seconds=runtime_seconds,
        config_payload=config_payload,
        data_payload=data_payload,
        artifacts_payload=artifacts_payload,
    )

    LOGGER.info("--------------------------------")
    LOGGER.info("Target: %s", target)
    LOGGER.info("Preprocess mode: %s", preprocess_mode)
    LOGGER.info("Preprocess enabled: %s", preprocess_enabled)
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
        "Prep params: enabled=%s outlier_sigma=%.2f flatten_window_length=%d no_flatten=%s",
        preprocess_enabled,
        outlier_sigma,
        flatten_window_length,
        no_flatten,
    )
    LOGGER.info("Author filter: %s", authors if authors else "all")
    LOGGER.info("Plot mode: %s", plot_mode)
    LOGGER.info("Plot enabled: %s", plot_enabled)
    LOGGER.info("Interactive HTML: %s", interactive_html)
    LOGGER.info("Interactive max points: %d", interactive_max_points)
    LOGGER.info(
        "BLS settings: enabled=%s mode=%s period=[%.2f, %.2f]d duration=[%.2f, %.2f]h n_periods=%d n_durations=%d top_n=%d",
        run_bls,
        bls_mode,
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
        vetting = stitched_vetting_by_rank.get(int(candidate.rank))
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
        if vetting is not None:
            LOGGER.info(
                "    vetting: pass=%s min_count=%s odd_even=%s alias=%s reasons=%s",
                vetting.vetting_pass,
                vetting.pass_min_transit_count,
                vetting.pass_odd_even_depth,
                vetting.pass_alias_harmonic,
                vetting.vetting_reasons,
            )
    LOGGER.info("Total runtime: %.2fs", runtime_seconds)
    LOGGER.info("Saved plot files: %d", len(output_paths))
    for path in output_paths:
        LOGGER.info("  - %s", path)
    LOGGER.info("Saved interactive plot files: %d", len(interactive_paths))
    for path in interactive_paths:
        LOGGER.info("  - %s", path)
    LOGGER.info("Saved preprocessing metrics CSV: %s", metrics_csv_path)
    LOGGER.info("Saved preprocessing metrics JSON: %s", metrics_json_path)
    LOGGER.info("Metrics cache file: %s", metrics_cache_path)
    LOGGER.info("Saved run manifest JSON: %s", manifest_path)
    if config_preset_id:
        LOGGER.info(
            "Config preset: id=%s version=%s hash=%s",
            config_preset_id,
            config_preset_version,
            config_preset_hash,
        )
    LOGGER.info("Saved run manifest index CSV (global): %s", manifest_global_index_path)
    LOGGER.info("Saved run manifest index CSV (target): %s", manifest_target_index_path)
    LOGGER.info("Saved BLS candidate CSV files: %d", len(candidate_csv_paths))
    for path in candidate_csv_paths:
        LOGGER.info("  - %s", path)
    LOGGER.info("Saved BLS candidate JSON files: %d", len(candidate_json_paths))
    for path in candidate_json_paths:
        LOGGER.info("  - %s", path)
    LOGGER.info("Candidate diagnostic asset sets: %d", len(diagnostic_assets))
    for periodogram_path, phasefold_path in diagnostic_assets:
        LOGGER.info("  - Saved periodogram: %s", periodogram_path)
        LOGGER.info("  - Saved phase-folded plot: %s", phasefold_path)
    LOGGER.info("--------------------------------")

    return output_paths[0] if output_paths else None


def fetch_and_plot(
    target: str,
    cache_dir: Path | None = None,
    refresh_cache: bool = False,
    outlier_sigma: float = 5.0,
    flatten_window_length: int = 401,
    max_download_files: int | None = None,
    preprocess_enabled: bool = True,
    no_flatten: bool = False,
    preprocess_mode: str = "per-sector",
    authors: str | None = None,
    interactive_html: bool = False,
    interactive_max_points: int = 200_000,
    plot_enabled: bool = True,
    plot_mode: str = "stitched",
    run_bls: bool = True,
    bls_period_min_days: float = 0.5,
    bls_period_max_days: float = 20.0,
    bls_duration_min_hours: float = 0.5,
    bls_duration_max_hours: float = 10.0,
    bls_n_periods: int = 2000,
    bls_n_durations: int = 12,
    bls_top_n: int = 5,
    bls_mode: str = "stitched",
    bls_min_snr: float = 7.0,
    bls_compute_fap: bool = False,
    bls_search_method: str = "bls",
    bls_fap_iterations: int = 1000,
    bls_iterative_masking: bool = False,
    bls_iterative_passes: int = 1,
    bls_iterative_top_n: int = 1,
    bls_transit_mask_padding_factor: float = 1.5,
    bls_subtraction_model: str = "box_mask",
    preprocess_iterative_flatten: bool = False,
    preprocess_transit_mask_padding_factor: float = 1.5,
    vetting_min_transit_count: int = 2,
    vetting_odd_even_max_mismatch_fraction: float = 0.30,
    vetting_alias_tolerance_fraction: float = 0.02,
    vetting_secondary_eclipse_max_fraction: float = 0.30,
    vetting_depth_consistency_max_fraction: float = 0.50,
    parameter_stellar_density_kg_m3: float = 1408.0,
    parameter_duration_ratio_min: float = 0.05,
    parameter_duration_ratio_max: float = 1.8,
    parameter_apply_limb_darkening_correction: bool = False,
    parameter_limb_darkening_u1: float = 0.4,
    parameter_limb_darkening_u2: float = 0.2,
    parameter_tic_density_lookup: bool = False,
    bls_unique_period_separation_fraction: float = 0.05,
    plot_smoothing_window: int = 5,
    config_schema_version: int = 1,
    config_preset_id: str | None = None,
    config_preset_version: int | None = None,
    config_preset_hash: str | None = None,
    no_cache: bool = False,
) -> Path | None:
    """Fetch, preprocess, analyze, and optionally plot a target light curve.

    Theory (milestone 17): ingest intentionally includes all available sectors.
    This defaults toward completeness, which improves transit recoverability for
    sparse or long-period events. The tradeoff is reduced user control over
    sector selection in the default workflow, but avoids accidental under-sampling.
    """
    started_at = perf_counter()
    cache_dir = cache_dir or _DEFAULT_CACHE_DIR
    run_started_dt = datetime.now(tz=timezone.utc)
    run_started_utc = run_started_dt.isoformat()
    preprocess_mode = _resolve_preprocess_mode(preprocess_mode)
    plot_mode = _resolve_two_track_mode(plot_mode, label="plot mode")
    bls_mode = _resolve_two_track_mode(bls_mode, label="BLS mode")
    selected_authors = _parse_authors(authors)

    # Stage 1: Ingest
    ingest = _ingest_stage(
        target=target, cache_dir=cache_dir, refresh_cache=refresh_cache,
        outlier_sigma=outlier_sigma, flatten_window_length=flatten_window_length,
        max_download_files=max_download_files, preprocess_enabled=preprocess_enabled,
        no_flatten=no_flatten, preprocess_mode=preprocess_mode,
        selected_authors=selected_authors, authors=authors,
        run_bls=run_bls, bls_duration_max_hours=bls_duration_max_hours,
        no_cache=no_cache,
    )
    lc = ingest.lc
    lc_prepared = ingest.lc_prepared

    # Stage 2: Metrics
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
        preprocess_enabled=preprocess_enabled,
        outlier_sigma=outlier_sigma,
        flatten_window_length=flatten_window_length,
        no_flatten=no_flatten,
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
        _save_cached_metrics(metrics_cache_path, metrics_payload, no_cache=no_cache)
    else:
        LOGGER.info("Preprocessing metrics cache hit: %s", metrics_cache_path)
    metrics_csv_path, metrics_json_path = _write_preprocessing_metrics(
        target=target,
        preprocess_mode=preprocess_mode,
        preprocess_enabled=preprocess_enabled,
        outlier_sigma=outlier_sigma,
        flatten_window_length=flatten_window_length,
        no_flatten=no_flatten,
        data_source=ingest.data_source,
        metrics=metrics_payload,
    )

    # Stage 3: Search + Output
    search = _search_and_output_stage(
        target=target, lc_prepared=lc_prepared,
        prepared_segments_for_bls=ingest.prepared_segments_for_bls,
        preprocess_mode=preprocess_mode, preprocess_enabled=preprocess_enabled,
        data_source=ingest.data_source, outlier_sigma=outlier_sigma,
        flatten_window_length=flatten_window_length, no_flatten=no_flatten,
        authors=authors, n_points_raw=n_points_raw,
        n_points_prepared=n_points_prepared, time_min=time_min, time_max=time_max,
        run_bls=run_bls, bls_period_min_days=bls_period_min_days,
        bls_period_max_days=bls_period_max_days,
        bls_duration_min_hours=bls_duration_min_hours,
        bls_duration_max_hours=bls_duration_max_hours,
        bls_n_periods=bls_n_periods, bls_n_durations=bls_n_durations,
        bls_top_n=bls_top_n, bls_mode=bls_mode, bls_search_method=bls_search_method,
        bls_min_snr=bls_min_snr,
        vetting_min_transit_count=vetting_min_transit_count,
        vetting_odd_even_max_mismatch_fraction=vetting_odd_even_max_mismatch_fraction,
        vetting_alias_tolerance_fraction=vetting_alias_tolerance_fraction,
        vetting_secondary_eclipse_max_fraction=vetting_secondary_eclipse_max_fraction,
        vetting_depth_consistency_max_fraction=vetting_depth_consistency_max_fraction,
        parameter_stellar_density_kg_m3=parameter_stellar_density_kg_m3,
        parameter_duration_ratio_min=parameter_duration_ratio_min,
        parameter_duration_ratio_max=parameter_duration_ratio_max,
        parameter_apply_limb_darkening_correction=parameter_apply_limb_darkening_correction,
        parameter_limb_darkening_u1=parameter_limb_darkening_u1,
        parameter_limb_darkening_u2=parameter_limb_darkening_u2,
        parameter_tic_density_lookup=parameter_tic_density_lookup,
        bls_unique_period_separation_fraction=bls_unique_period_separation_fraction,
        bls_iterative_masking=bls_iterative_masking,
        bls_iterative_passes=bls_iterative_passes,
        bls_iterative_top_n=bls_iterative_top_n,
        bls_transit_mask_padding_factor=bls_transit_mask_padding_factor,
        bls_subtraction_model=bls_subtraction_model,
        preprocess_iterative_flatten=preprocess_iterative_flatten,
        preprocess_transit_mask_padding_factor=preprocess_transit_mask_padding_factor,
        tpf=ingest.tpf,
    )

    # Stage 4: Plotting
    plots = _plotting_stage(
        target=target, lc=lc, lc_prepared=lc_prepared,
        boundaries=ingest.boundaries, plot_enabled=plot_enabled,
        plot_mode=plot_mode, preprocess_mode=preprocess_mode,
        interactive_html=interactive_html,
        interactive_max_points=interactive_max_points,
        raw_segments_for_plot=ingest.raw_segments_for_plot,
        prepared_segments_for_plot=ingest.prepared_segments_for_plot,
        smoothing_window=plot_smoothing_window,
    )

    # Stage 5: Manifest + Logging
    _manifest_stage(
        target=target, started_at=started_at, run_started_utc=run_started_utc,
        refresh_cache=refresh_cache, outlier_sigma=outlier_sigma,
        flatten_window_length=flatten_window_length,
        preprocess_enabled=preprocess_enabled, no_flatten=no_flatten,
        preprocess_mode=preprocess_mode, authors=authors,
        interactive_html=interactive_html,
        interactive_max_points=interactive_max_points,
        plot_enabled=plot_enabled, plot_mode=plot_mode,
        run_bls=run_bls, bls_mode=bls_mode,
        bls_period_min_days=bls_period_min_days,
        bls_period_max_days=bls_period_max_days,
        bls_duration_min_hours=bls_duration_min_hours,
        bls_duration_max_hours=bls_duration_max_hours,
        bls_n_periods=bls_n_periods, bls_n_durations=bls_n_durations,
        bls_top_n=bls_top_n, config_schema_version=config_schema_version,
        config_preset_id=config_preset_id,
        config_preset_version=config_preset_version,
        config_preset_hash=config_preset_hash,
        data_source=ingest.data_source, n_points_raw=n_points_raw,
        n_points_prepared=n_points_prepared, time_min=time_min, time_max=time_max,
        raw_cache_path=ingest.raw_cache_path,
        prepared_cache_path=ingest.prepared_cache_path,
        metrics_csv_path=metrics_csv_path, metrics_json_path=metrics_json_path,
        metrics_cache_path=metrics_cache_path,
        metrics_cache_hit=metrics_cache_hit, metrics_payload=metrics_payload,
        search_result=search, plot_result=plots,
    )

    return plots.output_paths[0] if plots.output_paths else None
