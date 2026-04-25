"""Tests for per-run output isolation (Plan 005)."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from exohunt import pipeline
from exohunt.cli import _sanitize_run_name, _build_run_id
from exohunt.manifest import write_run_readme

from conftest import _test_config


def test_sanitize_run_name():
    assert _sanitize_run_name("my experiment!") == "my_experiment"
    assert _sanitize_run_name("a/b\\c") == "a_b_c"
    assert _sanitize_run_name("___") == ""


def test_build_run_id_format():
    pinned = datetime(2026, 4, 25, 15, 49, 0, tzinfo=timezone.utc)
    rid = _build_run_id(preset_name="iterative-search", run_name=None, now=pinned)
    assert rid == "2026-04-25T15-49-00_iterative-search"

    rid2 = _build_run_id(preset_name="iterative-search", run_name="multi planet study", now=pinned)
    assert rid2 == "2026-04-25T15-49-00_iterative-search_multi_planet_study"

    rid3 = _build_run_id(preset_name=None, run_name=None, now=pinned)
    assert rid3 == "2026-04-25T15-49-00_custom"


def test_resume_loads_existing_state(monkeypatch, tmp_path):
    """Create state file with completed_targets, verify batch skips them."""
    from exohunt.batch import run_batch_analysis

    run_dir = tmp_path / "existing_run"
    run_dir.mkdir()
    state = {
        "schema_version": 1,
        "created_utc": "2026-01-01T00:00:00+00:00",
        "last_updated_utc": "",
        "completed_targets": ["TIC 1", "TIC 3"],
        "failed_targets": [],
        "errors": {},
    }
    (run_dir / "run_state.json").write_text(json.dumps(state), encoding="utf-8")

    calls: list[str] = []

    def _fake_fetch(target, config, run_dir, preset_meta=None, **kwargs):
        calls.append(target)
        return None

    monkeypatch.setattr(pipeline, "fetch_and_plot", _fake_fetch)

    run_batch_analysis(
        targets=["TIC 1", "TIC 2", "TIC 3"],
        config=_test_config(),
        run_dir=run_dir,
        cache_dir=tmp_path / "cache",
    )
    # Only TIC 2 should have been processed
    assert calls == ["TIC 2"]


def test_state_written_inside_run_dir(monkeypatch, tmp_path):
    """Verify run_state.json is in run_dir, not outputs/batch/."""
    from exohunt.batch import run_batch_analysis

    run_dir = tmp_path / "test_run"
    run_dir.mkdir()

    def _fake_fetch(target, config, run_dir, preset_meta=None, **kwargs):
        return None

    monkeypatch.setattr(pipeline, "fetch_and_plot", _fake_fetch)

    run_batch_analysis(
        targets=["TIC 1"],
        config=_test_config(),
        run_dir=run_dir,
        cache_dir=tmp_path / "cache",
    )
    assert (run_dir / "run_state.json").exists()
    assert (run_dir / "run_status.csv").exists()
    assert not (tmp_path / "outputs/batch").exists()


def test_write_run_readme_structure(tmp_path):
    """Verify README contains run name, runtime, preset, target list."""
    from exohunt.config import PresetMeta

    run_dir = tmp_path / "2026-04-25T15-49-00_test"
    run_dir.mkdir()
    preset = PresetMeta(name="test-preset", version=1, hash="abc123")
    config = _test_config()

    path = write_run_readme(
        run_dir, config, preset,
        targets=["TIC 1", "TIC 2"],
        started_utc="2026-04-25T15:49:00+00:00",
        finished_utc="2026-04-25T15:50:00+00:00",
        runtime_seconds=60.0,
        success_count=1, failure_count=1,
        errors={"TIC 2": "simulated failure"},
    )
    assert path.exists()
    content = path.read_text(encoding="utf-8")
    assert "2026-04-25T15-49-00_test" in content
    assert "60.0s" in content
    assert "test-preset" in content
    assert "`TIC 1`" in content
    assert "`TIC 2`" in content
    assert "❌ simulated failure" in content
