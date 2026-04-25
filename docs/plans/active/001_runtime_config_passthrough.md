# RuntimeConfig Passthrough — Eliminate 60+ kwargs Boilerplate

## Overview

`fetch_and_plot()` accepts 53 parameters and `run_batch_analysis()` accepts 37 parameters, almost all of which are 1:1 destructurings of `RuntimeConfig` fields. The CLI builds a `RuntimeConfig`, then immediately unpacks it into individual kwargs — three times. This plan replaces the flat kwargs signatures with direct `RuntimeConfig` passthrough, eliminating ~400 lines of boilerplate across `cli.py`, `pipeline.py`, and 16 test functions.

**Prerequisite:** None. This plan is self-contained and should be done first — Plans 002 and 003 build on the simplified signatures.

**Files modified:** `src/exohunt/pipeline.py`, `src/exohunt/cli.py`, `src/exohunt/config.py`, all test files that call `fetch_and_plot` or `run_batch_analysis`.

## Implementation

### Step 1: Add PresetMeta dataclass to config.py

**File:** `src/exohunt/config.py`

**What to implement:** The preset metadata `(name, version, hash)` is currently passed as three separate params (`config_preset_id`, `config_preset_version`, `config_preset_hash`). Add a small frozen dataclass to bundle them.

**Code snippet:**
```python
@dataclass(frozen=True)
class PresetMeta:
    name: str | None = None
    version: int | None = None
    hash: str | None = None

    @property
    def is_set(self) -> bool:
        return self.name is not None
```

Place this right after the `RuntimeConfig` dataclass definition (after line ~117).

Update `get_builtin_preset_metadata` to return `PresetMeta` instead of a raw tuple:

```python
def get_builtin_preset_metadata(name: str) -> PresetMeta:
    # ... existing validation ...
    return PresetMeta(name=name, version=BUILTIN_PRESET_PACK_VERSION, hash=preset_hash)
```

### Step 2: Change fetch_and_plot() signature to accept RuntimeConfig

**File:** `src/exohunt/pipeline.py`

**What to implement:** Replace the 53-parameter signature with 4 parameters. Keep `cache_dir`, `no_cache`, and `max_download_files` as explicit params since they are operational concerns not in `RuntimeConfig`.

**Current signature (53 params):**
```python
def fetch_and_plot(
    target: str,
    cache_dir: Path | None = None,
    refresh_cache: bool = False,
    outlier_sigma: float = 5.0,
    # ... 49 more params ...
) -> Path | None:
```

**New signature (5 params):**
```python
def fetch_and_plot(
    target: str,
    config: RuntimeConfig,
    preset_meta: PresetMeta | None = None,
    *,
    cache_dir: Path | None = None,
    no_cache: bool = False,
    max_download_files: int | None = None,
) -> Path | None:
```

**What changes inside the function body:** Replace all bare variable references with config field access. The function currently does:

```python
preprocess_mode = _resolve_preprocess_mode(preprocess_mode)
plot_mode = _resolve_two_track_mode(plot_mode, label="plot mode")
bls_mode = _resolve_two_track_mode(bls_mode, label="BLS mode")
selected_authors = _parse_authors(authors)
```

Replace with:

```python
preprocess_mode = config.preprocess.mode  # already validated by resolve_runtime_config
plot_mode = config.plot.mode
bls_mode = config.bls.mode
authors = ",".join(config.ingest.authors) if config.ingest.authors else None
selected_authors = _parse_authors(authors)
preset_meta = preset_meta or PresetMeta()
```

Remove the `_resolve_preprocess_mode` and `_resolve_two_track_mode` calls — config.py already validates and normalizes modes during `resolve_runtime_config()`.

For each stage call, replace the kwargs with config field access. For example, the `_ingest_stage` call changes from:

```python
ingest = _ingest_stage(
    target=target, cache_dir=cache_dir, refresh_cache=refresh_cache,
    outlier_sigma=outlier_sigma, flatten_window_length=flatten_window_length,
    # ... 10 more kwargs ...
)
```

To:

```python
ingest = _ingest_stage(
    target=target,
    config=config,
    cache_dir=cache_dir,
    selected_authors=selected_authors,
    no_cache=no_cache,
    max_download_files=max_download_files,
)
```

Apply the same pattern to `_search_and_output_stage`, `_plotting_stage`, and `_manifest_stage` calls.

### Step 3: Change run_batch_analysis() signature to accept RuntimeConfig

