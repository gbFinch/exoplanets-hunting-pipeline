from __future__ import annotations

from pathlib import Path

import pytest

from exohunt.config import ConfigValidationError, resolve_runtime_config


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
                "time_start_btjd = 100.0",
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
    cfg = resolve_runtime_config(
        cli_overrides={
            "preprocess": {"mode": "global"},
        }
    )
    assert cfg.preprocess.mode == "stitched"


def test_resolve_runtime_config_requires_odd_flatten_window_length():
    with pytest.raises(ConfigValidationError, match="flatten_window_length"):
        resolve_runtime_config(
            cli_overrides={
                "preprocess": {"flatten_window_length": 400},
            }
        )
