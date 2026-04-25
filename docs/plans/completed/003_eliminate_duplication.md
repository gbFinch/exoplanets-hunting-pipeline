# Eliminate Duplication — Shared Utilities, Constants, TIC Parsing

## Overview

The architecture review found 7 categories of duplicated code across the codebase: TIC ID parsing (7+ sites), SHA1 hashing (5 sites in 3 files), limb darkening constants (3 files), NASA TAP URL (2 files), harmonic ratios (2 files with divergent values), mode validation (2 files), and `isinstance(lc_prepared, tuple)` guards (2 sites). This plan consolidates each into a single source of truth.

**Prerequisite:** Plan 001 (RuntimeConfig passthrough) eliminates the mode validation duplication as a side effect. Plan 002 (pipeline decomposition) moves some of the affected code to new modules. This plan can be done in parallel with Plan 002 or after it — the changes are to utility functions, not orchestration.

**Files created:** None (all utilities go into existing modules).

**Files modified:** `src/exohunt/pipeline.py`, `src/exohunt/config.py`, `src/exohunt/cache.py`, `src/exohunt/stellar.py`, `src/exohunt/plotting.py`, `src/exohunt/ephemeris.py`, `src/exohunt/crossmatch.py`, `src/exohunt/vetting.py`, `src/exohunt/models.py`

## Implementation

### Step 1: Add parse_tic_id() to models.py

**File:** `src/exohunt/models.py`

**What to implement:** A single utility to parse TIC IDs from target strings. Currently `int(target.replace("TIC ", "").strip())` appears 7+ times across pipeline.py and crossmatch.py.

**Code snippet:**
```python
def parse_tic_id(target: str) -> int:
    """Extract numeric TIC ID from a target string like 'TIC 261136679'."""
    return int(target.replace("TIC", "").strip())
```

**Call sites to update:**

| File | Line(s) | Current code | New code |
|------|---------|-------------|----------|
| pipeline.py | 1191, 1197 | `int(target.replace("TIC ", "").strip())` | `parse_tic_id(target)` |
| pipeline.py | 1371, 1644, 1672 | `target.replace("TIC ", "").strip()` (str) | `str(parse_tic_id(target))` |
| pipeline.py | 1507, 1728 | `int(target.replace("TIC ", "").strip())` | `parse_tic_id(target)` |
| crossmatch.py | 54 | `int(target.replace("TIC ", "").strip())` | `parse_tic_id(target)` |

Add import to each file:
```python
from exohunt.models import parse_tic_id
```

### Step 2: Add content_hash() to cache.py

**File:** `src/exohunt/cache.py`

**What to implement:** A single hashing utility to replace the 5 duplicated `json.dumps → sha1 → hexdigest` patterns.

**Code snippet:**
```python
def content_hash(payload: dict[str, object], *, length: int = 16) -> str:
    """Compute a stable short hash of a JSON-serializable dict."""
    encoded = json.dumps(payload, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:length]
```

Note: uses SHA-256 instead of SHA-1 (advisory finding from review). Truncation to 16 chars preserves the same collision resistance as the current SHA-1[:16].

**Call sites to update:**

| File | Function | Current code | New code |
|------|----------|-------------|----------|
| pipeline.py | `_hash_payload` | `hashlib.sha1(encoded).hexdigest()[:16]` | Delete function, use `content_hash(payload)` |
| pipeline.py | `_metrics_cache_path` | `hashlib.sha1(encoded).hexdigest()[:16]` | `content_hash(payload)` |
| pipeline.py | `_candidate_output_key` | `hashlib.sha1(encoded).hexdigest()[:12]` | `content_hash(payload, length=12)` |
| config.py | `_stable_hash` | `hashlib.sha1(encoded).hexdigest()[:16]` | `content_hash(payload)` |
| cache.py | `_prepared_cache_key` | `hashlib.sha1(encoded).hexdigest()[:12]` | `content_hash(payload, length=12)` |