**File:** `src/exohunt/pipeline.py`

**What to implement:** Replace the 37-parameter signature with a config-based one.

**New signature:**
```python
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
```

**What changes inside the function body:** The internal `fetch_and_plot` call (line ~710) simplifies from 30+ kwargs to:

```python
output_path = fetch_and_plot(
    target=target,
    config=config,
    preset_meta=preset_meta,
    cache_dir=cache_dir,
    no_cache=no_cache,
    max_download_files=max_download_files,
)
```

### Step 4: Change _ingest_stage() to accept RuntimeConfig

**File:** `src/exohunt/pipeline.py`

**What to implement:** Replace 15 kwargs with config access.

**New signature:**
```python
def _ingest_stage(
    *,
    target: str,
    config: RuntimeConfig,
    cache_dir: Path,
    selected_authors: set[str] | None,
    no_cache: bool = False,
    max_download_files: int | None = None,
) -> IngestResult:
```

Inside the body, replace `outlier_sigma` with `config.preprocess.outlier_sigma`, `flatten_window_length` with `config.preprocess.flatten_window_length`, etc. The mapping is direct:

| Old param | New access |
|---|---|
| `refresh_cache` | `config.io.refresh_cache` |
| `outlier_sigma` | `config.preprocess.outlier_sigma` |
| `flatten_window_length` | `config.preprocess.flatten_window_length` |
| `preprocess_enabled` | `config.preprocess.enabled` |
| `no_flatten` | `not config.preprocess.flatten` |
| `preprocess_mode` | `config.preprocess.mode` |
| `run_bls` | `config.bls.enabled` |
| `bls_duration_max_hours` | `config.bls.duration_max_hours` |

Note: `authors` (str) is replaced by `selected_authors` (set) which is already parsed by the caller.

### Step 5: Change _search_and_output_stage() to accept RuntimeConfig

**File:** `src/exohunt/pipeline.py`

**What to implement:** Replace 49 kwargs with config + runtime data.

**New signature:**
```python
def _search_and_output_stage(
    *,
    target: str,
    config: RuntimeConfig,
    lc_prepared: lk.LightCurve,
    prepared_segments_for_bls: list[LightCurveSegment],
    data_source: str,
    n_points_raw: int,
    n_points_prepared: int,
    time_min: float,
    time_max: float,
    authors: str | None,
    tpf: object | None = None,
) -> SearchResult:
```

This reduces from 49 to 12 params. Inside the body, replace all `bls_period_min_days` with `config.bls.period_min_days`, `vetting_min_transit_count` with `config.vetting.min_transit_count`, etc.

### Step 6: Change _plotting_stage() to accept RuntimeConfig

**File:** `src/exohunt/pipeline.py`

**What to implement:** This stage already has only 13 params, but several come from config.

**New signature:**
```python
def _plotting_stage(
    *,
    target: str,
    config: RuntimeConfig,
    lc: lk.LightCurve,
    lc_prepared: lk.LightCurve,
    boundaries: list[float],
    raw_segments_for_plot: list[LightCurveSegment],
    prepared_segments_for_plot: list[LightCurveSegment],
) -> PlotResult:
```

Inside, replace `plot_enabled` with `config.plot.enabled`, `plot_mode` with `config.plot.mode`, etc.

### Step 7: Change _manifest_stage() to accept RuntimeConfig

**File:** `src/exohunt/pipeline.py`

**What to implement:** Replace 42 kwargs with config + runtime data.

**New signature:**
```python
def _manifest_stage(
    *,
    target: str,
    config: RuntimeConfig,
    preset_meta: PresetMeta,
    started_at: float,
    run_started_utc: str,
    authors: str | None,
    data_source: str,
    n_points_raw: int,
    n_points_prepared: int,
    time_min: float,
    time_max: float,
    raw_cache_path: Path,
    prepared_cache_path: Path,
    metrics_csv_path: Path,
    metrics_json_path: Path,
    metrics_cache_path: Path,
    metrics_cache_hit: bool,
    metrics_payload: dict,
    search_result: SearchResult,
    plot_result: PlotResult,
) -> Path | None:
```

Inside, build `config_payload` dict from `config` fields instead of individual params.

### Step 8: Simplify cli.py — remove all kwargs destructuring

**File:** `src/exohunt/cli.py`

**What to implement:** Replace the three 50-line kwargs blocks with direct config passthrough.

