# Exohunt

Tools for fetching and inspecting TESS light curves.

## Quickstart

```bash
source .venv/bin/activate
pip install -e .[dev]
python -m exohunt.cli --target "TIC 261136679"
```

By default, downloaded stitched light curves are cached under `outputs/cache/lightcurves`.
Use `--refresh-cache` to ignore cache and download fresh data:

```bash
python -m exohunt.cli --target "TIC 261136679" --refresh-cache
```
