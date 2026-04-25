# Per-Run Output Isolation

## Overview

Every exohunt invocation (single-target or batch) becomes an isolated "run" with its own directory under `outputs/runs/<run-id>/`. Only `outputs/cache/` is shared across runs. Global aggregate files (`outputs/manifests/`, `outputs/metrics/`, `outputs/batch/`, `outputs/candidates_*.json`) are removed from the top level — their per-run equivalents live inside each run folder, making runs self-contained.

**Run directory layout:**
```
outputs/runs/2026-04-25T15-49-00_iterative-search/
├── README.md                        ← human-readable run summary (new)
├── run_state.json                   ← batch resumable state (if applicable)
├── run_status.csv                   ← batch per-target status (if applicable)
├── run_status.json                  ← batch status JSON sidecar (if applicable)
├── run_manifest_index.csv           ← index of per-target manifests in this run
├── candidates_live.csv              ← all candidates from this run
├── candidates_novel.csv             ← novel candidates from this run
├── preprocessing_summary.csv        ← metrics rows from this run
├── tic_317597583/
│   ├── candidates/*.{csv,json}
│   ├── diagnostics/*.png
│   ├── manifests/*.json
│   ├── metrics/*.{csv,json}
│   └── plots/*.png
├── tic_xxx/
│   └── ...
```

**Run ID format:**
- Default: `YYYY-MM-DDTHH-MM-SS_<preset>` (e.g. `2026-04-25T15-49-00_iterative-search`)
- With `--run-name <name>`: `YYYY-MM-DDTHH-MM-SS_<preset>_<sanitized-name>` (e.g. `2026-04-25T15-49-00_iterative-search_multi_planet_study`)
- `--run-name` is sanitized: non-alphanumeric chars become `_`, leading/trailing `_` stripped.

**Prerequisites:** Plans 001-004 complete.

**Files modified:**
- `src/exohunt/cache.py` — path helpers now take an explicit `outputs_root`
- `src/exohunt/pipeline.py` — `fetch_and_plot` accepts `run_dir: Path`, threads through stages
- `src/exohunt/batch.py` — `run_batch_analysis` accepts `run_dir`, state/status go under it; resume takes a run path
- `src/exohunt/cli.py` — constructs `run_dir`, adds `--run-name` and new `--resume` arg semantics
- `src/exohunt/manifest.py` — index CSV path becomes per-run; writes `README.md` at run end
- `src/exohunt/metrics_io.py` — aggregate CSV path is per-run
- `src/exohunt/candidates_io.py` — `_LIVE_CSV`/`_NOVEL_CSV` become per-run paths passed in
- `src/exohunt/collect.py`, `src/exohunt/crossmatch.py` — accept `--run-dir` pointing at a run root
- `tests/conftest.py` + test files — tests pass a `tmp_path`-based `run_dir` or helper
- **Pre-migration cleanup step:** delete legacy `outputs/tic_*`, `outputs/manifests/`, `outputs/metrics/`, `outputs/batch/`, `outputs/candidates_*.json`, `outputs/search_run*.log`. Keep `outputs/cache/` untouched.

## Implementation

### Step 1: Add `run_dir` concept to cache.py path helpers

**File:** `src/exohunt/cache.py`

**What to implement:** The existing `_target_output_dir()` and `_target_artifact_dir()` already accept an optional `outputs_root` parameter but it defaults to `Path("outputs")`. Change the default behavior: when `outputs_root` is None, raise an error — we want callers to always pass an explicit run root, so forgetting one fails loudly rather than silently writing to the legacy location.

**Code snippet:**
```python
def _target_output_dir(target: str, outputs_root: Path) -> Path:
    """Return <outputs_root>/<safe_target_name>/. outputs_root is required."""
    return outputs_root / _safe_target_name(target)


def _target_artifact_dir(
    target: str, artifact_name: str, outputs_root: Path
) -> Path:
    return _target_output_dir(target=target, outputs_root=outputs_root) / artifact_name
```

Remove the `Path("outputs")` fallback. Update every caller to pass the run root (see Step 4).

### Step 2: Add run-id construction helper

**File:** `src/exohunt/cli.py` (or new `src/exohunt/runs.py` if it grows)

**What to implement:** A small module-level helper to build a run-id string and the corresponding `Path`.

