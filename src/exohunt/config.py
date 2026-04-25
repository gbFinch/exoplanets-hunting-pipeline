from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources
import json
import logging
from pathlib import Path
from typing import Any, Mapping

from exohunt.cache import content_hash

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore[no-redef]


_ALLOWED_MODE_VALUES = {"stitched", "per-sector"}
BUILTIN_PRESET_PACK_VERSION = 1
LOGGER = logging.getLogger(__name__)


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
    iterative_flatten: bool
    transit_mask_padding_factor: float


@dataclass(frozen=True)
class PlotConfig:
    enabled: bool
    mode: str
    interactive_html: bool
    interactive_max_points: int
    smoothing_window: int


@dataclass(frozen=True)
class BLSConfig:
    enabled: bool
    mode: str
    search_method: str  # "bls" or "tls"
    period_min_days: float
    period_max_days: float
    duration_min_hours: float
    duration_max_hours: float
    n_periods: int
    n_durations: int
    top_n: int
    min_snr: float
    compute_fap: bool
    fap_iterations: int
    iterative_masking: bool
    unique_period_separation_fraction: float
    iterative_passes: int
    subtraction_model: str
    iterative_top_n: int
    transit_mask_padding_factor: float


@dataclass(frozen=True)
class VettingConfig:
    min_transit_count: int
    odd_even_max_mismatch_fraction: float
    alias_tolerance_fraction: float
    secondary_eclipse_max_fraction: float
    depth_consistency_max_fraction: float
    triceratops_enabled: bool = False
    triceratops_n: int = 100_000


@dataclass(frozen=True)
class ParameterConfig:
    stellar_density_kg_m3: float
    duration_ratio_min: float
    duration_ratio_max: float
    apply_limb_darkening_correction: bool
    limb_darkening_u1: float
    limb_darkening_u2: float
    tic_density_lookup: bool


@dataclass(frozen=True)
class RuntimeConfig:
    schema_version: int
    preset: str | None
    io: IOConfig
    ingest: IngestConfig
    preprocess: PreprocessConfig
    plot: PlotConfig
    bls: BLSConfig
    vetting: VettingConfig
    parameters: ParameterConfig


@dataclass(frozen=True)
class PresetMeta:
    name: str | None = None
    version: int | None = None
    hash: str | None = None

    @property
    def is_set(self) -> bool:
        return self.name is not None


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
        "iterative_flatten": False,
        "transit_mask_padding_factor": 1.5,
    },
    "plot": {
        "enabled": True,
        "mode": "stitched",
        "interactive_html": False,
        "interactive_max_points": 200_000,
        "smoothing_window": 5,
    },
    "bls": {
        "enabled": True,
        "mode": "stitched",
        "search_method": "bls",
        "period_min_days": 0.5,
        "period_max_days": 20.0,
        "duration_min_hours": 0.5,
        "duration_max_hours": 10.0,
        "n_periods": 2000,
        "n_durations": 12,
        "top_n": 5,
        "min_snr": 7.0,
        "compute_fap": False,
        "fap_iterations": 1000,
        "iterative_masking": False,
        "unique_period_separation_fraction": 0.05,
        "iterative_passes": 1,
        "subtraction_model": "box_mask",
        "iterative_top_n": 1,
        "transit_mask_padding_factor": 1.5,
    },
    "vetting": {
        "min_transit_count": 2,
        "odd_even_max_mismatch_fraction": 0.30,
        "alias_tolerance_fraction": 0.02,
        "secondary_eclipse_max_fraction": 0.30,
        "depth_consistency_max_fraction": 0.50,
        "triceratops_enabled": False,
        "triceratops_n": 100_000,
    },
    "parameters": {
        "stellar_density_kg_m3": 1408.0,
        "duration_ratio_min": 0.05,
        "duration_ratio_max": 1.8,
        "apply_limb_darkening_correction": False,
        "limb_darkening_u1": 0.4,
        "limb_darkening_u2": 0.2,
        "tic_density_lookup": False,
    },
}

_DEPRECATED_KEY_MESSAGES = {
    "ingest.sectors": (
        "ingest sector filtering has been removed; exohunt now ingests all sectors."
    ),
    "plot.time_start_btjd": (
        "plot time-window and sector filters have been removed; "
        "use plot.mode=stitched or plot.mode=per-sector."
    ),
    "plot.time_end_btjd": (
        "plot time-window and sector filters have been removed; "
        "use plot.mode=stitched or plot.mode=per-sector."
    ),
    "plot.sectors": (
        "plot time-window and sector filters have been removed; "
        "use plot.mode=stitched or plot.mode=per-sector."
    ),
    "cache_dir": "cache_dir has been removed from user config; use fixed internal cache paths.",
    "max_download_files": (
        "max_download_files has been removed from user config; "
        "standard workflow now uses full ingest without download caps."
    ),
}


