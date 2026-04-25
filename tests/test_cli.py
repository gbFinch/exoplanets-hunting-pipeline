from __future__ import annotations

import logging
from pathlib import Path

from exohunt import cli


def test_run_command_uses_resolved_config(monkeypatch):
    captured: dict[str, object] = {}

    def _fake_fetch_and_plot(target, config, preset_meta=None, **kwargs):
        captured["target"] = target
        captured["config"] = config
        captured["preset_meta"] = preset_meta
        return None

    monkeypatch.setattr(cli, "fetch_and_plot", _fake_fetch_and_plot)

    rc = cli.main(["run", "--target", "TIC 123", "--config", "quicklook"])

    assert rc == 0
    assert captured["target"] == "TIC 123"
    assert captured["config"].preprocess.mode == "per-sector"
    assert captured["config"].plot.enabled is True
    assert captured["preset_meta"].name == "quicklook"


def test_batch_command_uses_targets_file(monkeypatch, tmp_path: Path):
    targets_file = tmp_path / "targets.txt"
    targets_file.write_text("TIC 1\n# comment\n\nTIC 2\n", encoding="utf-8")

    captured: dict[str, object] = {}

    def _fake_run_batch_analysis(targets, config, preset_meta=None, **kwargs):
        captured["targets"] = targets
        captured["config"] = config
        captured["preset_meta"] = preset_meta
        captured.update(kwargs)
        return None

    monkeypatch.setattr(cli, "run_batch_analysis", _fake_run_batch_analysis)

    rc = cli.main(
        [
            "batch",
            "--targets-file",
            str(targets_file),
            "--config",
            "science-default",
            "--resume",
        ]
    )

    assert rc == 0
    assert captured["targets"] == ["TIC 1", "TIC 2"]
    assert captured["resume"] is True
    assert captured["preset_meta"].name == "science-default"


def test_init_config_command_writes_file(tmp_path: Path):
    out_path = tmp_path / "generated.toml"
    rc = cli.main(["init-config", "--from", "deep-search", "--out", str(out_path)])
    assert rc == 0
    content = out_path.read_text(encoding="utf-8")
    assert 'preset = "deep-search"' in content


def test_legacy_cli_emits_deprecation_and_maps_global(monkeypatch, caplog):
    caplog.set_level(logging.WARNING)
    captured: dict[str, object] = {}

    def _fake_fetch_and_plot(target, config, preset_meta=None, **kwargs):
        captured["target"] = target
        captured["config"] = config
        return None

    monkeypatch.setattr(cli, "fetch_and_plot", _fake_fetch_and_plot)

    rc = cli.main(["--target", "TIC 456", "--preprocess-mode", "global"])

    assert rc == 0
    assert "Deprecated legacy CLI usage detected" in caplog.text
    assert captured["config"].preprocess.mode == "stitched"


def test_legacy_cli_removed_plot_filter_errors_with_actionable_message(tmp_path: Path):
    config_path = tmp_path / "legacy-removed-key.toml"
    config_path.write_text("[plot]\nsectors = [14]\n", encoding="utf-8")

    try:
        cli.main(["run", "--target", "TIC 123", "--config", str(config_path)])
    except RuntimeError as exc:
        message = str(exc)
    else:  # pragma: no cover
        raise AssertionError("Expected RuntimeError for removed plot key")

    assert "plot time-window and sector filters have been removed" in message