**Code snippet (inline in cli.py near the other helpers):**
```python
import re
from datetime import datetime, timezone

_RUNS_ROOT = Path("outputs/runs")


def _sanitize_run_name(name: str) -> str:
    """Sanitize user-provided run name to safe filesystem characters."""
    sanitized = re.sub(r"[^A-Za-z0-9_-]+", "_", name).strip("_")
    return sanitized


def _build_run_id(
    *, preset_name: str | None, run_name: str | None,
    now: datetime | None = None,
) -> str:
    """Construct run id as YYYY-MM-DDTHH-MM-SS_<preset>[_<name>]."""
    now = now or datetime.now(tz=timezone.utc)
    ts = now.strftime("%Y-%m-%dT%H-%M-%S")
    parts = [ts]
    parts.append(preset_name or "custom")
    if run_name:
        safe = _sanitize_run_name(run_name)
        if safe:
            parts.append(safe)
    return "_".join(parts)


def _new_run_dir(preset_name: str | None, run_name: str | None) -> Path:
    run_id = _build_run_id(preset_name=preset_name, run_name=run_name)
    run_dir = _RUNS_ROOT / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    return run_dir
```

Using `exist_ok=False` on `mkdir` means two runs started within the same second collide — acceptable edge case; the second will fail with a clear error.

### Step 3: Thread `run_dir` through `fetch_and_plot` and stages

**File:** `src/exohunt/pipeline.py`

**What to implement:** Add `run_dir: Path` parameter to `fetch_and_plot()`. Pass it to every stage. Each stage that currently calls `_target_artifact_dir(target, "plots")` etc. must now pass `outputs_root=run_dir`.

**New signature (after Plan 001):**
```python
def fetch_and_plot(
    target: str,
    config: RuntimeConfig,
    run_dir: Path,
    preset_meta: PresetMeta | None = None,
    *,
    cache_dir: Path | None = None,
    no_cache: bool = False,
    max_download_files: int | None = None,
) -> Path | None:
```

**Internal changes:**
- Pass `run_dir=run_dir` to `_ingest_stage`, `_search_and_output_stage`, `_plotting_stage`, `_manifest_stage`, `_metrics_cache_path` (wait: no, `_metrics_cache_path` lives in metrics_io.py and writes to the cache which is shared — keep as-is).
- Actually, re-examine: the metrics **cache path** writes to `cache_dir / metrics`, so it stays shared (correct). But `_write_preprocessing_metrics` writes to `outputs/metrics/preprocessing_summary.csv` as aggregate AND `outputs/<target>/metrics/...` — both must become run-scoped.

Each stage function gets `run_dir: Path` added to its keyword args (they're already keyword-only after Plan 001's refactor). Internal calls like:
```python
output_dir = _target_artifact_dir(target, "plots")
```
become:
```python
output_dir = _target_artifact_dir(target, "plots", outputs_root=run_dir)
```

### Step 4: Update every caller of `_target_artifact_dir`

**Affected files and sites:**

| File | Line | Change |
|---|---|---|
| `candidates_io.py` | ~106 (in `_write_bls_candidates`) | Add `run_dir` param to function; pass `outputs_root=run_dir` |
| `plotting.py` | ~142, ~254 (`save_raw_vs_prepared_plot`, `save_raw_vs_prepared_plot_interactive`) | Add `run_dir` param; pass through |
| `plotting.py` | ~495 (`save_candidate_diagnostics`) | Add `run_dir` param; pass through |
| `manifest.py` | ~101 (in `_write_run_manifest`) | Accept `run_dir`; use for per-target manifest dir |
| `metrics_io.py` | ~111 (in `_write_preprocessing_metrics`) | Accept `run_dir`; replace `aggregate_output_dir = Path("outputs/metrics")` with `aggregate_output_dir = run_dir` and target dir uses `run_dir` too |
| `pipeline.py` | ~893 (TRICERATOPS val_path) | Use `run_dir` |

All current signatures of public-ish functions in plotting.py, candidates_io.py, manifest.py, metrics_io.py gain a required `run_dir: Path` keyword parameter. Their callers (all inside pipeline.py stages) pass the stage's `run_dir`.

### Step 5: Per-run paths in manifest.py

**File:** `src/exohunt/manifest.py`

**What to implement:**
1. Remove the global `global_index_path = Path("outputs/manifests/run_manifest_index.csv")` write at line ~155.
2. The only "index" write is now per-run at `<run_dir>/run_manifest_index.csv`.
3. Per-target manifest dir stays the same (inside `run_dir/<target>/manifests/`).

