from __future__ import annotations

import csv
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from exohunt.cache import content_hash, _safe_target_name, _target_artifact_dir

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
    key = content_hash(payload)
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
    *,
    run_dir: Path,
) -> tuple[Path, Path]:
    aggregate_output_dir = run_dir
    aggregate_output_dir.mkdir(parents=True, exist_ok=True)
    target_output_dir = _target_artifact_dir(target, "metrics", outputs_root=run_dir)
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
