# Exohunt

Tools for fetching and inspecting TESS light curves.

## Quickstart

```bash
source .venv/bin/activate
pip install -e .[dev]
python -m exohunt.cli --target "TIC 261136679"
```

By default, preprocessing runs in `per-sector` mode:
- each downloaded segment is cached under `outputs/cache/lightcurves/segments/<target>/`
- each prepared segment is cached with a preprocessing-parameter hash
- segments are stitched only after per-segment preprocessing

Global stitched-cache mode is still available with `--preprocess-mode global`.
Use `--refresh-cache` to ignore cache and download fresh data:

```bash
python -m exohunt.cli --target "TIC 261136679" --refresh-cache
```

Preprocessing is now applied before plotting (normalize, outlier filtering, flattening):

```bash
python -m exohunt.cli --target "TIC 261136679" --outlier-sigma 5 --flatten-window-length 401
```

Example per-sector filters:

```bash
python -m exohunt.cli --target "TIC 261136679" --preprocess-mode per-sector --sectors 14,15 --authors SPOC
```

Interactive downsampled HTML (recommended for very large light curves):

```bash
pip install -e .[plotting]
python -m exohunt.cli --target "TIC 261136679" --interactive-html --interactive-max-points 200000
```

Plots are generated only when at least one time bound is provided. For example (BTJD):

```bash
python -m exohunt.cli --target "TIC 261136679" --plot-time-start 1300 --plot-time-end 1350
```

You can also generate a plot by sector selection (per-sector mode):

```bash
python -m exohunt.cli --target "TIC 261136679" --preprocess-mode per-sector --plot-sectors 14,15
```

The output plot is saved as `outputs/plots/<target>_prepared.png`.

BLS transit-search core runs by default on prepared light curves (top candidates are logged):

```bash
python -m exohunt.cli --target "TIC 261136679" --bls-period-min-days 0.5 --bls-period-max-days 20 --bls-top-n 5
```

To run BLS independently per prepared sector (instead of stitched):

```bash
python -m exohunt.cli --target "TIC 261136679" --preprocess-mode per-sector --bls-mode per-sector
```

Each run writes ranked BLS candidate tables to `outputs/candidates/` as deterministic
CSV/JSON files keyed by target and run configuration.

When BLS candidates exist, per-candidate diagnostics are written to `outputs/diagnostics/`:
- periodogram plots with candidate period marker
- phase-folded light curves with transit-window overlays

Each run also writes preprocessing quality metrics to:
- `outputs/metrics/preprocessing_summary.csv` (append-only run table)
- `outputs/metrics/<target>_preprocessing_summary.json` (latest per-target summary)

Build preprocessing method comparison report (selects recommended defaults by cadence/span):

```bash
python -m exohunt.comparison \
  --metrics-csv outputs/metrics/preprocessing_summary.csv \
  --cache-dir outputs/cache/lightcurves \
  --report-path outputs/reports/preprocessing-method-comparison.md
```
