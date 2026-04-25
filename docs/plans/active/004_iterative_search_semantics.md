# Rework Iterative Search Semantics — Mask-First, Vet-Last

## Overview

`run_iterative_bls_search` has a self-contradictory design that silently hides real signals: it masks the top-power peak only when that peak passes vetting, and aborts the entire iterative loop on the first vetting failure. This defeats the purpose of iterative masking, which exists to reveal weaker signals hiding under a dominant peak — real or systematic.

**Observed regression:** For TIC 317597583, pass 1's top-power peak (P=21.56d) is a systematic that fails odd/even vetting, causing the loop to `break` with zero candidates — even though pass 1's rank 2 is a SDE=18 signal at P=4.57d that should clearly have been reported. Before the refactor, a separate CLI plumbing bug silently disabled iterative masking in batch mode, so this bad design was never exercised and the direct TLS call returned all 5 peaks.

**Design intent of iterative masking** (per docstring and scientific context): mask the dominant signal so weaker signals can be detected in subsequent passes. Whether the dominant signal is a real planet or a systematic is orthogonal to whether it should be masked — both should be masked to uncover what's underneath.

**Proposed fix: decouple masking from vetting.**
- **Mask the top-power peak(s) each pass regardless of vetting** (this is what lets weaker signals emerge)
- **Collect all candidates across all passes**
- **Vet the full accumulated list at the end** (so the caller sees every real peak, whether it was pass 1's top or pass 3's leftovers)

This restores the P=4.57d candidate for TIC 317597583 AND makes the iterative-search preset actually iterative for multi-planet systems.

**Prerequisite:** Plans 001/002/003 are complete.

**Files modified:** `src/exohunt/bls.py` (primary), `tests/test_iterative_bls.py` (update assertions), `src/exohunt/presets/iterative-search.toml` (bump `iterative_top_n` default).

**Files NOT modified:** `pipeline.py`, `cli.py`, `config.py`, other search/vetting modules. The change is localized to `run_iterative_bls_search` and its tests.

## Context and Root-Cause Evidence

**Current logic (`bls.py::run_iterative_bls_search`, pseudocode):**
```python
for iteration in passes:
    candidates = run_search(masked_flux)          # up to top_n candidates
    candidates = filter_degenerate(candidates)     # mask-width sanity
    candidates = candidates[:iterative_top_n]      # truncate to 1 by default
    vetted = vet(candidates)                       # odd/even, alias, etc.
    new = [c for c in candidates if vetted[c].vetting_pass]
    if not new: break                              # ← abort on first vet failure
    accepted_for_masking.extend(new)
    flux[cumulative_mask_from_accepted] = NaN     # ← only mask vet-passed
```

**Failure mode demonstrated:** TIC 317597583 pass 1 returns 5 TLS peaks:

| Rank | Period | Vetting | Real? |
|---|---|---|---|
| 1 | 21.56d | FAIL (odd/even) | No — systematic |
| 2 | 4.57d | **PASS** | Yes — SDE=18 |
| 3 | 24.73d | inconclusive | Maybe |
| 4 | 16.26d | FAIL (depth_inconsistent) | Unclear |
| 5 | 22.86d | FAIL (alias of #2) | No — alias |

With `iterative_top_n=1`, only rank 1 enters vetting. It fails, `break` runs, return empty. **Rank 2's real planet is discarded before it's even considered.**

**Proposed logic:**
```python
for iteration in passes:
    candidates = run_search(masked_flux)
    candidates = filter_degenerate(candidates)
    top_for_masking = candidates[:iterative_top_n]
    top_for_masking = [c for c in top_for_masking
                       if _cross_iteration_unique(c, accepted_for_masking)]
    if not top_for_masking: break                  # nothing new, truly done
    all_candidates.extend(all candidates from this pass, tagged with iteration)
    accepted_for_masking.extend(top_for_masking)
    flux[cumulative_mask_from_accepted_for_masking] = NaN
# Final vetting happens in caller (pipeline already re-vets stitched_vetting_by_rank)
return all_candidates
```

**Key changes:**
1. Mask the top-power peak(s) regardless of vetting.
2. Collect ALL candidates returned by each pass (not just the top `iterative_top_n`), tagged with their iteration number.
3. Remove the in-loop vetting and the `break on vet fail`.
4. Keep the `n_valid < 100` safety guard unchanged.
5. Keep the degenerate-duration filter unchanged (that's a data-sanity filter, not a vetting call).
6. Keep `_cross_iteration_unique` check — it prevents masking the same period twice across passes, which is still valid.

The existing downstream pipeline already vets all `bls_candidates` after `run_iterative_bls_search` returns (in `_search_and_output_stage`). So removing in-loop vetting doesn't lose vetting coverage — it's just moved to the caller, which is where it already runs anyway.

## Implementation

### Step 1: Rewrite the loop body in `run_iterative_bls_search`

**File:** `src/exohunt/bls.py`

**What to implement:** Replace the in-loop vet-before-mask block (currently lines ~437-483 in the file, identifiable by the `from exohunt.vetting import vet_bls_candidates` import and the `if not new_this_iter: break` pattern) with a mask-all-top-power block.

**New loop body (replace the existing body from the `# Take only the top iterative_top_n...` comment through the end of the flux-masking else-branch):**

```python
# Filter degenerate candidates where mask would cover most of the orbit.
# This is a data-sanity check, not vetting — a candidate with duration
# ~= period is not a transit, so masking it would erase the whole LC.
candidates = [
    c for c in candidates
    if (c.duration_hours / 24.0 * config.transit_mask_padding_factor
        / c.period_days) <= 0.5
]

if not candidates:
    break

# Tag all candidates from this pass with the iteration index.
tagged_all = [
    BLSCandidate(
        rank=c.rank, period_days=c.period_days,
        duration_hours=c.duration_hours, depth=c.depth,
        depth_ppm=c.depth_ppm, power=c.power,
        transit_time=c.transit_time,
        transit_count_estimate=c.transit_count_estimate,
        snr=c.snr, fap=c.fap, iteration=iteration,
    )
    for c in candidates
]

# Pick the top-power peaks for masking. Vetting is NOT applied here —
# iterative masking exists to uncover signals hiding under dominant peaks,
# and the dominant peak may be a systematic. Masking it is what lets
# weaker real signals surface in later passes. Final vetting runs in the
# caller after all passes complete.
masking_candidates = [
    c for c in tagged_all[: config.iterative_top_n]
    if _cross_iteration_unique(c, accepted_for_masking)
]
if not masking_candidates:
    # All top peaks were duplicates of already-masked periods; stop.
    break

all_candidates.extend(tagged_all)
accepted_for_masking.extend(masking_candidates)

# Build cumulative mask from everything we've ever masked, and apply.
cumulative_mask = _build_transit_mask(
    time, accepted_for_masking, config.transit_mask_padding_factor,
)

if (
    preprocess_config is not None
    and preprocess_config.iterative_flatten
    and lc is not None
):
    from exohunt.preprocess import prepare_lightcurve

    reflattened, _ = prepare_lightcurve(
        lc, transit_mask=cumulative_mask,
    )
    flux = np.asarray(reflattened.flux.value, dtype=float).copy()
else:
    flux = np.asarray(lc_prepared.flux.value, dtype=float).copy()
    flux[cumulative_mask] = np.nan
```

**Delete:**
- The `from exohunt.vetting import vet_bls_candidates` inline import.
- The `iter_vetting = vet_bls_candidates(...)` call.
- The `for cand in candidates:` loop that appends to `new_this_iter`.
- The `if not new_this_iter: break` line.

**Update docstring:**
```python
"""Run iterative BLS/TLS with transit masking between passes.

Each iteration masks the top-power peak(s) and re-searches, accumulating
all peaks found across passes. Masking is deliberately decoupled from
vetting: a dominant systematic must be masked to reveal weaker real
signals beneath it. Vetting is applied by the caller on the full
returned list.

Stops early when no non-duplicate peaks are found in a pass or when
fewer than 100 valid points remain.
"""
```

**Preserve unchanged:**
- Function signature.
- `time`/`flux` array extraction at the top.
- `n_valid < 100` guard.
- BLS vs TLS dispatch (per `config.search_method`).
- `candidates[: config.iterative_top_n]` logic — still used to pick the top-N for **masking**, just not for filtering the returned set.
- All returned `BLSCandidate` objects tagged with correct iteration index.

### Step 2: Update test expectations in `test_iterative_bls.py`

**File:** `tests/test_iterative_bls.py`

**What to implement:** Three existing tests have assumptions that change under the new semantics. Update them, and add one regression test for the new behavior.

**Test changes:**

**2a. `TestIterativeBLSSearch.test_finds_two_signals` (line ~148)** — previously assumed vetting-pass-or-stop. Under the new design, it should still find two signals because masking-then-research is the exact mechanism it tests. Verify it still passes; if the synthetic data produces clean vet-passing peaks it likely does. No change expected, but confirm.

**2b. `TestIterativeBLSSearch.test_single_pass_matches_baseline` (line ~167)** — compares iterative with `iterative_passes=1` to direct `run_bls_search`. Under the new semantics, iterative with 1 pass returns ALL candidates from that pass (not just the top_n vet-passers). Baseline also returns all peaks. If baseline and iterative both use the same `top_n`, this should still match. Verify, adjust if needed.

**2c. `TestIterativeBLSSearch.test_stops_on_low_snr` (line ~192)** — test name implies the loop stops when SNR is too low. Under the new design, the loop no longer has a vet-based stop — it stops only when (i) pass returns empty, (ii) all top-N are duplicates, or (iii) < 100 valid points. If the test was checking "stops when vetting fails", rename to `test_stops_when_no_candidates_returned` and update to inject a synthetic signal that gets fully masked, leaving subsequent passes empty.

**2d. Add new regression test** (add to `TestIterativeBLSSearch`):

```python
def test_masks_failing_top_peak_to_reveal_deeper_signal(self):
    """Regression: iterative should mask the top-power peak regardless of
    vetting, allowing weaker real signals to surface in later passes.

    This test mirrors the TIC 317597583 scenario: pass 1's rank-1 is a
    spurious peak that fails odd/even vetting, but rank 2 (or a
    weaker-power signal exposed after masking) is a real transit.
    """
    # Build a synthetic LC with a strong odd/even-mismatched signal at P=21d
    # and a cleaner signal at P=4.5d. The 21d signal dominates BLS power
    # but should fail vetting; the 4.5d signal has less power.
    time = np.linspace(0, 90, 20000)
    flux = np.ones_like(time)
    # Strong asymmetric signal (odd transits deeper than even)
    for n in range(int(90 / 21) + 2):
        epoch = 0.3 + n * 21.0
        depth = 0.003 if (n % 2 == 0) else 0.0015
        flux[np.abs(time - epoch) < 0.08] -= depth
    # Cleaner weaker signal
    for n in range(int(90 / 4.5) + 2):
        epoch = 1.1 + n * 4.5
        flux[np.abs(time - epoch) < 0.04] -= 0.0008
    flux += np.random.default_rng(42).normal(0, 0.0005, size=len(time))
    lc = lk.LightCurve(time=time, flux=flux)

    config = _make_bls_config(
        iterative_passes=3,
        iterative_top_n=1,
        top_n=5,
        period_min_days=1.0,
        period_max_days=25.0,
    )
    candidates = run_iterative_bls_search(lc, config)

    # The 4.5d signal must appear in the returned list — either as a
    # rank in pass 1 OR as the dominant peak in pass 2+ after the 21d
    # peak is masked away. Old behavior would return [] because the 21d
    # peak fails odd/even vetting and aborts the loop.
    assert any(
        abs(c.period_days - 4.5) / 4.5 < 0.02 for c in candidates
    ), f"Expected ~4.5d signal in candidates, got {[c.period_days for c in candidates]}"
```

Use whatever synthetic data helpers already exist in the test file (`_synthetic_lc`, `_inject_box_transit`, `_make_bls_config`) — don't invent new ones. Adjust the test to match the existing helpers' conventions.

### Step 3: Update the `iterative-search.toml` preset

**File:** `src/exohunt/presets/iterative-search.toml`

**What to implement:** With the new semantics, `iterative_top_n=1` is reasonable — we mask the dominant peak each pass, which is the canonical iterative workflow. Before, `iterative_top_n=1` was a trap because it also gated the return set. Now that the return set is independent, leave `iterative_top_n = 1` alone.

**However:** document the behavior change in a comment at the top of the preset so users understand the new semantics:

```toml
# Iterative-search preset: mask the top-power peak each pass, re-search,
# accumulate all peaks across passes. Vetting is applied by the caller on
# the full list. Real weak signals hiding under dominant systematics will
# surface in pass 2+ after the systematic is masked.
schema_version = 1
preset = "iterative-search"
...
```

**No behavior change to the preset itself** — only documentation.

### Step 4: Verify downstream vetting is sufficient

**File:** `src/exohunt/pipeline.py`

**What to implement:** Read `_search_and_output_stage` to confirm that `stitched_vetting_by_rank = vet_bls_candidates(...)` is called on the full returned `bls_candidates` list after `run_iterative_bls_search` returns. This IS already the case today — no changes needed, just verify.

Expected call chain:
```
run_iterative_bls_search(...)  →  bls_candidates  (all passes, no vetting)
refine_bls_candidates(...)     →  bls_candidates  (refined, still unvetted)
vet_bls_candidates(...)        →  stitched_vetting_by_rank
```

If this is already the flow (it is, based on current code), no pipeline changes needed. Add a comment to the `run_iterative_bls_search` docstring and/or the caller noting that vetting is the caller's responsibility.

## Testing

### Unit Tests

**File:** `tests/test_iterative_bls.py`

**Changes:**
- Update 2-3 existing tests as described in Step 2.
- Add `test_masks_failing_top_peak_to_reveal_deeper_signal` (regression test for the TIC 317597583 scenario).

**New test cases to add:**

1. **`test_accumulates_candidates_across_passes`** — with 2 injected signals at P=3d and P=7d, with `iterative_passes=2` and `iterative_top_n=1`, assert the returned list has candidates with `iteration=0` AND `iteration=1`.

2. **`test_stops_when_pass_returns_empty`** — with flat noise-free flux, assert the loop stops after pass 1 (no candidates returned by search → `break`).

3. **`test_stops_when_all_top_candidates_are_duplicates`** — inject a single strong signal; with `iterative_passes=3`, assert the loop stops at pass 2 (pass 2's top peak is the same period as pass 1's top, which was already masked, so `_cross_iteration_unique` returns False for all → `break`).

### Integration Test (Manual Verification)

Rerun the TIC 317597583 scenario:

```bash
rm -rf outputs/tic_317597583/
grep -v "317597583" outputs/manifests/run_manifest_index.csv > /tmp/m.csv && mv /tmp/m.csv outputs/manifests/run_manifest_index.csv
# (prune other shared CSVs similarly)
find outputs/cache/lightcurves/metrics -name "tic_317597583*" -delete
caffeinate -i .venv/bin/python -m exohunt.cli run --target "TIC 317597583" --config iterative-search
```

**Expected:** `BLS candidates found: ≥ 1`, including P=4.57d with SDE≈18 and `vetting_pass=True`. The raw cache is already populated from the prior run, so this should take ~15 min.

### Full Test Suite

```bash
.venv/bin/python -m pytest tests/ -q --tb=short
```

All 162 currently-passing tests must still pass after Step 2's test updates. The 3 modified tests should now reflect the new semantics; the 4 new tests (3 unit + 1 regression) should pass.

### Optional: Smoke test against a known multi-planet system

TIC 55525572 (TOI-178, a 6-planet system) is a known multi-transit target. After the fix, running iterative-search on it should return multiple distinct peaks at the known planet periods (1.91d, 3.24d, 6.56d, 9.96d, 15.23d, 20.71d). Do this only if time permits — it's a 1+ hour run.

## Out of Scope

- **No changes to the vetting module.** Vetting itself works correctly; the issue is when vetting was applied.
- **No changes to BLS/TLS search algorithms.** The search code is unchanged.
- **No changes to `refine_bls_candidates`.** Refinement operates on the list returned by iterative and is unaffected.
- **No behavior change for non-iterative paths** (`config.iterative_masking=False` or `iterative_passes=1`). Those paths bypass `run_iterative_bls_search` entirely.
- **No config schema changes.** Existing `BLSConfig` fields remain.

## Risk Assessment

**Low-to-medium risk:**
- The change is localized to one function.
- The caller already re-vets the full result, so removing in-loop vetting doesn't lose vetting coverage.
- The `n_valid < 100` safety guard prevents the "mask all data" failure mode.
- Existing tests cover baseline behavior; updating 2-3 tests plus adding 4 is tractable.

**Concerns to watch:**
- **False positive amplification:** if pass 1's top peak is a systematic, masking it and re-searching *could* surface more spurious peaks as the data gets noisier. Mitigation: the final vetting step in the caller will catch these. The new behavior can only ADD candidates to the output; downstream vetting determines which ones pass.
- **Mask budget exhaustion:** with `iterative_top_n=1` and 3 passes, at most 3 regions get NaN-masked. Each mask is `duration × padding_factor = duration × 1.5`. For a 10-hour max duration, that's 15 hours × 3 = 45 hours of masked data per ~2000 day baseline. Negligible.
- **Test brittleness on synthetic data:** the regression test may be sensitive to noise seeds. Pin the RNG seed (as shown in the snippet) for reproducibility.
