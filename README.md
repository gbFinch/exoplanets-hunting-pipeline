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

Plot only a selected timeline window (BJD-2450000):

```bash
python -m exohunt.cli --target "TIC 261136679" --plot-time-start 8300 --plot-time-end 8350
```

The output plot is saved as `outputs/plots/<target>_prepared.png`.
