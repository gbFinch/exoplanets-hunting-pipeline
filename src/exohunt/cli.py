"""CLI for downloading and plotting TESS light curves."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

import numpy as np
import matplotlib.pyplot as plt
import lightkurve as lk


DEFAULT_TARGET = "TIC 261136679"
LOGGER = logging.getLogger(__name__)


def _safe_target_name(target: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in target).strip("_").lower()


def _cache_path(target: str, cache_dir: Path) -> Path:
    return cache_dir / f"{_safe_target_name(target)}.npz"


def fetch_and_plot(target: str, cache_dir: Path, refresh_cache: bool = False) -> Path:
    cache_path = _cache_path(target, cache_dir)

    lc = None
    data_source = "download"
    if cache_path.exists() and not refresh_cache:
        try:
            with np.load(cache_path) as cached:
                lc = lk.LightCurve(time=cached["time"], flux=cached["flux"])
            data_source = "cache"
        except Exception as exc:
            LOGGER.warning("Cache read failed for %s (%s); re-downloading.", cache_path, exc)

    if lc is None:
        search = lk.search_lightcurve(target, mission="TESS")
        if len(search) == 0:
            raise RuntimeError(f"No TESS light curves found for target: {target}")

        lcs = search.download_all()
        if lcs is None or len(lcs) == 0:
            raise RuntimeError(f"Failed to download TESS light curve for target: {target}")
        lc = lcs.stitch().remove_nans()
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        np.savez(cache_path, time=lc.time.value, flux=lc.flux.value)

    lc = lc.remove_nans()
    n_points = len(lc.time.value)
    time_min = float(lc.time.value.min())
    time_max = float(lc.time.value.max())

    output_dir = Path("outputs/plots")
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{_safe_target_name(target)}_raw.png"

    fig, ax = plt.subplots(figsize=(10, 4))
    ax.plot(lc.time.value, lc.flux.value, ".", markersize=0.5, alpha=0.7)
    ax.set_title(f"TESS Light Curve: {target}")
    ax.set_xlabel("Time [BTJD]")
    ax.set_ylabel("Flux")
    fig.tight_layout()
    fig.savefig(output_path, dpi=150)
    plt.close(fig)

    LOGGER.info("--------------------------------")
    LOGGER.info("Target: %s", target)
    LOGGER.info("Points: %d", n_points)
    LOGGER.info("Time range (BTJD): %.5f -> %.5f", time_min, time_max)
    LOGGER.info("Data source: %s", data_source)
    LOGGER.info("Cache file: %s", cache_path)
    LOGGER.info("Saved plot: %s", output_path)
    LOGGER.info("--------------------------------")

    return output_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Download and plot a TESS light curve.")
    parser.add_argument(
        "--target", default=DEFAULT_TARGET, help="Target name, e.g. 'TIC 261136679'."
    )
    parser.add_argument(
        "--cache-dir",
        default="outputs/cache/lightcurves",
        help="Directory for cached stitched light curves.",
    )
    parser.add_argument(
        "--refresh-cache",
        action="store_true",
        help="Ignore cached light curve and re-download from TESS.",
    )
    return parser


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    args = build_parser().parse_args()
    fetch_and_plot(args.target, cache_dir=Path(args.cache_dir), refresh_cache=args.refresh_cache)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