After Plan 002, `_hash_payload` and `_metrics_cache_path` will be in `manifest.py` and `metrics_io.py` respectively. Update those files instead.

**Important:** Changing the hash algorithm invalidates existing cache files. Add a migration note in the function docstring:

```python
def content_hash(payload: dict[str, object], *, length: int = 16) -> str:
    """Compute a stable short hash of a JSON-serializable dict.

    Note: Changed from SHA-1 to SHA-256 in v2. Existing cache files
    with SHA-1 keys will be treated as cache misses and recomputed.
    """
```

### Step 3: Export SOLAR_LIMB_DARKENING from stellar.py

**File:** `src/exohunt/stellar.py`

**What to implement:** Rename `_SOLAR_U` to `SOLAR_LIMB_DARKENING` (public constant) so other modules can import it instead of hardcoding `(0.4804, 0.1867)`.

**Code snippet:**
```python
# In stellar.py — rename the existing constant
SOLAR_LIMB_DARKENING: tuple[float, float] = (0.4804, 0.1867)
```

Update the internal reference in stellar.py:
```python
# In _solar_defaults()
limb_darkening=SOLAR_LIMB_DARKENING, used_defaults=True,
```

**Call sites to update:**

| File | Line(s) | Current code | New code |
|------|---------|-------------|----------|
| plotting.py | 392 | `limb_darkening: tuple[float, float] = (0.4804, 0.1867)` | `limb_darkening: tuple[float, float] = SOLAR_LIMB_DARKENING` |
| plotting.py | 576 | `ld_u = (0.4804, 0.1867)` | `ld_u = SOLAR_LIMB_DARKENING` |
| pipeline.py | 1204 | `u1, u2 = (0.4804, 0.1867)` | `u1, u2 = SOLAR_LIMB_DARKENING` |

Add import to plotting.py and pipeline.py (or known_transit_masking.py after Plan 002):
```python
from exohunt.stellar import SOLAR_LIMB_DARKENING
```

### Step 4: Consolidate NASA TAP URL and query utility in ephemeris.py

**File:** `src/exohunt/ephemeris.py` and `src/exohunt/crossmatch.py`

**What to implement:** `_NASA_TAP` and the TAP query pattern are duplicated. Make ephemeris.py the single source and have crossmatch.py import from it.

**In ephemeris.py** — the `_tap_query` function and `_NASA_TAP` constant already exist. Make `_tap_query` importable (it already is — just underscore-prefixed):

```python
# ephemeris.py — no changes needed to the function itself
# Just ensure _NASA_TAP and _tap_query are importable
```

**In crossmatch.py** — remove the duplicated constant and inline query logic:

```python
# Before (crossmatch.py)
_NASA_TAP = "https://exoplanetarchive.ipac.caltech.edu/TAP/sync"

def _query_nasa_archive(tic_id: int) -> list[dict]:
    query = f"select ... from ps where tic_id='TIC {tic_id}' ..."
    url = f"{_NASA_TAP}?query={urllib.parse.quote(query)}&format=json"
    try:
        resp = urllib.request.urlopen(url, timeout=15)
        return json.loads(resp.read())
    except Exception as exc:
        LOGGER.warning(...)
        return []

# After (crossmatch.py)
from exohunt.ephemeris import _tap_query

def _query_nasa_archive(tic_id: int) -> list[dict]:
    query = (
        f"select pl_name,pl_orbper,pl_trandep,pl_rade,tic_id "
        f"from ps where tic_id='TIC {tic_id}' and default_flag=1"
    )
    try:
        return _tap_query(query, timeout=15)
    except Exception as exc:
        LOGGER.warning("NASA archive query failed for TIC %s: %s", tic_id, exc)
        return []
```

Remove `_NASA_TAP` from crossmatch.py. Remove the `import urllib.parse` at the bottom of crossmatch.py (line 165) and the `globals()["urllib.parse"]` hack in `main()`.

### Step 5: Consolidate harmonic ratio constants

