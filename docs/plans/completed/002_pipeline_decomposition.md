# Decompose pipeline.py God Module

## Overview

`pipeline.py` is 2354 lines with 7+ mixed concerns: orchestration, batch processing, manifest generation, metrics I/O, candidate I/O, mode validation, and inline business logic. This plan extracts each concern into a focused module, leaving `pipeline.py` as a thin orchestrator (~300 lines) that wires stages together.

**Prerequisite:** Plan 001 (RuntimeConfig passthrough) should be completed first. This plan assumes `fetch_and_plot()` and all stage functions already accept `RuntimeConfig` instead of 60+ kwargs.

**Files created:** `src/exohunt/batch.py`, `src/exohunt/manifest.py`, `src/exohunt/metrics_io.py`, `src/exohunt/candidates_io.py`, `src/exohunt/known_transit_masking.py`

**Files modified:** `src/exohunt/pipeline.py`, `src/exohunt/cli.py`, test files that import from `pipeline`

## Implementation

### Step 1: Extract batch.py — batch processing, state, status

**File to create:** `src/exohunt/batch.py`

**What to move from pipeline.py:**
- `BatchTargetStatus` dataclass (line 177)
- `_BATCH_STATUS_COLUMNS` list (line 139)
- `_default_batch_state_path()` (line 569)
- `_default_batch_status_path()` (line 575)
- `_load_batch_state()` (line 581)
- `_save_batch_state()` (line 603)
- `_write_batch_status_report()` (line 609)
- `run_batch_analysis()` (line 626)

**Estimated size:** ~245 lines

**Imports needed in batch.py:**
```python
from __future__ import annotations

import csv
import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter

from exohunt.config import RuntimeConfig, PresetMeta
from exohunt.pipeline import fetch_and_plot
from exohunt.progress import _render_progress
```

**Key change:** `run_batch_analysis` currently calls `fetch_and_plot` with 30+ kwargs. After Plan 001, it passes `RuntimeConfig` directly, so the extraction is clean — no circular import since `batch.py` imports from `pipeline.py` (not the reverse).

**Update cli.py imports:**
```python
# Before
from exohunt.pipeline import fetch_and_plot, run_batch_analysis
# After
from exohunt.pipeline import fetch_and_plot
from exohunt.batch import run_batch_analysis
```

**Code snippet for batch.py structure:**
```python
_BATCH_STATUS_COLUMNS = [...]  # move from pipeline.py

@dataclass(frozen=True)
class BatchTargetStatus: ...  # move from pipeline.py

def _default_batch_state_path(...) -> Path: ...
def _default_batch_status_path(...) -> Path: ...
def _load_batch_state(...) -> dict: ...
def _save_batch_state(...) -> None: ...
def _write_batch_status_report(...) -> tuple[Path, Path]: ...

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
) -> tuple[Path, Path, Path]: ...
```

### Step 2: Extract manifest.py — run manifest and reproducibility

**File to create:** `src/exohunt/manifest.py`

**What to move from pipeline.py:**
- `_MANIFEST_INDEX_COLUMNS` list (line 117)
- `_hash_payload()` (line 186)
- `_safe_package_version()` (line 191)
- `_runtime_version_map()` (line 200)
- `_write_manifest_index_row()` (line 213)
- `_write_run_manifest()` (line 223)

**Estimated size:** ~128 lines

**Imports needed:**
```python
from __future__ import annotations

import csv
import hashlib
import json
import platform
import sys
from importlib.metadata import PackageNotFoundError, version as package_version
from pathlib import Path

from exohunt.cache import _safe_target_name, _target_artifact_dir
```

**Code snippet:**
```python
_MANIFEST_INDEX_COLUMNS = [...]  # move from pipeline.py

def _hash_payload(payload: dict[str, object]) -> str: ...
def _safe_package_version(name: str) -> str: ...
def _runtime_version_map() -> dict[str, str]: ...
def _write_manifest_index_row(path: Path, row: dict) -> None: ...
def _write_run_manifest(*, target, run_started_utc, ...) -> tuple[Path, Path, Path]: ...
```

### Step 3: Extract metrics_io.py — preprocessing metrics caching and CSV/JSON output

**File to create:** `src/exohunt/metrics_io.py`

**What to move from pipeline.py:**
- `_PREPROCESSING_METRICS_COLUMNS` list (line 54)
- `_PREPROCESSING_SUMMARY_COLUMNS` list (line 69)
- `_metrics_cache_path()` (line 314)
- `_load_cached_metrics()` (line 351)
- `_save_cached_metrics()` (line 366)
- `_write_preprocessing_metrics()` (line 373)