**Code snippet (replace the tuple return + both index writes):**
```python
# inside _write_run_manifest
run_index_path = run_dir / "run_manifest_index.csv"
target_index_path = target_manifest_dir / "run_manifest_index.csv"
_write_manifest_index_row(run_index_path, index_row)
_write_manifest_index_row(target_index_path, index_row)
return manifest_path, run_index_path, target_index_path
```

The return signature still returns `(manifest_path, run_index_path, target_index_path)` — semantically the "global" index is now the run-level index. Callers in pipeline.py use these paths in log lines; update log text from "global" → "run".

### Step 6: Per-run live/novel CSVs in candidates_io.py

**File:** `src/exohunt/candidates_io.py`

**What to implement:** Remove module-level `_LIVE_CSV = Path("outputs/batch/...")` constants. Change `_append_live_candidates()` to accept a `run_dir: Path` parameter and compute the paths from it.

**Code snippet:**
```python
# Delete:
# _LIVE_CSV = Path("outputs/batch/candidates_live.csv")
# _NOVEL_CSV = Path("outputs/batch/candidates_novel.csv")

_LIVE_COLS = "target,rank,period_days,depth_ppm,snr,duration_hours,transit_time,iteration,vetting_reasons,vetting_pass"


def _append_live_candidates(
    target: str, candidates: list, vetting: dict, known_ephemerides: list,
    *, run_dir: Path,
) -> None:
    live_csv = run_dir / "candidates_live.csv"
    novel_csv = run_dir / "candidates_novel.csv"
    for csv_path in (live_csv, novel_csv):
        # ... existing try/except create-header logic ...
    # ... rest unchanged, just substitute _LIVE_CSV → live_csv, _NOVEL_CSV → novel_csv ...
```

Update pipeline.py's call site to pass `run_dir=run_dir`.

### Step 7: Per-run metrics aggregate in metrics_io.py

**File:** `src/exohunt/metrics_io.py`

**What to implement:** `_write_preprocessing_metrics` currently writes to `Path("outputs/metrics/preprocessing_summary.csv")` (aggregate) and `<target>/metrics/...` (per-target). Both paths become relative to `run_dir`.

**Code snippet (replace the aggregate_output_dir line and _target_artifact_dir call):**
```python
def _write_preprocessing_metrics(
    target: str, ..., metrics: dict[str, float | int | str],
    *, run_dir: Path,
) -> tuple[Path, Path]:
    aggregate_output_dir = run_dir  # aggregate CSV lives at run root
    aggregate_output_dir.mkdir(parents=True, exist_ok=True)
    target_output_dir = _target_artifact_dir(target, "metrics", outputs_root=run_dir)
    target_output_dir.mkdir(parents=True, exist_ok=True)
    # ... rest unchanged ...
    csv_path = aggregate_output_dir / "preprocessing_summary.csv"
    # ...
    target_csv_path = target_output_dir / "preprocessing_summary.csv"
    # ...
    json_path = target_output_dir / "preprocessing_summary.json"
```

Add `run_dir` to pipeline.py's call to this function.

### Step 8: `batch.py` state/status go inside run_dir; add resume-by-path

**File:** `src/exohunt/batch.py`

**What to implement:**

1. **Remove** `_default_batch_state_path()` and `_default_batch_status_path()` — they hardcoded `outputs/batch/...`. State/status are now always inside `run_dir`.

2. **Change** `run_batch_analysis` signature to accept `run_dir: Path` and compute:
   ```python
   state_path = run_dir / "run_state.json"
   status_path = run_dir / "run_status.csv"
   ```

3. **New resume behavior:** If `resume_from: Path | None` is provided instead of `run_dir`, load state from `resume_from / "run_state.json"` and continue writing into `resume_from`. No new folder is created.

**New signature:**
```python
def run_batch_analysis(
    targets: list[str],
    config: RuntimeConfig,
    run_dir: Path,
    preset_meta: PresetMeta | None = None,
    *,
    no_cache: bool = False,
    cache_dir: Path | None = None,
    max_download_files: int | None = None,
) -> tuple[Path, Path, Path]:
```

Resume doesn't need a separate code path — if `run_dir` is a pre-existing directory, load the state file; otherwise start fresh. The CLI decides whether to create a new run_dir or point at an existing one.

### Step 9: Update CLI to construct `run_dir`, add `--run-name`, change resume semantics

**File:** `src/exohunt/cli.py`

**What to implement:**