def list_builtin_presets() -> tuple[str, ...]:
    return tuple(sorted(_load_builtin_preset_documents()))


@lru_cache(maxsize=1)
def _load_builtin_preset_documents() -> dict[str, dict[str, Any]]:
    presets: dict[str, dict[str, Any]] = {}
    preset_dir = resources.files("exohunt.presets")
    for item in preset_dir.iterdir():
        if item.name.startswith(".") or Path(item.name).suffix != ".toml":
            continue
        with item.open("rb") as handle:
            payload = tomllib.load(handle)
        if not isinstance(payload, dict):
            raise ConfigValidationError(f"Invalid preset document format: {item.name}")
        presets[Path(item.name).stem] = payload
    if not presets:
        raise ConfigValidationError("No built-in preset files found under exohunt.presets.")
    return presets


def _load_builtin_preset_values() -> dict[str, dict[str, Any]]:
    values: dict[str, dict[str, Any]] = {}
    for name, payload in _load_builtin_preset_documents().items():
        merged_payload = deepcopy(payload)
        merged_payload.pop("schema_version", None)
        merged_payload.pop("preset", None)
        values[name] = merged_payload
    return values


def _stable_hash(payload: Mapping[str, Any]) -> str:
    return content_hash(payload)


def get_builtin_preset_metadata(name: str) -> PresetMeta:
    preset_values = _load_builtin_preset_documents().get(name)
    if preset_values is None:
        available = ", ".join(list_builtin_presets())
        raise ConfigValidationError(f"Unknown preset: {name!r}. Available presets: {available}.")
    preset_hash = _stable_hash(
        {
            "pack_version": BUILTIN_PRESET_PACK_VERSION,
            "schema_version": _DEFAULTS["schema_version"],
            "preset": name,
            "values": preset_values,
        }
    )
    return PresetMeta(name=name, version=BUILTIN_PRESET_PACK_VERSION, hash=preset_hash)


def _encode_toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        escaped = value.replace('"', '\\"')
        return f'"{escaped}"'
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return f"{value:.10g}"
    if isinstance(value, list):
        return "[" + ", ".join(_encode_toml_value(item) for item in value) + "]"
    raise TypeError(f"Unsupported TOML value type: {type(value).__name__}")


def _dump_toml(payload: Mapping[str, Any]) -> str:
    lines: list[str] = []
    scalar_keys = ["schema_version", "preset"]
    for key in scalar_keys:
        if key in payload:
            lines.append(f"{key} = {_encode_toml_value(payload[key])}")
    for section in ["io", "ingest", "preprocess", "plot", "bls", "vetting", "parameters"]:
        value = payload.get(section)
        if not isinstance(value, Mapping):
            continue
        lines.append("")
        lines.append(f"[{section}]")
        for key, item in value.items():
            lines.append(f"{key} = {_encode_toml_value(item)}")
    return "\n".join(lines) + "\n"


def write_preset_config(*, preset_name: str, out_path: Path) -> Path:
    preset_doc = _load_builtin_preset_documents().get(preset_name)
    if preset_doc is None:
        available = ", ".join(list_builtin_presets())
        raise ConfigValidationError(
            f"Unknown preset: {preset_name!r}. Available presets: {available}."
        )
    payload = deepcopy(_DEFAULTS)
    _deep_merge(payload, preset_doc, schema=_DEFAULTS, scope="preset")
    payload["preset"] = preset_name
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(_dump_toml(payload), encoding="utf-8")
    return out_path