**Estimated size:** ~111 lines

**Imports needed:**
```python
from __future__ import annotations

import csv
import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from exohunt.cache import _safe_target_name, _target_artifact_dir
```

**Note:** `_metrics_cache_path` currently takes 14 individual params for cache key computation. After Plan 001, simplify to accept `RuntimeConfig` + runtime data:

```python
def _metrics_cache_path(
    target: str,
    cache_dir: Path,
    config: RuntimeConfig,
    raw_n_points: int,
    prepared_n_points: int,
    raw_time_min: float,
    raw_time_max: float,
    prepared_time_min: float,
    prepared_time_max: float,
) -> Path:
    payload = {
        "version": 1,
        "target": target,
        "preprocess_mode": config.preprocess.mode,
        "preprocess_enabled": config.preprocess.enabled,
        "outlier_sigma": round(config.preprocess.outlier_sigma, 6),
        "flatten_window_length": config.preprocess.flatten_window_length,
        "no_flatten": not config.preprocess.flatten,
        "authors": ",".join(config.ingest.authors) if config.ingest.authors else "",
        # ... runtime data params ...
    }
    # ... hash and return path ...
```

### Step 4: Extract candidates_io.py — candidate CSV/JSON writing and live candidates

**File to create:** `src/exohunt/candidates_io.py`

**What to move from pipeline.py:**
- `_CANDIDATE_COLUMNS` list (line 81)
- `_candidate_output_key()` (line 447)
- `_write_bls_candidates()` (line 492)
- `_LIVE_CSV`, `_NOVEL_CSV`, `_LIVE_COLS` (lines 1774–1776)
- `_append_live_candidates()` (line 1779)

**Estimated size:** ~209 lines

**Imports needed:**
```python
from __future__ import annotations

import csv
import hashlib
import json
import logging
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from exohunt.bls import BLSCandidate
from exohunt.cache import _safe_target_name, _target_artifact_dir
from exohunt.parameters import CandidateParameterEstimate
from exohunt.vetting import CandidateVettingResult
```

### Step 5: Extract known_transit_masking.py — batman subtraction + NaN masking

**File to create:** `src/exohunt/known_transit_masking.py`

**What to move from pipeline.py `_search_and_output_stage`:**
- Batman model subtraction logic (lines 1200–1256)
- The inline `batman` import and `TransitParams` construction

**Estimated size:** ~80 lines

**Function signature:**
```python
def mask_known_transits(
    lc_prepared: lk.LightCurve,
    known_ephemerides: list[KnownPlanetEphemeris],
    stellar_params: StellarParams | None = None,
) -> lk.LightCurve:
    """Subtract or mask known planet transits from a light curve.

    For confirmed planets with full orbital parameters (Rp/Rs, a/Rs,
    impact parameter), uses batman Mandel-Agol model subtraction.
    Falls back to NaN masking for TOI candidates or when batman fails.

    Returns a new LightCurve with known transits removed.
    """
```

**Code snippet:**
```python
from __future__ import annotations

import logging

import lightkurve as lk
import numpy as np

from exohunt.ephemeris import KnownPlanetEphemeris
from exohunt.stellar import StellarParams, _SOLAR_U

LOGGER = logging.getLogger(__name__)


def mask_known_transits(
    lc_prepared: lk.LightCurve,
    known_ephemerides: list[KnownPlanetEphemeris],
    stellar_params: StellarParams | None = None,
) -> lk.LightCurve:
    if not known_ephemerides:
        return lc_prepared

    time_arr = np.asarray(lc_prepared.time.value, dtype=float)
    flux_arr = np.asarray(lc_prepared.flux.value, dtype=float).copy()
    n_subtracted = 0
    n_masked = 0
    u1, u2 = _SOLAR_U
    if stellar_params and not stellar_params.used_defaults:
        u1, u2 = stellar_params.limb_darkening

    for eph in known_ephemerides:
        t0_btjd = eph.t0_bjd - 2457000.0
        period = eph.period_days
        if period <= 0:
            continue

        if eph.confirmed and eph.rp_rs and eph.a_rs and eph.impact_param is not None:
            try:
                import batman
                params = batman.TransitParams()
                params.t0 = t0_btjd
                params.per = period
                params.rp = eph.rp_rs
                params.a = eph.a_rs
                params.inc = np.degrees(np.arccos(eph.impact_param / eph.a_rs))
                params.ecc = 0.0
                params.w = 90.0
                params.u = [u1, u2]
                params.limb_dark = "quadratic"
                m = batman.TransitModel(params, time_arr)
                model_flux = m.light_curve(params)
                finite = np.isfinite(flux_arr)
                flux_arr[finite] = flux_arr[finite] / model_flux[finite]
                n_subtracted += 1
                continue
            except Exception as exc:
                LOGGER.warning("Batman failed for %s, falling back to NaN mask: %s", eph.name, exc)

        half_w = 0.5 * (eph.duration_hours / 24.0) * 1.5
        t_min, t_max = float(np.nanmin(time_arr)), float(np.nanmax(time_arr))
        n_start = int(np.floor((t_min - t0_btjd) / period)) - 1
        n_end = int(np.ceil((t_max - t0_btjd) / period)) + 1
        for n in range(n_start, n_end + 1):
            epoch = t0_btjd + n * period
            hit = np.abs(time_arr - epoch) < half_w
            n_masked += int(np.sum(hit & np.isfinite(flux_arr)))
            flux_arr[hit] = np.nan

    LOGGER.info(
        "Pre-masking: %d batman-subtracted, %d NaN-masked cadences, %d planet(s)",
        n_subtracted, n_masked, len(known_ephemerides),
    )
    return lk.LightCurve(time=lc_prepared.time, flux=flux_arr)
```