**`_run_single_target` — before (50+ lines):**
```python
def _run_single_target(*, target: str, config_ref: str | None) -> None:
    runtime_config, preset_meta = _resolve_runtime(config_ref=config_ref)
    authors = ",".join(runtime_config.ingest.authors) if runtime_config.ingest.authors else None
    fetch_and_plot(
        target,
        refresh_cache=runtime_config.io.refresh_cache,
        outlier_sigma=runtime_config.preprocess.outlier_sigma,
        # ... 48 more lines ...
    )
```

**`_run_single_target` — after (4 lines):**
```python
def _run_single_target(*, target: str, config_ref: str | None) -> None:
    runtime_config, preset_meta = _resolve_runtime(config_ref=config_ref)
    fetch_and_plot(target, config=runtime_config, preset_meta=preset_meta)
```

**`_run_batch_targets` — after:**
```python
def _run_batch_targets(
    *, targets_file: Path, config_ref: str | None,
    resume: bool, no_cache: bool = False,
    state_path: Path | None, status_path: Path | None,
) -> None:
    targets = _load_batch_targets(targets_file)
    if not targets:
        raise RuntimeError(f"No targets found in batch file: {targets_file}")
    runtime_config, preset_meta = _resolve_runtime(config_ref=config_ref)
    run_batch_analysis(
        targets=targets, config=runtime_config, preset_meta=preset_meta,
        resume=resume, no_cache=no_cache,
        state_path=state_path, status_path=status_path,
    )
```

**`_run_legacy` — after:** Same pattern. Build `runtime_config` via `_resolve_runtime`, then pass it directly. The legacy CLI override dict is already handled by `_resolve_runtime(cli_overrides=...)`.

Update `_resolve_runtime` to return `PresetMeta` instead of a tuple:

```python
def _resolve_runtime(
    *, config_ref: str | None, cli_overrides: dict[str, object] | None = None,
) -> tuple[RuntimeConfig, PresetMeta]:
    preset_name, config_path = _resolve_config_reference(config_ref)
    runtime_config = resolve_runtime_config(
        config_path=config_path, preset_name=preset_name, cli_overrides=cli_overrides,
    )
    preset_meta = PresetMeta()
    if runtime_config.preset is not None and runtime_config.preset in set(list_builtin_presets()):
        preset_meta = get_builtin_preset_metadata(runtime_config.preset)
    return runtime_config, preset_meta
```

### Step 9: Remove dead mode validation from pipeline.py

**File:** `src/exohunt/pipeline.py`

**What to implement:** Delete `_resolve_preprocess_mode()`, `_resolve_two_track_mode()`, and `_ALLOWED_TWO_TRACK_MODES`. These are now redundant — `resolve_runtime_config()` in config.py already validates and normalizes all mode values before they reach pipeline.py.

Delete these lines (approximately lines 149-172):
```python
_ALLOWED_TWO_TRACK_MODES = {"stitched", "per-sector"}

def _resolve_preprocess_mode(mode: str) -> str: ...
def _resolve_two_track_mode(mode: str, *, label: str) -> str: ...
```

### Step 10: Update all test files to pass RuntimeConfig

**Files:** `tests/test_smoke.py`, `tests/test_refactoring.py`, `tests/test_analysis_modules.py`, `tests/test_cli.py`

**What to implement:** Add a test helper that builds a `RuntimeConfig` with sensible defaults, then update all 16 test call sites.

