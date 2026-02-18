"""CLI entrypoint for downloading and plotting TESS light curves."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from exohunt.pipeline import fetch_and_plot


DEFAULT_TARGET = "TIC 261136679"


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
    parser.add_argument(
        "--outlier-sigma",
        type=float,
        default=5.0,
        help="Sigma threshold for outlier rejection in preprocessing.",
    )
    parser.add_argument(
        "--flatten-window-length",
        type=int,
        default=401,
        help="Window length used to flatten long-term trends.",
    )
    parser.add_argument(
        "--max-download-files",
        type=int,
        default=None,
        help="Optional cap on number of light-curve files to download before stitching.",
    )
    parser.add_argument(
        "--no-flatten",
        action="store_true",
        help="Disable flatten detrending in preprocessing.",
    )
    parser.add_argument(
        "--preprocess-mode",
        choices=["global", "per-sector"],
        default="per-sector",
        help="Preprocessing strategy. Per-sector is recommended for TESS.",
    )
    parser.add_argument(
        "--sectors",
        default=None,
        help="Optional comma-separated sector filter, e.g. '14,15,16'.",
    )
    parser.add_argument(
        "--authors",
        default="SPOC",
        help="Optional comma-separated author filter, e.g. 'SPOC'.",
    )
    parser.add_argument(
        "--interactive-html",
        action="store_true",
        help="Also save an interactive Plotly HTML (downsampled for performance).",
    )
    parser.add_argument(
        "--interactive-max-points",
        type=int,
        default=200000,
        help="Maximum points per trace in interactive HTML via min/max downsampling.",
    )
    parser.add_argument(
        "--plot-time-start",
        type=float,
        default=None,
        help="Optional plot x-axis start in BJD-2450000.",
    )
    parser.add_argument(
        "--plot-time-end",
        type=float,
        default=None,
        help="Optional plot x-axis end in BJD-2450000.",
    )
    return parser


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    args = build_parser().parse_args()
    fetch_and_plot(
        args.target,
        cache_dir=Path(args.cache_dir),
        refresh_cache=args.refresh_cache,
        outlier_sigma=args.outlier_sigma,
        flatten_window_length=args.flatten_window_length,
        max_download_files=args.max_download_files,
        no_flatten=args.no_flatten,
        preprocess_mode=args.preprocess_mode,
        sectors=args.sectors,
        authors=args.authors,
        interactive_html=args.interactive_html,
        interactive_max_points=args.interactive_max_points,
        plot_time_start=args.plot_time_start,
        plot_time_end=args.plot_time_end,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