### Step 6: Move sub-harmonic checking to vetting.py

**File:** `src/exohunt/vetting.py`

**What to implement:** Add a function that checks candidate periods against known planet periods for sub-harmonic relationships. This logic is currently inline in `_search_and_output_stage` (lines 1535–1575).

**Code snippet:**
```python
_SUBHARMONIC_DIVISORS = (2, 3, 4, 5, 6, 7, 8, 9, 10)


def check_known_period_subharmonics(
    candidates: list[BLSCandidate],
    vetting_by_rank: dict[int, CandidateVettingResult],
    known_periods: list[float],
    tolerance: float = 0.03,
) -> dict[int, CandidateVettingResult]:
    """Flag candidates whose period is a sub-harmonic (P_known/N) of a known planet."""
    from dataclasses import replace

    if not known_periods:
        return vetting_by_rank

    updated = dict(vetting_by_rank)
    for c in candidates:
        vr = updated.get(c.rank)
        if not vr or not vr.vetting_pass:
            continue
        for kp in known_periods:
            for n in _SUBHARMONIC_DIVISORS:
                if abs(c.period_days - kp / n) / c.period_days < tolerance:
                    updated[c.rank] = replace(
                        vr,
                        pass_alias_harmonic=False,
                        vetting_pass=False,
                        vetting_reasons=(
                            vr.vetting_reasons.replace("pass", "")
                            + f"toi_subharmonic_1/{n}_of_{kp:.1f}d"
                        ),
                    )
                    break
            else:
                continue
            break
    return updated
```

**Also add a helper for centroid override** (replaces the manual CandidateVettingResult reconstruction in pipeline.py lines 1518–1534):

```python
def override_vetting_for_centroid(
    vetting_by_rank: dict[int, CandidateVettingResult],
    centroid_results: dict[int, "CentroidResult"],
) -> dict[int, CandidateVettingResult]:
    """Override vetting results for candidates that fail centroid checks."""
    from dataclasses import replace

    updated = dict(vetting_by_rank)
    for rank, cr in centroid_results.items():
        vr = updated.get(rank)
        if vr and not cr.passed and cr.status == "fail":
            updated[rank] = replace(
                vr,
                vetting_pass=False,
                vetting_reasons=vr.vetting_reasons + ";centroid_shift",
            )
    return updated
```

### Step 7: Split _ingest_stage into stitched and per-sector paths

**File:** `src/exohunt/pipeline.py`

**What to implement:** Replace the 289-line `_ingest_stage` with a thin dispatcher that calls `_ingest_stitched()` or `_ingest_per_sector()`.

**Code snippet:**
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
    """Ingest light curve data: cache check, download, preprocess."""
    if config.preprocess.mode == "stitched":
        ingest = _ingest_stitched(
            target=target, config=config, cache_dir=cache_dir,
            selected_authors=selected_authors, no_cache=no_cache,
            max_download_files=max_download_files,
        )
    else:
        ingest = _ingest_per_sector(
            target=target, config=config, cache_dir=cache_dir,
            selected_authors=selected_authors, no_cache=no_cache,
            max_download_files=max_download_files,
        )

    # Download one TPF for centroid vetting
    tpf = _download_tpf(target)
    return IngestResult(
        lc=ingest.lc, lc_prepared=ingest.lc_prepared,
        boundaries=ingest.boundaries, data_source=ingest.data_source,
        raw_cache_path=ingest.raw_cache_path,
        prepared_cache_path=ingest.prepared_cache_path,
        prepared_segments_for_bls=ingest.prepared_segments_for_bls,
        raw_segments_for_plot=ingest.raw_segments_for_plot,
        prepared_segments_for_plot=ingest.prepared_segments_for_plot,
        tpf=tpf,
    )