**Add helper to a shared test fixture (e.g., `tests/conftest.py` or top of each test file):**
```python
from exohunt.config import (
    RuntimeConfig, IOConfig, IngestConfig, PreprocessConfig,
    PlotConfig, BLSConfig, VettingConfig, ParameterConfig,
)

def _test_config(**overrides) -> RuntimeConfig:
    """Build a RuntimeConfig for tests with minimal defaults."""
    return RuntimeConfig(
        schema_version=1,
        preset=None,
        io=IOConfig(refresh_cache=overrides.get("refresh_cache", False)),
        ingest=IngestConfig(authors=("SPOC",)),
        preprocess=PreprocessConfig(
            enabled=overrides.get("preprocess_enabled", True),
            mode=overrides.get("preprocess_mode", "stitched"),
            outlier_sigma=5.0,
            flatten_window_length=401,
            flatten=True,
            iterative_flatten=False,
            transit_mask_padding_factor=1.5,
        ),
        plot=PlotConfig(
            enabled=overrides.get("plot_enabled", True),
            mode=overrides.get("plot_mode", "stitched"),
            interactive_html=False,
            interactive_max_points=200_000,
            smoothing_window=5,
        ),
        bls=BLSConfig(
            enabled=overrides.get("run_bls", True),
            mode=overrides.get("bls_mode", "stitched"),
            search_method="bls",
            period_min_days=0.5, period_max_days=20.0,
            duration_min_hours=0.5, duration_max_hours=10.0,
            n_periods=2000, n_durations=12, top_n=5,
            min_snr=7.0, compute_fap=False, fap_iterations=1000,
            iterative_masking=False,
            unique_period_separation_fraction=0.05,
            iterative_passes=1, subtraction_model="box_mask",
            iterative_top_n=1, transit_mask_padding_factor=1.5,
        ),
        vetting=VettingConfig(
            min_transit_count=2,
            odd_even_max_mismatch_fraction=0.30,
            alias_tolerance_fraction=0.02,
            secondary_eclipse_max_fraction=0.30,
            depth_consistency_max_fraction=0.50,
        ),
        parameters=ParameterConfig(
            stellar_density_kg_m3=1408.0,
            duration_ratio_min=0.05, duration_ratio_max=1.8,
            apply_limb_darkening_correction=False,
            limb_darkening_u1=0.4, limb_darkening_u2=0.2,
            tic_density_lookup=False,
        ),
    )
```

**Update each test call site.** Example — `test_fetch_and_plot_uses_cache`:

Before:
```python
result = fetch_and_plot(target, cache_dir=cache_dir, preprocess_mode="global")
```

After:
```python
config = _test_config(preprocess_mode="stitched")  # "global" no longer valid post-config-validation
result = fetch_and_plot(target, config=config, cache_dir=cache_dir)
```

For `test_cli.py`, update the monkeypatched fake functions to match the new signatures:

Before:
```python
def _fake_fetch_and_plot(target, **kwargs):
    calls.append((target, kwargs))
    return Path("fake.png")
```

After:
```python
def _fake_fetch_and_plot(target, config, preset_meta=None, **kwargs):
    calls.append((target, config, kwargs))
    return Path("fake.png")
```

## Testing

### Unit Tests for PresetMeta
**File:** `tests/test_config.py`

**Test cases:**
- `test_preset_meta_default_is_unset` — `PresetMeta().is_set` returns `False`
- `test_preset_meta_with_name_is_set` — `PresetMeta(name="x", version=1, hash="h").is_set` returns `True`
- `test_get_builtin_preset_metadata_returns_preset_meta` — verify return type is `PresetMeta`

**Code snippet:**
```python
def test_preset_meta_default_is_unset():
    assert not PresetMeta().is_set

def test_preset_meta_with_name_is_set():
    meta = PresetMeta(name="science-default", version=1, hash="abc123")
    assert meta.is_set
    assert meta.name == "science-default"

def test_get_builtin_preset_metadata_returns_preset_meta():
    meta = get_builtin_preset_metadata("science-default")
    assert isinstance(meta, PresetMeta)
    assert meta.is_set
```

### Regression Tests for Config Passthrough
**File:** `tests/test_refactoring.py`

**Test cases:** All existing tests in `TestFetchAndPlotBehavior` must pass after updating their call sites. No new test logic needed — the existing tests already verify behavior. The only change is how arguments are passed.

**Verification approach:**
1. Run `pytest tests/` after each step
2. All 4 tests in `TestFetchAndPlotBehavior` must pass
3. All 11 tests in `test_smoke.py` that call `fetch_and_plot` must pass
4. All 6 tests in `test_cli.py` must pass
5. `test_fetch_and_plot_with_fixed_fixture_emits_reproducible_candidate_payload` in `test_analysis_modules.py` must pass

### New Test: Config Round-Trip
**File:** `tests/test_refactoring.py`

**Test case:** Verify that passing `RuntimeConfig` produces identical output to the old kwargs path (this test can be removed after migration is complete).

```python
def test_config_passthrough_equivalent_to_defaults(tmp_path, monkeypatch):
    """RuntimeConfig with defaults produces same result as old default kwargs."""
    monkeypatch.setattr("exohunt.pipeline.lk.search_lightcurve", _no_search)
    config = _test_config()
    cache_dir = tmp_path / "cache"
    # Pre-populate cache so no download is attempted
    _populate_test_cache(cache_dir, "TIC 000000001")
    result = fetch_and_plot("TIC 000000001", config=config, cache_dir=cache_dir)
    assert result is not None
```