1. Add `--run-name` to both `run` and `batch` subparsers.
2. For `run` and `batch` (non-resume case): call `_new_run_dir(preset_meta.name, args.run_name)` to create a new run folder. Pass it to fetch_and_plot / run_batch_analysis.
3. Change `--resume` to take a run directory path: `--resume <path/to/run_dir>`. If given, skip creating a new run_dir and use the one provided. Validate it exists and has `run_state.json`.
4. `--state-path` and `--status-path` flags are removed (obsolete — paths are determined by `run_dir`).

**Code snippets:**

Resolve the `run` command:
```python
def _run_single_target(*, target: str, config_ref: str | None, run_name: str | None) -> None:
    runtime_config, preset_meta = _resolve_runtime(config_ref=config_ref)
    run_dir = _new_run_dir(preset_meta.name, run_name)
    LOGGER.info("Run directory: %s", run_dir)
    fetch_and_plot(
        target=target, config=runtime_config, run_dir=run_dir,
        preset_meta=preset_meta,
    )
    _write_run_readme(run_dir, runtime_config, preset_meta, targets=[target])
```

Resolve the `batch` command:
```python
def _run_batch_targets(
    *, targets_file: Path, config_ref: str | None,
    run_name: str | None, resume_from: Path | None,
    no_cache: bool,
) -> None:
    targets = _load_batch_targets(targets_file)
    if not targets:
        raise RuntimeError(f"No targets found in batch file: {targets_file}")
    runtime_config, preset_meta = _resolve_runtime(config_ref=config_ref)
    if resume_from is not None:
        if not (resume_from / "run_state.json").exists():
            raise RuntimeError(f"Cannot resume: {resume_from}/run_state.json not found")
        run_dir = resume_from
        LOGGER.info("Resuming run directory: %s", run_dir)
    else:
        run_dir = _new_run_dir(preset_meta.name, run_name)
        LOGGER.info("New run directory: %s", run_dir)
    run_batch_analysis(
        targets=targets, config=runtime_config, run_dir=run_dir,
        preset_meta=preset_meta, no_cache=no_cache,
    )
    _write_run_readme(run_dir, runtime_config, preset_meta, targets=targets)
```

CLI parser additions (both `run` and `batch`):
```python
parser.add_argument("--run-name", default=None, help="Optional name appended to run id.")
# For batch only:
batch_parser.add_argument(
    "--resume", type=Path, default=None,
    help="Path to an existing run directory to resume.",
)
```

Remove the legacy `_run_legacy` code path's interactions with `--batch-resume`, `--batch-state-path`, `--batch-status-path` — replace with `--run-name` and `--resume <path>` in the legacy parser too, OR drop the legacy support for those flags entirely. **Recommendation: drop legacy batch-resume/state/status flags** — if a user is using the legacy CLI they can update to `exohunt batch`. Emit a clear deprecation error for the old flags.

### Step 10: Write `README.md` at run end

