from __future__ import annotations

from pathlib import Path

import logging
import pytest

from exohunt.config import (
    BUILTIN_PRESET_PACK_VERSION,
    ConfigValidationError,
    get_builtin_preset_metadata,
    list_builtin_presets,
    resolve_runtime_config,
    write_preset_config,
)


def test_resolve_runtime_config_uses_expected_merge_order(tmp_path: Path):
    user_config = tmp_path / "run.toml"
    user_config.write_text(
        "\n".join(
            [
                "schema_version = 1",
                "",
                "[preprocess]",
                "outlier_sigma = 4.2",
                "mode = 'per-sector'",
                "",
                "[bls]",
                "top_n = 11",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    cfg = resolve_runtime_config(
        preset_name="preset-a",
        preset_values={
            "preset-a": {
                "preprocess": {"outlier_sigma": 7.0, "mode": "stitched"},
                "bls": {"top_n": 7},
            }
        },
        config_path=user_config,
        cli_overrides={
            "preprocess": {"outlier_sigma": 3.5},
            "bls": {"top_n": 5},
        },
    )

    assert cfg.preprocess.outlier_sigma == pytest.approx(3.5)
    assert cfg.preprocess.mode == "per-sector"
    assert cfg.bls.top_n == 5


def test_resolve_runtime_config_rejects_unknown_keys(tmp_path: Path):
    user_config = tmp_path / "invalid.toml"
    user_config.write_text(
        "\n".join(
            [
                "schema_version = 1",
                "[plot]",
                "foo = 100.0",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ConfigValidationError, match="Unknown config key"):
        resolve_runtime_config(config_path=user_config)


def test_resolve_runtime_config_rejects_invalid_mode_coupling():
    with pytest.raises(ConfigValidationError, match="mode coupling"):
        resolve_runtime_config(
            cli_overrides={
                "preprocess": {"mode": "stitched"},
                "plot": {"mode": "per-sector"},
            }
        )


def test_resolve_runtime_config_maps_global_to_stitched():
    cfg = resolve_runtime_config(cli_overrides={"preprocess": {"mode": "global"}})
    assert cfg.preprocess.mode == "stitched"


def test_resolve_runtime_config_logs_deprecated_global_mapping(caplog: pytest.LogCaptureFixture):
    caplog.set_level(logging.WARNING)
    resolve_runtime_config(cli_overrides={"preprocess": {"mode": "global"}})
    assert "Deprecated mode value 'global'" in caplog.text


def test_resolve_runtime_config_requires_odd_flatten_window_length():
    with pytest.raises(ConfigValidationError, match="flatten_window_length"):
        resolve_runtime_config(
            cli_overrides={
                "preprocess": {"flatten_window_length": 400},
            }
        )


def test_resolve_runtime_config_reports_removed_sector_filter(tmp_path: Path):
    config_path = tmp_path / "invalid-sectors.toml"
    config_path.write_text("[ingest]\nsectors = [14, 15]\n", encoding="utf-8")
    with pytest.raises(ConfigValidationError, match="ingests all sectors"):
        resolve_runtime_config(config_path=config_path)


def test_resolve_runtime_config_reports_removed_plot_filters(tmp_path: Path):
    config_path = tmp_path / "invalid-plot.toml"
    config_path.write_text("[plot]\ntime_start_btjd = 100.0\n", encoding="utf-8")
    with pytest.raises(ConfigValidationError, match="plot.mode=stitched or plot.mode=per-sector"):
        resolve_runtime_config(config_path=config_path)


def test_resolve_runtime_config_reports_removed_cache_dir(tmp_path: Path):
    config_path = tmp_path / "invalid-cache.toml"
    config_path.write_text('cache_dir = "outputs/cache/lightcurves"\n', encoding="utf-8")
    with pytest.raises(ConfigValidationError, match="cache_dir has been removed"):
        resolve_runtime_config(config_path=config_path)


def test_builtin_presets_available():
    assert set(list_builtin_presets()) == {"deep-search", "iterative-search", "quicklook", "science-default"}


def test_resolve_runtime_config_with_builtin_preset():
    cfg = resolve_runtime_config(preset_name="deep-search")
    assert cfg.preset == "deep-search"
    assert cfg.preprocess.flatten_window_length == 801
    assert cfg.bls.n_periods == 8000
    assert cfg.plot.interactive_html is True


def test_resolve_runtime_config_reads_preset_from_file(tmp_path: Path):
    config_path = tmp_path / "from-file.toml"
    config_path.write_text('schema_version = 1\npreset = "quicklook"\n', encoding="utf-8")
    cfg = resolve_runtime_config(config_path=config_path)
    assert cfg.preset == "quicklook"
    assert cfg.preprocess.outlier_sigma == pytest.approx(4.0)


def test_write_preset_config(tmp_path: Path):
    out_path = tmp_path / "configs" / "science-default.toml"
    write_preset_config(preset_name="science-default", out_path=out_path)
    content = out_path.read_text(encoding="utf-8")
    assert 'preset = "science-default"' in content
    assert "[preprocess]" in content
    assert "flatten_window_length = 801" in content


def test_get_builtin_preset_metadata_returns_stable_version_and_hash():
    meta = get_builtin_preset_metadata("science-default")
    assert meta.name == "science-default"
    assert meta.version == BUILTIN_PRESET_PACK_VERSION
    assert len(meta.hash) == 16
