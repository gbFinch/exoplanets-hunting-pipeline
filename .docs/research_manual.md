# Exohunt Research Manual

Step-by-step guide for running a systematic planet search with Exohunt.

---

## Overview

The workflow has 4 phases:

1. **Configure** — choose targets and search parameters
2. **Run** — batch analysis (hours to days)
3. **Monitor** — watch live candidate CSVs during the run
4. **Analyze** — review novel candidates, cross-match, validate

---

## Phase 1: Configure

### Choose a target list

Pre-built target lists are in `.docs/`. Start small, expand later:

| File | Targets | Est. runtime |
|------|:-------:|:------------:|
| `.docs/targets_premium.txt` | ~200 | ~40 hours |
| `.docs/targets_standard.txt` | ~1,100 | ~9 days |
| `.docs/targets_extended.txt` | ~1,900 | ~16 days |
| `.docs/targets_iterative_search.txt` | ~3,200 | ~27 days |

Or create your own (one TIC ID per line, `#` comments allowed):

```text
# My custom targets
TIC 261136679
TIC 355867695
```

### Choose a search config

Use the built-in `iterative-search` preset — it's configured for systematic planet hunting:

```
iterative-search:
  TLS search with stellar parameters from TIC
  Iterative masking (3 passes) to find multiple planets
  Batman subtraction of known confirmed planets
  NaN masking of TOI candidates
  Period range 0.5–25 days
  TRICERATOPS disabled (run separately on candidates)
  ~12 min per target (varies with sector count)
```

No config file needed — just pass the preset name directly:

```bash
python -m exohunt.cli batch \
  --targets-file .docs/targets_premium.txt \
  --config iterative-search \
  --run-name premium_search \
  --no-cache
```

`--run-name` is optional but recommended — it appends a label to the auto-generated run id (timestamp + preset), making the run directory easier to identify later.

To customize, export and edit:

```bash
python -m exohunt.cli init-config --from iterative-search --out ./configs/my_search.toml
# Edit configs/my_search.toml, then:
python -m exohunt.cli batch \
  --targets-file targets.txt \
  --config ./configs/my_search.toml \
  --run-name my_search
```

### What the pipeline does for each target

1. Downloads TESS SPOC light curves from MAST
2. Preprocesses per-sector (outlier removal, flattening)
3. Stitches sectors into one light curve
4. Queries NASA archive for known planets → batman model subtraction (confirmed) or NaN masking (TOI candidates)
5. Runs TLS transit search on the residual
6. If iterative masking is on: masks found signal, repeats TLS
7. Vets each candidate (odd/even, alias, secondary eclipse, depth, centroid, TOI sub-harmonic)
8. Writes candidates, diagnostics, plots, manifests
9. Appends to live summary CSVs

---

## Phase 2: Run

### Start the batch

```bash
python -m exohunt.cli batch \
  --targets-file .docs/targets_premium.txt \
  --config iterative-search \
  --run-name premium_search \
  --no-cache \
  > outputs/logs/search_run.log 2>&1 &
```

- `--run-name premium_search` — labels the run directory (e.g.
  `outputs/runs/2026-04-25T17-00-00_iterative-search_premium_search/`).
  Optional; the timestamp+preset alone is already unique.
- `--no-cache` — disables writing light curve cache files to save disk space.
  Raw LC data will still be downloaded; it just won't be persisted.
- `> outputs/logs/search_run.log 2>&1 &` — runs in background, logs to file.

On macOS, prevent sleep:

```bash
mkdir -p outputs/logs
nohup caffeinate -dims python -m exohunt.cli batch \
  --targets-file .docs/targets_premium.txt \
  --config iterative-search \
  --run-name premium_search \
  --no-cache \
  > outputs/logs/search_run.log 2>&1 &
echo "PID: $!"
```

### Resume an interrupted batch

If a batch was interrupted, resume by pointing `--resume` at the existing
run directory. The run's `run_state.json` tracks completed targets, so they
are skipped on resume:

```bash
python -m exohunt.cli batch \
  --targets-file .docs/targets_premium.txt \
  --config iterative-search \
  --resume outputs/runs/2026-04-25T17-00-00_iterative-search_premium_search \
  > outputs/logs/search_run_resume.log 2>&1 &
```

The `--config` and `--targets-file` must match the original run for
resume to be meaningful.

### Expand to more targets

Running a larger tier is a **new run**, not a resume — it creates its own
run directory. Completed targets from prior runs are NOT automatically
skipped across runs; the cache (`outputs/cache/`) handles reuse of
downloaded light curves:

```bash
python -m exohunt.cli batch \
  --targets-file .docs/targets_iterative_search.txt \
  --config iterative-search \
  --run-name iterative_full \
  > outputs/logs/search_run_full.log 2>&1 &
```

---

## Phase 3: Monitor

All artifacts for a run live under `outputs/runs/<run-id>/`. Set a shell
variable to save typing:

```bash
RUN=outputs/runs/2026-04-25T17-00-00_iterative-search_premium_search
```

### Live candidate files

During the run, two CSVs are updated in real-time inside the run directory:

| File | Contents |
|------|----------|
| `$RUN/candidates_live.csv` | All candidates from all targets (passing and failing) |
| `$RUN/candidates_novel.csv` | **Only passing candidates that don't match any known planet or TOI** |

The novel CSV is the one you want to watch:

```bash
# Watch for new novel candidates
tail -f "$RUN/candidates_novel.csv"

# Or grep the log for the 📡 marker
grep "📡" outputs/logs/search_run.log
```

### Batch status

```bash
# Quick status check
cat "$RUN/run_status.csv"

# How many targets succeeded
grep -c "success" "$RUN/run_status.csv"

# Any failures
grep "failed" "$RUN/run_status.csv"
```

### Run summary

Each run writes a human-readable `README.md` at the end:

```bash
cat "$RUN/README.md"
```

It lists start/end timestamps, runtime, preset, and a per-target pass/fail
summary.

### Log monitoring

```bash
# Last target being processed
grep "Target:" outputs/logs/search_run.log | tail -1

# Pre-masking activity
grep "Pre-masking:" outputs/logs/search_run.log | tail -5

# BLS completion times
grep "BLS complete" outputs/logs/search_run.log | tail -5
```

---

## Phase 4: Analyze

Analysis operates on a single run directory. Export it once:

```bash
RUN=outputs/runs/2026-04-25T17-00-00_iterative-search_premium_search
```

### Step 1: Review novel candidates

```bash
# View novel candidates sorted by SDE
cat "$RUN/candidates_novel.csv" | sort -t, -k5 -rn
```

Columns: `target, rank, period_days, depth_ppm, snr, duration_hours, transit_time, iteration, vetting_reasons, vetting_pass`

Key fields to check:
- `snr` — higher is more significant (>10 is strong, 7-10 is marginal)
- `depth_ppm` — transit depth; <100 ppm is very shallow, >1000 ppm is deep
- `iteration` — 0 = found in first pass, 1+ = found after masking prior signals
- `vetting_reasons` — why it passed (e.g., `odd_even_inconclusive` means too few transits to test)

### Step 2: Collect all candidates

```bash
python -m exohunt.collect --run-dir "$RUN"
```

Produces `$RUN/candidates_summary.json` with all vetted candidates across every
target in that run.

Options:
```bash
python -m exohunt.collect --run-dir "$RUN" --iterative-only   # only candidates from iteration >= 1
python -m exohunt.collect --run-dir "$RUN" --all              # include failed vetting too
```

### Step 3: Cross-match against NASA archive

```bash
python -m exohunt.crossmatch "$RUN/candidates_summary.json"
```

Labels each candidate as:
- **KNOWN** — matches a confirmed exoplanet period
- **HARMONIC** — matches a harmonic (0.5×, 2×, 3×) of a known planet
- **NEW** — no match found (worth manual review)

Results: `$RUN/candidates_crossmatched.json`

### Step 4: Manual review of NEW candidates

For each NEW candidate, check:

1. **Diagnostics plots** — `$RUN/<target>/diagnostics/`
   - Periodogram: is the peak clean or surrounded by aliases?
   - Phase-folded light curve: does it look like a transit?

2. **Duration plausibility** — is the transit duration consistent with the period and stellar radius?

3. **Depth consistency** — is the depth consistent across sectors?