```

Each sub-function (`_ingest_stitched`, `_ingest_per_sector`) contains only its own path — no branching. `_download_tpf` is a small helper (~10 lines) extracted from the TPF download block.

### Step 8: Simplify _search_and_output_stage using extracted modules

**File:** `src/exohunt/pipeline.py`

**What to implement:** Replace inline logic with calls to the extracted modules. The 648-line function becomes a ~150-line orchestrator.

**Before (conceptual):**
```python
def _search_and_output_stage(...):
    # 60 lines: stellar + ephemeris query
    # 57 lines: batman subtraction inline
    # 147 lines: per-sector BLS loop
    # 66 lines: stitched BLS
    # 28 lines: refinement + vetting
    # 36 lines: centroid vetting (manual VettingResult reconstruction)
    # 41 lines: sub-harmonic checking (manual VettingResult reconstruction)
    # 138 lines: candidate output writing
    # 41 lines: TRICERATOPS
    # 3 lines: live candidates
```

**After (conceptual):**
```python
def _search_and_output_stage(*, target, config, lc_prepared, ...):
    stellar_params, known = _query_context(target, config)

    if known:
        lc_prepared = mask_known_transits(lc_prepared, known, stellar_params)

    bls_candidates, vetting = _run_search_and_vet(
        target, config, lc_prepared, prepared_segments_for_bls,
        stellar_params, ...
    )

    if known:
        vetting = check_known_period_subharmonics(
            bls_candidates, vetting, [e.period_days for e in known],
        )

    # Write candidates
    candidate_paths = _write_candidate_outputs(...)

    # TRICERATOPS (opt-in)
    if config.vetting.triceratops_enabled and ...:
        _run_triceratops(...)

    if bls_candidates and vetting:
        _append_live_candidates(target, bls_candidates, vetting, known)

    return SearchResult(...)
```

### Step 9: Move _stitch_segments to ingest.py

**File:** `src/exohunt/ingest.py`

**What to implement:** Move `_stitch_segments()` (22 lines) from pipeline.py to ingest.py, since it's a data preparation utility used only during ingestion.

```python
# Add to ingest.py
def _stitch_segments(lightcurves: list[lk.LightCurve]) -> tuple[lk.LightCurve, list[float]]:
    # ... existing implementation, unchanged ...
```

Update pipeline.py to import it:
```python
from exohunt.ingest import _extract_segments, _parse_authors, _stitch_segments
```

### Step 10: Move stage I/O dataclasses to models.py

**File:** `src/exohunt/models.py`

**What to implement:** Move `IngestResult`, `SearchResult`, and `PlotResult` from pipeline.py to models.py. These are pure data containers that shouldn't live in the orchestrator.

```python
# Add to models.py
@dataclass(frozen=True)
class IngestResult:
    lc: lk.LightCurve
    lc_prepared: lk.LightCurve
    boundaries: list[float]
    data_source: str
    raw_cache_path: Path
    prepared_cache_path: Path
    prepared_segments_for_bls: list[LightCurveSegment]
    raw_segments_for_plot: list[LightCurveSegment]
    prepared_segments_for_plot: list[LightCurveSegment]
    tpf: object | None = None

@dataclass(frozen=True)
class SearchResult:
    bls_candidates: list[BLSCandidate]
    candidate_output_key: str | None
    candidate_csv_paths: list[Path]
    candidate_json_paths: list[Path]
    diagnostic_assets: list[tuple[Path, Path]]
    stitched_vetting_by_rank: dict[int, CandidateVettingResult]

@dataclass(frozen=True)
class PlotResult:
    output_paths: list[Path]
    interactive_paths: list[Path]
