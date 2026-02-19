from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore[no-redef]


_ALLOWED_MODE_VALUES = {"stitched", "per-sector"}


class ConfigValidationError(ValueError):
    """Raised when configuration schema validation fails."""


@dataclass(frozen=True)
class IOConfig:
    refresh_cache: bool


@dataclass(frozen=True)
class IngestConfig:
    authors: tuple[str, ...]


@dataclass(frozen=True)
class PreprocessConfig:
    enabled: bool
    mode: str
    outlier_sigma: float
    flatten_window_length: int
    flatten: bool


@dataclass(frozen=True)
class PlotConfig:
    enabled: bool
    mode: str
    interactive_html: bool
    interactive_max_points: int


@dataclass(frozen=True)
class BLSConfig:
    enabled: bool
    mode: str
    period_min_days: float
    period_max_days: float
    duration_min_hours: float
    duration_max_hours: float
    n_periods: int
    n_durations: int
    top_n: int


@dataclass(frozen=True)
class RuntimeConfig:
    schema_version: int
    preset: str | None
    io: IOConfig
    ingest: IngestConfig
    preprocess: PreprocessConfig
    plot: PlotConfig
    bls: BLSConfig


_DEFAULTS: dict[str, Any] = {
    "schema_version": 1,
    "preset": None,
    "io": {
        "refresh_cache": False,
    },
    "ingest": {
        "authors": ["SPOC"],
    },
    "preprocess": {
        "enabled": True,
        "mode": "per-sector",
        "outlier_sigma": 5.0,
        "flatten_window_length": 401,
        "flatten": True,
    },
    "plot": {
        "enabled": True,
        "mode": "stitched",
        "interactive_html": False,
        "interactive_max_points": 200_000,
    },
    "bls": {
        "enabled": True,
        "mode": "stitched",
        "period_min_days": 0.5,
        "period_max_days": 20.0,
        "duration_min_hours": 0.5,
        "duration_max_hours": 10.0,
        "n_periods": 2000,
        "n_durations": 12,
        "top_n": 5,
    },
}


def _deep_merge(
    base: dict[str, Any],
    patch: Mapping[str, Any],
    *,
    schema: Mapping[str, Any],
    scope: str,
) -> None:
    for key, value in patch.items():
        if key not in schema:
            raise ConfigValidationError(f"Unknown config key at {scope}.{key}")
        if isinstance(schema[key], dict):
            if not isinstance(value, Mapping):
                raise ConfigValidationError(
                    f"Invalid type at {scope}.{key}: expected table/object, got {type(value).__name__}"
                )
            _deep_merge(
                base[key],
                value,
                schema=schema[key],
                scope=f"{scope}.{key}",
            )
        else:
            base[key] = value


def _normalize_mode(raw: Any, *, key_path: str) -> str:
    if not isinstance(raw, str):
        raise ConfigValidationError(
            f"Invalid type at {key_path}: expected string, got {type(raw).__name__}"
        )
    value = raw.strip().lower()
    if value == "global":
        return "stitched"
    if value not in _ALLOWED_MODE_VALUES:
        raise ConfigValidationError(
            f"Invalid value at {key_path}: {raw!r}. Expected one of: stitched, per-sector."
        )
    return value


def _expect_bool(payload: Mapping[str, Any], key: str, *, scope: str) -> bool:
    value = payload[key]
    if not isinstance(value, bool):
        raise ConfigValidationError(
            f"Invalid type at {scope}.{key}: expected bool, got {type(value).__name__}"
        )
    return value


def _expect_int(payload: Mapping[str, Any], key: str, *, scope: str) -> int:
    value = payload[key]
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigValidationError(
            f"Invalid type at {scope}.{key}: expected int, got {type(value).__name__}"
        )
    return value


def _expect_float(payload: Mapping[str, Any], key: str, *, scope: str) -> float:
    value = payload[key]
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        raise ConfigValidationError(
            f"Invalid type at {scope}.{key}: expected float, got {type(value).__name__}"
        )
    return float(value)