**File:** `src/exohunt/vetting.py` and `src/exohunt/crossmatch.py`

**What to implement:** Two different `_HARMONIC_RATIOS` exist with different values and semantics:
- `crossmatch.py`: `(0.5, 2.0, 1/3, 3.0, 2/3, 3/2)` — period ratios for archive matching
- `pipeline.py`: `(2, 3, 4, 5, 6, 7, 8, 9, 10)` — integer divisors for sub-harmonic checking

These are genuinely different concepts. Rename them to be unambiguous.

**In vetting.py** (after Plan 002 moves sub-harmonic checking here):
```python
# Already added in Plan 002, Step 6
_SUBHARMONIC_DIVISORS = (2, 3, 4, 5, 6, 7, 8, 9, 10)
```

**In crossmatch.py** — rename for clarity:
```python
# Before
_HARMONIC_RATIOS = (0.5, 2.0, 1 / 3, 3.0, 2 / 3, 3 / 2)
# After
_CROSSMATCH_PERIOD_RATIOS = (0.5, 2.0, 1 / 3, 3.0, 2 / 3, 3 / 2)
```

Update the reference in `_is_harmonic()`:
```python
def _is_harmonic(period: float, known_period: float) -> str | None:
    for ratio in _CROSSMATCH_PERIOD_RATIOS:
        expected = known_period * ratio
        if abs(period - expected) / expected < _PERIOD_MATCH_FRAC:
            return f"{ratio:.2g}x"
    return None
```

### Step 6: Remove isinstance(lc_prepared, tuple) guards

**File:** `src/exohunt/pipeline.py`

**What to implement:** `prepare_lightcurve()` returns `tuple[LightCurve, bool]` per its type signature. The `isinstance(lc_prepared, tuple)` checks at lines 697 and 756 are defensive guards from when the return type was changed. Since the function always returns a tuple now, always unpack.

**Before (line ~697):**
```python
lc_prepared = prepare_lightcurve(lc, ...)
# Fix: Change 9/10 — Unpack normalization flag (P2)
if isinstance(lc_prepared, tuple):
    lc_prepared, _normalized = lc_prepared
```

**After:**
```python
lc_prepared, _normalized = prepare_lightcurve(lc, ...)
```

Apply the same change at line ~756 (per-sector path):
```python
# Before
prepared_lc = prepare_lightcurve(segment.lc, ...)
if isinstance(prepared_lc, tuple):
    prepared_lc, _seg_normalized = prepared_lc

# After
prepared_lc, _seg_normalized = prepare_lightcurve(segment.lc, ...)
```

### Step 7: Use dataclasses.replace() for CandidateVettingResult overrides

**File:** `src/exohunt/pipeline.py` (or `src/exohunt/vetting.py` after Plan 002)

**What to implement:** Replace the two manual 15-field CandidateVettingResult reconstructions with `dataclasses.replace()`. This is partially addressed by Plan 002 Step 6 (which extracts the logic to vetting.py), but if implementing this plan independently, apply the fix directly.

**Before (pipeline.py line ~1518, centroid override):**
```python
stitched_vetting_by_rank[rank] = CandidateVettingResult(
    pass_min_transit_count=vr.pass_min_transit_count,
    pass_odd_even_depth=vr.pass_odd_even_depth,
    pass_alias_harmonic=vr.pass_alias_harmonic,
    pass_secondary_eclipse=vr.pass_secondary_eclipse,
    pass_depth_consistency=vr.pass_depth_consistency,
    vetting_pass=False,
    transit_count_observed=vr.transit_count_observed,
    odd_depth_ppm=vr.odd_depth_ppm,
    even_depth_ppm=vr.even_depth_ppm,
    odd_even_depth_mismatch_fraction=vr.odd_even_depth_mismatch_fraction,
    secondary_eclipse_depth_fraction=vr.secondary_eclipse_depth_fraction,
    depth_consistency_fraction=vr.depth_consistency_fraction,
    alias_harmonic_with_rank=vr.alias_harmonic_with_rank,
    vetting_reasons=vr.vetting_reasons + ";centroid_shift",
    odd_even_status=vr.odd_even_status,
)
```

