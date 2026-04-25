from __future__ import annotations

import csv
import json
import logging
import platform
import sys
from importlib.metadata import PackageNotFoundError, version as package_version
from pathlib import Path

from exohunt.cache import content_hash, _safe_target_name, _target_artifact_dir

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


def _hash_payload(payload: dict[str, object]) -> str:
    return content_hash(payload)


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
    run_dir: Path,
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

    target_manifest_dir = _target_artifact_dir(target, "manifests", outputs_root=run_dir)
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

    run_index_path = run_dir / "run_manifest_index.csv"
    target_index_path = target_manifest_dir / "run_manifest_index.csv"
    _write_manifest_index_row(run_index_path, index_row)
    _write_manifest_index_row(target_index_path, index_row)
    return manifest_path, run_index_path, target_index_path


_README_LOGGER = logging.getLogger(__name__)


def write_run_readme(
    run_dir: Path, config, preset_meta,
    *, targets: list[str],
    started_utc: str, finished_utc: str, runtime_seconds: float,
    success_count: int, failure_count: int,
    errors: dict[str, str] | None = None,
) -> Path:
    """Write a human-readable README.md describing this run."""
    try:
        preset_label = (
            f"`{preset_meta.name}` (version={preset_meta.version}, hash=`{preset_meta.hash}`)"
            if preset_meta.is_set
            else "custom (no preset)"
        )
        lines = [
            f"# Run: {run_dir.name}",
            "",
            f"- **Started (UTC):** {started_utc}",
            f"- **Finished (UTC):** {finished_utc}",
            f"- **Runtime:** {runtime_seconds:.1f}s",
            f"- **Preset:** {preset_label}",
            f"- **Targets:** {len(targets)} "
            f"({success_count} succeeded, {failure_count} failed)",
            "",
            "## Targets",
            "",
        ]
        for t in targets:
            err = (errors or {}).get(t)
            status = f"❌ {err}" if err else "✓"
            lines.append(f"- `{t}` — {status}")
        lines.append("")
        readme_path = run_dir / "README.md"
        readme_path.write_text("\n".join(lines), encoding="utf-8")
        return readme_path
    except Exception as exc:
        _README_LOGGER.warning("Failed to write run README: %s", exc)
        return run_dir / "README.md"