```

### Step 11: Update pipeline.py imports and verify

**File:** `src/exohunt/pipeline.py`

**What to implement:** After all extractions, update imports at the top of pipeline.py:

```python
from exohunt.candidates_io import (
    _append_live_candidates,
    _candidate_output_key,
    _write_bls_candidates,
)
from exohunt.known_transit_masking import mask_known_transits
from exohunt.manifest import _write_run_manifest
from exohunt.metrics_io import (
    _load_cached_metrics,
    _metrics_cache_path,
    _save_cached_metrics,
    _write_preprocessing_metrics,
)
from exohunt.models import IngestResult, PlotResult, SearchResult
from exohunt.vetting import (
    CandidateVettingResult,
    check_known_period_subharmonics,
    override_vetting_for_centroid,
    vet_bls_candidates,
)
```

Remove all moved code from pipeline.py. The remaining file should contain:
- `_DEFAULT_CACHE_DIR` constant
- `_ingest_stage()` (dispatcher, ~10 lines)
- `_ingest_stitched()` (~95 lines)
- `_ingest_per_sector()` (~144 lines)
- `_download_tpf()` (~10 lines)
- `_search_and_output_stage()` (~150 lines, orchestration only)
- `_plotting_stage()` (~96 lines, unchanged)
- `_manifest_stage()` (~100 lines, simplified — delegates to manifest.py)
- `fetch_and_plot()` (~80 lines after Plan 001)

**Expected pipeline.py size after decomposition:** ~600 lines (down from 2354).

## Testing

### Unit Tests for batch.py
**File:** `tests/test_batch.py`

**Test cases:**
- `test_load_batch_state_creates_default_when_missing` — verify default state structure
- `test_save_and_load_batch_state_roundtrip` — save then load, verify fields
- `test_write_batch_status_report_creates_csv_and_json` — verify both files written
- `test_run_batch_analysis_calls_fetch_and_plot` — monkeypatch fetch_and_plot, verify called per target

**Code snippet:**
```python
def test_load_batch_state_creates_default_when_missing(tmp_path):
    state = _load_batch_state(tmp_path / "nonexistent.json")
    assert state["schema_version"] == 1
    assert state["completed_targets"] == []

def test_save_and_load_batch_state_roundtrip(tmp_path):
    path = tmp_path / "state.json"
    payload = _load_batch_state(path)
    payload["completed_targets"] = ["TIC 1"]
    _save_batch_state(path, payload)
    reloaded = _load_batch_state(path)
    assert reloaded["completed_targets"] == ["TIC 1"]
```

### Unit Tests for known_transit_masking.py
**File:** `tests/test_known_transit_masking.py`

**Test cases:**
- `test_mask_known_transits_no_ephemerides_returns_unchanged` — empty list returns same LC
- `test_mask_known_transits_nan_masks_toi_candidate` — TOI without batman params gets NaN masked
- `test_mask_known_transits_batman_subtraction_confirmed` — confirmed planet with full params uses batman (mock batman)

**Code snippet:**
```python
def test_mask_known_transits_no_ephemerides_returns_unchanged():
    lc = lk.LightCurve(time=np.arange(100), flux=np.ones(100))
    result = mask_known_transits(lc, [])
    np.testing.assert_array_equal(result.flux.value, lc.flux.value)

def test_mask_known_transits_nan_masks_toi_candidate():
    time = np.linspace(0, 30, 10000)
    flux = np.ones_like(time)
    lc = lk.LightCurve(time=time, flux=flux)
    eph = KnownPlanetEphemeris(
        name="TOI-123.01", period_days=5.0,
        t0_bjd=2457000.0 + 2.5, duration_hours=3.0,
    )
    result = mask_known_transits(lc, [eph])
    assert np.any(np.isnan(result.flux.value))
```

### Unit Tests for vetting additions
**File:** `tests/test_p1_fixes.py` (or new `tests/test_vetting_extensions.py`)

**Test cases:**
- `test_check_known_period_subharmonics_flags_half_period` — P=5d flagged when known P=10d
- `test_check_known_period_subharmonics_ignores_unrelated` — P=7d not flagged when known P=10d
- `test_override_vetting_for_centroid_fails_on_shift` — centroid fail overrides vetting_pass

**Code snippet:**
```python
def test_check_known_period_subharmonics_flags_half_period():
    cand = _make_candidate(rank=1, period_days=5.0)
    vetting = {1: CandidateVettingResult(
        pass_min_transit_count=True, pass_odd_even_depth=True,
        pass_alias_harmonic=True, vetting_pass=True,
        transit_count_observed=10, odd_depth_ppm=100.0,
        even_depth_ppm=100.0, odd_even_depth_mismatch_fraction=0.0,
        alias_harmonic_with_rank=-1, vetting_reasons="pass",
        odd_even_status="pass",
    )}
    result = check_known_period_subharmonics([cand], vetting, [10.0])
    assert not result[1].vetting_pass
    assert "subharmonic" in result[1].vetting_reasons
```

### Existing Test Migration
All existing tests in `test_smoke.py` that call `run_batch_analysis` must update their import:

```python
# Before
from exohunt.pipeline import run_batch_analysis
# After
from exohunt.batch import run_batch_analysis
```

Run full test suite after each step to catch regressions.