4. **ExoFOP check** — search the target on [ExoFOP](https://exofop.ipac.caltech.edu/tess/) for community notes, dispositions, or ground-based follow-up

5. **Centroid check** — centroid pass/fail is reported in the log and embedded in the candidate JSON's `vetting_reasons` (look for `centroid_shift` flag)

### Step 5: TRICERATOPS validation (for promising candidates)

For candidates that survive manual review, run TRICERATOPS on a single target.
This creates a separate run directory:

```bash
python -m exohunt.cli run \
  --target "TIC 123456789" \
  --config ./configs/validate.toml \
  --run-name triceratops_validate
```

With `configs/validate.toml`:
```toml
schema_version = 1
preset = "deep-search"

[bls]
search_method = "tls"
iterative_masking = true
iterative_passes = 3
period_max_days = 25.0

[parameters]
tic_density_lookup = true

[vetting]
triceratops_enabled = true
triceratops_n = 1000000
```

TRICERATOPS thresholds (Giacalone & Dressing 2020):
- FPP < 0.015 and NFPP < 0.001 → **statistically validated planet**
- FPP < 0.5 → ambiguous, needs more data
- FPP > 0.5 → likely false positive

---

## Quick Reference

### Useful commands

```bash
# Single target quick look
python -m exohunt.cli run --target "TIC 261136679" --config quicklook

# Single target full analysis with a label
python -m exohunt.cli run --target "TIC 261136679" --config deep-search --run-name deep_look

# Batch
python -m exohunt.cli batch \
  --targets-file targets.txt --config my_search.toml --run-name q2_sweep

# Resume an interrupted batch (pass the run directory)
python -m exohunt.cli batch \
  --targets-file targets.txt --config my_search.toml \
  --resume outputs/runs/2026-04-25T17-00-00_my_search_q2_sweep

# Collect results from a specific run
python -m exohunt.collect --run-dir outputs/runs/<run-id>

# Cross-match (needs the summary JSON produced by collect)
python -m exohunt.crossmatch outputs/runs/<run-id>/candidates_summary.json

# Find the most recent run
ls -t outputs/runs | head -1

# Clean light curve cache (reclaim disk space; cache is rebuilt from MAST on next run)
rm -rf outputs/cache/lightcurves

# Remove an old run (destructive — artifacts gone)
rm -rf outputs/runs/<run-id>
```

### Output structure

```
outputs/
  cache/                                    ← shared across all runs (reusable)
    lightcurves/
      <target>.npz                          ← stitched raw light curve
      segments/<target>/                    ← per-sector raw + prepared caches
      metrics/<target>__metrics_*.json      ← preprocessing metrics cache
  logs/                                     ← shell-redirected stdout/stderr logs
  runs/
    2026-04-25T17-00-00_iterative-search_premium_search/   ← one run
      README.md                             ← human-readable run summary
      run_state.json                        ← resumable batch state
      run_status.csv                        ← per-target status (success/failed)
      run_status.json                       ← status sidecar
      run_manifest_index.csv                ← index of per-target manifests
      candidates_live.csv                   ← all candidates from this run
      candidates_novel.csv                  ← novel candidates from this run
      preprocessing_summary.csv             ← per-target preprocessing metrics
      candidates_summary.json               ← written by `exohunt.collect`
      candidates_crossmatched.json          ← written by `exohunt.crossmatch`
      <target>/
        candidates/              ← candidate JSON/CSV for this run
        diagnostics/             ← periodograms, phase-folded plots
        plots/                   ← prepared light curve plots
        manifests/               ← per-run target manifest (config hash, versions)
        metrics/                 ← per-target preprocessing summary copy
```

### Built-in presets

| Preset | Use case | TLS? | Iterative? | Period range |
|--------|----------|:----:|:----------:|:------------:|
| `quicklook` | Fast inspection | No (BLS) | No | 0.5–20d |
| `science-default` | Balanced analysis | Yes | No | 0.5–20d |
| `iterative-search` | **Batch planet hunting** | Yes | Yes (3 passes) | 0.5–25d |
| `deep-search` | Maximum sensitivity | Yes | Yes (3 passes) | 0.5–40d |

---

## Tips

- **Start with premium targets** — they have the best data (bright, many sectors). If the pipeline finds nothing there, it won't find anything in noisier data.
- **Each invocation is a run** — there are no "global" aggregates. The per-run layout means restarting an analysis cannot accidentally mix with prior results.
- **Use `--run-name`** for meaningful labels when you'll revisit a batch later (e.g., `--run-name q2_2026_bright_targets`). Without it you still get a unique timestamp-based id.
- **Resume is explicit** — `--resume <path>` only makes sense for the same targets file and config that produced the run. Resumption skips targets already in `run_state.json`.
- **Watch disk space** — each target produces ~1MB of artifacts per run. 3200 targets ≈ 3.2 GB per run. Multiple runs accumulate.
- **Cache is shared and safe to reuse** — `outputs/cache/` is untouched by new runs unless `--no-cache` is passed, and even then only writes are disabled (reads still hit the existing cache). You can safely delete `outputs/runs/<id>/` without affecting other runs or the cache.
- **MAST rate limits** — if you see many timeouts, the MAST server may be overloaded. The pipeline retries automatically, but very long runs may benefit from running overnight when MAST traffic is lower.
- **Iteration 0 vs 1+** — iteration 0 candidates are found after pre-masking known planets. Iteration 1+ candidates are found after additionally masking the iteration 0 signal. Multi-planet systems show up at iteration 1+.