**File:** `src/exohunt/manifest.py` (or a new small helper in cli.py — either works; manifest.py is the natural home since it's about reproducibility docs)

**What to implement:** A function that writes a human-readable markdown document at `<run_dir>/README.md`. Called once per run, at the end (after all targets are processed).

**Code snippet (in manifest.py):**
```python
def write_run_readme(
    run_dir: Path, config: RuntimeConfig, preset_meta: PresetMeta,
    *, targets: list[str],
    started_utc: str, finished_utc: str, runtime_seconds: float,
    success_count: int, failure_count: int,
    errors: dict[str, str] | None = None,
) -> Path:
    """Write a human-readable README.md describing this run."""
    lines = [
        f"# Run: {run_dir.name}",
        "",
        f"- **Started (UTC):** {started_utc}",
        f"- **Finished (UTC):** {finished_utc}",
        f"- **Runtime:** {runtime_seconds:.1f}s",
        f"- **Preset:** `{preset_meta.name or 'custom'}` "
        f"(version={preset_meta.version}, hash=`{preset_meta.hash}`)"
        if preset_meta.is_set else "- **Preset:** custom (no preset)",
        f"- **Targets:** {len(targets)} "
        f"({success_count} succeeded, {failure_count} failed)",
        "",
        "## Config",
        "",
        f"- `schema_version`: {config.schema_version}",
        f"- `bls.search_method`: `{config.bls.search_method}`",
        f"- `bls.period_range_days`: [{config.bls.period_min_days}, {config.bls.period_max_days}]",
        f"- `bls.iterative_masking`: {config.bls.iterative_masking}",
        f"- `bls.iterative_passes`: {config.bls.iterative_passes}",
        f"- `bls.min_snr`: {config.bls.min_snr}",
        f"- `preprocess.mode`: `{config.preprocess.mode}`",
        f"- `preprocess.flatten_window_length`: {config.preprocess.flatten_window_length}",
        f"- `vetting.triceratops_enabled`: {config.vetting.triceratops_enabled}",
        "",
        "## Targets",
        "",
    ]
    for t in targets:
        err = (errors or {}).get(t)
        status = f"❌ {err}" if err else "✓"
        lines.append(f"- `{t}` — {status}")
    lines.append("")
    lines.append(f"_See `run_manifest_index.csv` for per-run details and "
                 f"`<target>/manifests/*.json` for per-target reproducibility._")
    readme_path = run_dir / "README.md"
    readme_path.write_text("\n".join(lines), encoding="utf-8")
    return readme_path
```

**Integration:**
- For `run` (single target): call at end with `targets=[target]`, `success_count=1 if result else 0`, etc. fetch_and_plot returns a path; use it.
- For `batch`: call inside `run_batch_analysis` at the end, using the `statuses` list it already builds.

The simplest placement: call it from `run_batch_analysis` (which already tracks stats), and from `_run_single_target` in cli.py (one-target case can pass minimal stats).

**Important:** the README write should be best-effort — wrap in try/except so a README write failure doesn't abort the run. Log a warning on failure.

### Step 11: Update collect.py and crossmatch.py to work with a run directory

**File:** `src/exohunt/collect.py`

**What to implement:** Change `--outputs-dir` semantics OR add `--run-dir`. Per the user decision (Q5=b: no cross-run aggregates), `collect.py` operates on a single run directory.

**Change:**
```python
def collect_passed_candidates(
    run_dir: Path,
    iterative_only: bool = False,
    passed_only: bool = True,
) -> dict:
    """Scan target candidate JSONs in a single run directory."""
    results: dict[str, list[dict]] = {}
    for json_path in sorted(run_dir.rglob("candidates/*__bls_*.json")):
        # ... existing logic, but replace "outputs_dir" refs with "run_dir" ...
```

CLI:
```python
parser.add_argument("--run-dir", type=Path, required=True,
                    help="Path to a run directory under outputs/runs/.")
# default summary output:
out_path = args.output or args.run_dir / "candidates_summary.json"
```

**File:** `src/exohunt/crossmatch.py`

Similar: `--summary` now defaults to `<run_dir>/candidates_summary.json`. Or require `--run-dir` and compute summary path from it. Minimal change: keep `--summary` positional; user points at a per-run summary file.

### Step 12: Delete module-level `_DEFAULT_CACHE_DIR` duplicates

**Files:** `src/exohunt/pipeline.py` line 67, `src/exohunt/batch.py` line 17.

Both define the same constant. Keep ONE in `batch.py` (used for default when `cache_dir` is None), or move to `cache.py`. Minimal: keep both since they're both `Path("outputs/cache/lightcurves")` and wouldn't drift — but add a comment noting they're intentional duplicates. Or (cleaner) move to `cache.py` and import. Recommendation: move to `cache.py` as `DEFAULT_CACHE_DIR = Path("outputs/cache/lightcurves")` and import from both.

### Step 13: Pre-migration cleanup script / manual step

**What to implement:** Before the new code runs for the first time, the user must wipe the legacy layout. Provide a one-shot bash script as a migration note in the plan execution (not code in the repo):

```bash
# Executed once during rollout, manually:
rm -rf outputs/tic_* outputs/manifests outputs/metrics outputs/batch
rm -f outputs/candidates_summary.json outputs/candidates_crossmatched.json
rm -f outputs/search_run.log outputs/search_run_resume.log
# Keep outputs/cache/ and outputs/logs/ (logs are developer-facing).
```

This is a one-time, irreversible manual action. Execute AFTER all code is merged and AFTER the test suite passes. Do not automate this in the code — scripts that delete things silently are dangerous.

### Step 14: Update tests for run_dir

**Files:** `tests/conftest.py`, `tests/test_smoke.py`, `tests/test_refactoring.py`, `tests/test_analysis_modules.py`, `tests/test_cli.py`

**What to implement:**
- In `conftest.py`, add a `_test_run_dir(tmp_path)` helper that returns `tmp_path / "run"` (or similar).
- Each test that calls `fetch_and_plot` or `run_batch_analysis` now passes `run_dir=_test_run_dir(tmp_path)`.
- Tests that assert specific output paths must update their expectations to be `run_dir / <target> / ...` rather than `outputs/<target>/...`.
- The monkeypatched fake `fetch_and_plot` in `test_cli.py` accepts `run_dir` in its signature.

**Code snippet for conftest.py:**
```python
import pytest

@pytest.fixture
def test_run_dir(tmp_path):
    run_dir = tmp_path / "run_under_test"
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir
```

And each test accepting `tmp_path` replaces it with `test_run_dir` where `run_dir` is needed.

### Step 15: Integration test on TIC 317597583

After all code changes and the manual cleanup:

```bash
rm -rf outputs/tic_* outputs/manifests outputs/metrics outputs/batch \
       outputs/candidates_*.json outputs/search_run*.log
.venv/bin/python -m pytest tests/ -q --tb=short  # all pass
# Single-target:
caffeinate -i .venv/bin/python -m exohunt.cli run \
  --target "TIC 317597583" --config iterative-search --run-name regression_check
# Verify:
ls outputs/runs/ | head  # should show 2026-04-25T..._iterative-search_regression_check/
cat outputs/runs/*_regression_check/README.md
ls outputs/runs/*_regression_check/tic_317597583/
```

Expected:
- `outputs/runs/<timestamp>_iterative-search_regression_check/README.md` exists and reads cleanly.
- `outputs/runs/<timestamp>_iterative-search_regression_check/tic_317597583/candidates/` contains the candidate CSV/JSON.
- `outputs/runs/<timestamp>_iterative-search_regression_check/tic_317597583/diagnostics/` contains 6 periodogram + phasefold pairs.
- `outputs/runs/<timestamp>_iterative-search_regression_check/candidates_live.csv` contains rows for this run only.
- `outputs/runs/<timestamp>_iterative-search_regression_check/run_manifest_index.csv` has one row.
- `outputs/cache/lightcurves/segments/tic_317597583/` still present (cache reused).

## Testing

### Unit Tests

1. **`tests/test_cli.py::test_build_run_id_format`** — verify `_build_run_id` produces `YYYY-MM-DDTHH-MM-SS_<preset>` and `YYYY-MM-DDTHH-MM-SS_<preset>_<name>` formats. Pin `now` parameter.

2. **`tests/test_cli.py::test_sanitize_run_name`** — verify `_sanitize_run_name("my experiment!")` → `"my_experiment"`, `"a/b\\c"` → `"a_b_c"`, `"___"` → `""`.

3. **`tests/test_batch.py::test_resume_loads_existing_state`** — create a fake state file in `tmp_path`, call `run_batch_analysis(..., run_dir=tmp_path)` with all targets already in `completed_targets`, assert no fetch calls happen.

4. **`tests/test_batch.py::test_state_written_inside_run_dir`** — assert `run_state.json` and `run_status.csv` are written inside `run_dir`, not at `outputs/batch/`.

5. **`tests/test_manifest.py::test_write_run_readme_structure`** — verify the README contains run name, runtime, preset, all targets.

### Regression Tests

All existing 163 tests must pass after updating their call sites. The key shift is: any test that previously relied on `Path("outputs")` as implicit root must now explicitly pass `run_dir`.

### Manual Verification

See Step 15.

## Risk Assessment

**High-risk areas:**
1. **Signature changes cascade across 5+ files.** Plan 001 already did this for config; we're doing it again for run_dir. Apply as one atomic change; don't run tests until all file edits are done.
2. **Legacy cleanup is irreversible.** The user has explicitly approved wiping (context: those runs were produced with the iterative-masking bug). But confirm once more at execution time.
3. **Tests that used to write to `outputs/` now need `tmp_path`.** Any test that didn't properly use `tmp_path` might silently write to the real `outputs/` dir during testing. Grep for `Path("outputs")` in tests before executing the plan to catch these.

**Low-risk areas:**
- Cache stays put. No disk migration.
- Per-target layout inside a run is identical to before. Only the root changes.

## Out of Scope

- **Moving the cache** out of `outputs/` to `~/.cache/exohunt/` or similar — separate concern, user declined.
- **Cross-run aggregate views.** If the user later wants "show me all candidates across all my runs", that's a new feature that scans `outputs/runs/*/candidates_live.csv`. Not this plan.
- **Run pruning / garbage collection.** No automated cleanup of old run directories. User manages manually.
- **Run search/discovery CLI** (`exohunt runs list`). Future feature.

## Open Questions

Per the user's answers, all prior open questions are closed. If any new ambiguity surfaces during implementation (e.g., a test failure that depends on output path semantics), STOP and ASK before guessing.