def _expect_optional_string(value: Any, *, key_path: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ConfigValidationError(
            f"Invalid type at {key_path}: expected string|null, got {type(value).__name__}"
        )
    normalized = value.strip()
    return normalized if normalized else None


def _validate_authors(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ConfigValidationError(
            f"Invalid type at ingest.authors: expected list[str], got {type(value).__name__}"
        )
    authors: list[str] = []
    for idx, raw in enumerate(value):
        if not isinstance(raw, str):
            raise ConfigValidationError(
                f"Invalid type at ingest.authors[{idx}]: expected string, got {type(raw).__name__}"
            )
        normalized = raw.strip().upper()
        if not normalized:
            raise ConfigValidationError(
                f"Invalid value at ingest.authors[{idx}]: empty author string is not allowed"
            )
        authors.append(normalized)
    return tuple(authors)


def _load_toml(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as handle:
            payload = tomllib.load(handle)
    except FileNotFoundError as exc:
        raise ConfigValidationError(f"Config file not found: {path}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigValidationError(f"Invalid TOML in config file {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ConfigValidationError(f"Invalid TOML root in {path}: expected table/object")
    return payload


def resolve_runtime_config(
    *,
    config_path: Path | None = None,
    preset_name: str | None = None,
    preset_values: Mapping[str, Mapping[str, Any]] | None = None,
    cli_overrides: Mapping[str, Any] | None = None,
) -> RuntimeConfig:
    """Resolve canonical runtime configuration from deterministic layered inputs.

    Theory: reproducible scientific runs require a single canonical config
    object assembled by stable ordering. Layering defaults, preset, user file,
    and explicit CLI overrides gives deterministic behavior while preserving
    ergonomic overrides for one-off runs.
    """
    merged: dict[str, Any] = deepcopy(_DEFAULTS)
    schema = _DEFAULTS

    active_preset: str | None = _expect_optional_string(preset_name, key_path="preset")
    presets = dict(preset_values or {})
    if active_preset is not None:
        if active_preset not in presets:
            available = ", ".join(sorted(presets)) if presets else "none"
            raise ConfigValidationError(
                f"Unknown preset: {active_preset!r}. Available presets: {available}."
            )
        _deep_merge(
            merged,
            presets[active_preset],
            schema=schema,
            scope="preset",
        )
        merged["preset"] = active_preset

    if config_path is not None:
        file_payload = _load_toml(config_path)
        _deep_merge(
            merged,
            file_payload,
            schema=schema,
            scope="config",
        )

    if cli_overrides:
        _deep_merge(
            merged,
            cli_overrides,
            schema=schema,
            scope="cli",
        )

    schema_version = _expect_int(merged, "schema_version", scope="root")
    if schema_version != 1:
        raise ConfigValidationError(f"Unsupported schema_version: {schema_version}. Expected 1.")

    io_data = merged["io"]
    ingest_data = merged["ingest"]
    preprocess_data = merged["preprocess"]
    plot_data = merged["plot"]
    bls_data = merged["bls"]

    io = IOConfig(
        refresh_cache=_expect_bool(io_data, "refresh_cache", scope="io"),
    )
    ingest = IngestConfig(authors=_validate_authors(ingest_data["authors"]))
    preprocess = PreprocessConfig(
        enabled=_expect_bool(preprocess_data, "enabled", scope="preprocess"),
        mode=_normalize_mode(preprocess_data["mode"], key_path="preprocess.mode"),
        outlier_sigma=_expect_float(preprocess_data, "outlier_sigma", scope="preprocess"),
        flatten_window_length=_expect_int(
            preprocess_data, "flatten_window_length", scope="preprocess"
        ),
        flatten=_expect_bool(preprocess_data, "flatten", scope="preprocess"),
    )
    plot = PlotConfig(
        enabled=_expect_bool(plot_data, "enabled", scope="plot"),
        mode=_normalize_mode(plot_data["mode"], key_path="plot.mode"),
        interactive_html=_expect_bool(plot_data, "interactive_html", scope="plot"),
        interactive_max_points=_expect_int(plot_data, "interactive_max_points", scope="plot"),
    )
    bls = BLSConfig(
        enabled=_expect_bool(bls_data, "enabled", scope="bls"),
        mode=_normalize_mode(bls_data["mode"], key_path="bls.mode"),
        period_min_days=_expect_float(bls_data, "period_min_days", scope="bls"),
        period_max_days=_expect_float(bls_data, "period_max_days", scope="bls"),
        duration_min_hours=_expect_float(bls_data, "duration_min_hours", scope="bls"),
        duration_max_hours=_expect_float(bls_data, "duration_max_hours", scope="bls"),
        n_periods=_expect_int(bls_data, "n_periods", scope="bls"),
        n_durations=_expect_int(bls_data, "n_durations", scope="bls"),
        top_n=_expect_int(bls_data, "top_n", scope="bls"),
    )

    if preprocess.outlier_sigma <= 0.0:
        raise ConfigValidationError("Invalid preprocess.outlier_sigma: must be > 0.")
    if preprocess.flatten_window_length <= 0 or preprocess.flatten_window_length % 2 == 0:
        raise ConfigValidationError(
            "Invalid preprocess.flatten_window_length: must be a positive odd integer."
        )
    if bls.period_min_days <= 0.0 or bls.period_min_days >= bls.period_max_days:
        raise ConfigValidationError(
            "Invalid bls.period range: require 0 < period_min_days < period_max_days."
        )
    if bls.duration_min_hours <= 0.0 or bls.duration_min_hours >= bls.duration_max_hours:
        raise ConfigValidationError(
            "Invalid bls.duration range: require 0 < duration_min_hours < duration_max_hours."
        )
    if bls.n_periods < 1 or bls.n_durations < 1 or bls.top_n < 1:
        raise ConfigValidationError(
            "Invalid bls grid sizes: n_periods, n_durations, and top_n must all be >= 1."
        )
    if plot.interactive_max_points < 1000:
        raise ConfigValidationError("Invalid plot.interactive_max_points: must be >= 1000.")
    if plot.mode == "per-sector" and preprocess.mode != "per-sector":
        raise ConfigValidationError(
            "Invalid mode coupling: plot.mode='per-sector' requires preprocess.mode='per-sector'."
        )

    return RuntimeConfig(
        schema_version=schema_version,
        preset=_expect_optional_string(merged.get("preset"), key_path="preset"),
        io=io,
        ingest=ingest,
        preprocess=preprocess,
        plot=plot,
        bls=bls,
    )
