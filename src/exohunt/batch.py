from __future__ import annotations

import csv
import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter

from exohunt import pipeline as _pipeline_mod
from exohunt.config import PresetMeta, RuntimeConfig
from exohunt.progress import _render_progress

LOGGER = logging.getLogger(__name__)

_DEFAULT_CACHE_DIR = Path("outputs/cache/lightcurves")

_BATCH_STATUS_COLUMNS = [
    "run_utc",
    "target",
    "status",
    "error",
    "runtime_seconds",
    "output_path",
]


@dataclass(frozen=True)
class BatchTargetStatus:
    run_utc: str
    target: str
    status: str
    error: str
    runtime_seconds: float
    output_path: str


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
    config: RuntimeConfig,
    preset_meta: PresetMeta | None = None,
    *,
    resume: bool = False,
    no_cache: bool = False,
    cache_dir: Path | None = None,
    max_download_files: int | None = None,
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
                    output_path = _pipeline_mod.fetch_and_plot(
                        target=target,
                        config=config,
                        preset_meta=preset_meta,
                        cache_dir=cache_dir,
                        no_cache=no_cache,
                        max_download_files=max_download_files,
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
