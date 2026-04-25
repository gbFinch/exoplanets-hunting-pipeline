from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

import lightkurve as lk
import numpy as np

from exohunt.models import LightCurveSegment


def content_hash(payload: Mapping[str, Any], *, length: int = 16) -> str:
    """Compute a stable short SHA-1 hash of a JSON-serializable dict.

    Note: SHA-1 is used for cache-key backward compatibility, not for security.
    Changing the algorithm would invalidate all existing cache files. Do not
    change without a cache migration strategy.
    """
    encoded = json.dumps(payload, sort_keys=True).encode("utf-8")
    return hashlib.sha1(encoded).hexdigest()[:length]


def _safe_target_name(target: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in target).strip("_").lower()


DEFAULT_CACHE_DIR = Path("outputs/cache/lightcurves")


def _target_output_dir(target: str, outputs_root: Path) -> Path:
    """Return <outputs_root>/<safe_target_name>/. outputs_root is required."""
    return outputs_root / _safe_target_name(target)


def _target_artifact_dir(
    target: str, artifact_name: str, outputs_root: Path
) -> Path:
    return _target_output_dir(target=target, outputs_root=outputs_root) / artifact_name


def _cache_path(target: str, cache_dir: Path) -> Path:
    return cache_dir / f"{_safe_target_name(target)}.npz"


def _prepared_cache_key(
    outlier_sigma: float,
    flatten_window_length: int,
    no_flatten: bool,
) -> str:
    payload = {
        "version": 1,
        "outlier_sigma": round(float(outlier_sigma), 6),
        "flatten_window_length": int(flatten_window_length),
        "no_flatten": bool(no_flatten),
    }
    return content_hash(payload, length=12)


def _prepared_cache_path(
    target: str,
    cache_dir: Path,
    outlier_sigma: float,
    flatten_window_length: int,
    no_flatten: bool,
) -> Path:
    key = _prepared_cache_key(
        outlier_sigma=outlier_sigma,
        flatten_window_length=flatten_window_length,
        no_flatten=no_flatten,
    )
    return cache_dir / f"{_safe_target_name(target)}__prep_{key}.npz"


def _segment_base_dir(target: str, cache_dir: Path) -> Path:
    return cache_dir / "segments" / _safe_target_name(target)


def _segment_manifest_path(target: str, cache_dir: Path) -> Path:
    return _segment_base_dir(target, cache_dir) / "manifest.json"


def _segment_raw_cache_path(target: str, cache_dir: Path, segment_id: str) -> Path:
    return _segment_base_dir(target, cache_dir) / f"{segment_id}__raw.npz"


def _segment_prepared_cache_path(
    target: str,
    cache_dir: Path,
    segment_id: str,
    outlier_sigma: float,
    flatten_window_length: int,
    no_flatten: bool,
) -> Path:
    key = _prepared_cache_key(
        outlier_sigma=outlier_sigma,
        flatten_window_length=flatten_window_length,
        no_flatten=no_flatten,
    )
    return _segment_base_dir(target, cache_dir) / f"{segment_id}__prep_{key}.npz"


def _load_npz_lightcurve(cache_path: Path) -> lk.LightCurve:
    with np.load(cache_path) as cached:
        return lk.LightCurve(time=cached["time"], flux=cached["flux"])


def _save_npz_lightcurve(cache_path: Path, lc: lk.LightCurve, *, no_cache: bool = False) -> None:
    if no_cache:
        return
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    np.savez(cache_path, time=lc.time.value, flux=lc.flux.value)


def _write_segment_manifest(target: str, cache_dir: Path, segments: list[LightCurveSegment], *, no_cache: bool = False) -> None:
    if no_cache:
        return
    manifest_path = _segment_manifest_path(target, cache_dir)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "target": target,
        "segments": [
            {
                "segment_id": segment.segment_id,
                "sector": segment.sector,
                "author": segment.author,
                "cadence": segment.cadence,
            }
            for segment in segments
        ],
    }
    manifest_path.write_text(json.dumps(payload, indent=2))


def _load_segment_manifest(target: str, cache_dir: Path) -> list[dict[str, Any]]:
    manifest_path = _segment_manifest_path(target, cache_dir)
    if not manifest_path.exists():
        return []
    payload = json.loads(manifest_path.read_text())
    return list(payload.get("segments", []))