**After:**
```python
from dataclasses import replace

stitched_vetting_by_rank[rank] = replace(
    vr,
    vetting_pass=False,
    vetting_reasons=vr.vetting_reasons + ";centroid_shift",
)
```

Apply the same pattern to the sub-harmonic override (line ~1550):
```python
# Before: 15-field manual construction
# After:
stitched_vetting_by_rank[c.rank] = replace(
    vr,
    pass_alias_harmonic=False,
    vetting_pass=False,
    vetting_reasons=(
        vr.vetting_reasons.replace("pass", "")
        + f"toi_subharmonic_1/{n}_of_{kp:.1f}d"
    ),
)
```

## Testing

### Unit Tests for parse_tic_id
**File:** `tests/test_models.py` (new file)

**Test cases:**
- `test_parse_tic_id_standard_format` — `parse_tic_id("TIC 261136679")` returns `261136679`
- `test_parse_tic_id_no_space` — `parse_tic_id("TIC261136679")` returns `261136679`
- `test_parse_tic_id_extra_whitespace` — `parse_tic_id("  TIC  261136679  ")` returns `261136679`
- `test_parse_tic_id_invalid_raises` — `parse_tic_id("not a tic")` raises `ValueError`

**Code snippet:**
```python
import pytest
from exohunt.models import parse_tic_id

def test_parse_tic_id_standard_format():
    assert parse_tic_id("TIC 261136679") == 261136679

def test_parse_tic_id_no_space():
    assert parse_tic_id("TIC261136679") == 261136679

def test_parse_tic_id_extra_whitespace():
    assert parse_tic_id("  TIC  261136679  ") == 261136679

def test_parse_tic_id_invalid_raises():
    with pytest.raises(ValueError):
        parse_tic_id("not a tic")
```

### Unit Tests for content_hash
**File:** `tests/test_cache.py` (new or append to existing)

**Test cases:**
- `test_content_hash_deterministic` — same payload produces same hash
- `test_content_hash_key_order_independent` — `{"a": 1, "b": 2}` == `{"b": 2, "a": 1}`
- `test_content_hash_length_parameter` — `length=12` produces 12-char hash
- `test_content_hash_different_payloads_differ` — different inputs produce different hashes

**Code snippet:**
```python
from exohunt.cache import content_hash

def test_content_hash_deterministic():
    payload = {"key": "value", "n": 42}
    assert content_hash(payload) == content_hash(payload)

def test_content_hash_key_order_independent():
    assert content_hash({"a": 1, "b": 2}) == content_hash({"b": 2, "a": 1})

def test_content_hash_length_parameter():
    h = content_hash({"x": 1}, length=12)
    assert len(h) == 12

def test_content_hash_different_payloads_differ():
    assert content_hash({"a": 1}) != content_hash({"a": 2})
```

### Unit Tests for SOLAR_LIMB_DARKENING import
**File:** `tests/test_stellar.py` (new or append)

**Test case:** Verify the constant is importable and has expected values.

```python
from exohunt.stellar import SOLAR_LIMB_DARKENING

def test_solar_limb_darkening_values():
    assert len(SOLAR_LIMB_DARKENING) == 2
    assert SOLAR_LIMB_DARKENING == (0.4804, 0.1867)
```

### Regression Tests
All existing tests must pass unchanged. The refactoring is purely structural — no behavior changes. Run `pytest tests/` after each step.

The one exception is the SHA-1 → SHA-256 change (Step 2), which changes cache key values. Tests that assert specific hash strings will need updating. Search for hardcoded hash values in tests:

```bash
grep -r "hexdigest\|abc123def\|_hash\|output_key" tests/
```

Update any hardcoded hash expectations to match the new SHA-256 output.