def _deep_merge(
    base: dict[str, Any],
    patch: Mapping[str, Any],
    *,
    schema: Mapping[str, Any],
    scope: str,
) -> None:
    for key, value in patch.items():
        if key not in schema:
            visible_scope = scope.split(".", 1)[1] if "." in scope else ""
            normalized = f"{visible_scope}.{key}".strip(".")
            deprecation_message = _DEPRECATED_KEY_MESSAGES.get(normalized)
            if deprecation_message is not None:
                raise ConfigValidationError(f"{deprecation_message} (key: {scope}.{key})")
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
        LOGGER.warning("Deprecated mode value 'global' for %s; using 'stitched'.", key_path)
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
    presets = dict(preset_values or _load_builtin_preset_values())

    file_payload: dict[str, Any] = {}
    file_preset: str | None = None
    if config_path is not None:
        file_payload = _load_toml(config_path)
        if "preset" in file_payload:
            file_preset = _expect_optional_string(file_payload["preset"], key_path="config.preset")

    active_preset: str | None = _expect_optional_string(preset_name, key_path="preset")
    if active_preset is None:
        active_preset = file_preset

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

    if file_payload:
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
        iterative_flatten=_expect_bool(preprocess_data, "iterative_flatten", scope="preprocess"),
        transit_mask_padding_factor=_expect_float(
            preprocess_data, "transit_mask_padding_factor", scope="preprocess"
        ),
    )
    plot = PlotConfig(
        enabled=_expect_bool(plot_data, "enabled", scope="plot"),
        mode=_normalize_mode(plot_data["mode"], key_path="plot.mode"),
        interactive_html=_expect_bool(plot_data, "interactive_html", scope="plot"),
        interactive_max_points=_expect_int(plot_data, "interactive_max_points", scope="plot"),
        smoothing_window=_expect_int(plot_data, "smoothing_window", scope="plot"),
    )
    bls = BLSConfig(
        enabled=_expect_bool(bls_data, "enabled", scope="bls"),
        mode=_normalize_mode(bls_data["mode"], key_path="bls.mode"),
        search_method=str(bls_data.get("search_method", "bls")),
        period_min_days=_expect_float(bls_data, "period_min_days", scope="bls"),
        period_max_days=_expect_float(bls_data, "period_max_days", scope="bls"),
        duration_min_hours=_expect_float(bls_data, "duration_min_hours", scope="bls"),
        duration_max_hours=_expect_float(bls_data, "duration_max_hours", scope="bls"),
        n_periods=_expect_int(bls_data, "n_periods", scope="bls"),
        n_durations=_expect_int(bls_data, "n_durations", scope="bls"),
        top_n=_expect_int(bls_data, "top_n", scope="bls"),
        min_snr=_expect_float(bls_data, "min_snr", scope="bls"),
        compute_fap=_expect_bool(bls_data, "compute_fap", scope="bls"),
        fap_iterations=_expect_int(bls_data, "fap_iterations", scope="bls"),
        iterative_masking=_expect_bool(bls_data, "iterative_masking", scope="bls"),
        unique_period_separation_fraction=_expect_float(
            bls_data, "unique_period_separation_fraction", scope="bls"
        ),
        iterative_passes=_expect_int(bls_data, "iterative_passes", scope="bls"),
        subtraction_model=str(bls_data.get("subtraction_model", "box_mask")),
        iterative_top_n=_expect_int(bls_data, "iterative_top_n", scope="bls"),
        transit_mask_padding_factor=_expect_float(
            bls_data, "transit_mask_padding_factor", scope="bls"
        ),
    )

    vetting_data = merged["vetting"]
    vetting = VettingConfig(
        min_transit_count=_expect_int(vetting_data, "min_transit_count", scope="vetting"),
        odd_even_max_mismatch_fraction=_expect_float(
            vetting_data, "odd_even_max_mismatch_fraction", scope="vetting"
        ),
        alias_tolerance_fraction=_expect_float(
            vetting_data, "alias_tolerance_fraction", scope="vetting"
        ),
        secondary_eclipse_max_fraction=_expect_float(
            vetting_data, "secondary_eclipse_max_fraction", scope="vetting"
        ),
        depth_consistency_max_fraction=_expect_float(
            vetting_data, "depth_consistency_max_fraction", scope="vetting"
        ),
        triceratops_enabled=_expect_bool(vetting_data, "triceratops_enabled", scope="vetting") if "triceratops_enabled" in vetting_data else False,
        triceratops_n=_expect_int(vetting_data, "triceratops_n", scope="vetting") if "triceratops_n" in vetting_data else 100_000,
    )

    parameters_data = merged["parameters"]
    parameters = ParameterConfig(
        stellar_density_kg_m3=_expect_float(
            parameters_data, "stellar_density_kg_m3", scope="parameters"
        ),
        duration_ratio_min=_expect_float(
            parameters_data, "duration_ratio_min", scope="parameters"
        ),
        duration_ratio_max=_expect_float(
            parameters_data, "duration_ratio_max", scope="parameters"
        ),
        apply_limb_darkening_correction=_expect_bool(
            parameters_data, "apply_limb_darkening_correction", scope="parameters"
        ),
        limb_darkening_u1=_expect_float(
            parameters_data, "limb_darkening_u1", scope="parameters"
        ),
        limb_darkening_u2=_expect_float(
            parameters_data, "limb_darkening_u2", scope="parameters"
        ),
        tic_density_lookup=_expect_bool(
            parameters_data, "tic_density_lookup", scope="parameters"
        ),
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
    if bls.min_snr < 0.0:
        raise ConfigValidationError("Invalid bls.min_snr: must be >= 0.")
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
        vetting=vetting,
        parameters=parameters,
    )
