"""CLI entrypoint for downloading and plotting TESS light curves."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from exohunt.pipeline import fetch_and_plot, run_batch_analysis


DEFAULT_TARGET = "TIC 261136679"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Download and plot a TESS light curve.")
    parser.add_argument(
        "--target", default=DEFAULT_TARGET, help="Target name, e.g. 'TIC 261136679'."
    )
    parser.add_argument(
        "--batch-targets-file",
        default=None,
        help="Optional newline-delimited targets file for batch mode (one target per line).",
    )
    parser.add_argument(
        "--batch-resume",
        action="store_true",
        help="Resume a prior batch run by skipping targets already marked completed in state.",
    )
    parser.add_argument(
        "--batch-state-path",
        default=None,
        help="Optional path for batch resumable state JSON.",
    )
    parser.add_argument(
        "--batch-status-path",
        default=None,
        help="Optional path for batch status CSV (JSON sidecar written next to it).",
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
        help="Optional plot x-axis start in BTJD.",
    )
    parser.add_argument(
        "--plot-time-end",
        type=float,
        default=None,
        help="Optional plot x-axis end in BTJD.",
    )
    parser.add_argument(
        "--plot-sectors",
        default=None,
        help="Optional comma-separated sectors to include in plot, e.g. '14,15' (per-sector mode).",
    )
    parser.add_argument(
        "--no-bls",
        action="store_true",
        help="Disable BLS transit search on the prepared light curve.",
    )
    parser.add_argument(
        "--bls-period-min-days",
        type=float,
        default=0.5,
        help="Minimum BLS period in days.",
    )
    parser.add_argument(
        "--bls-period-max-days",
        type=float,
        default=20.0,
        help="Maximum BLS period in days.",
    )
    parser.add_argument(
        "--bls-duration-min-hours",
        type=float,
        default=0.5,
        help="Minimum BLS transit duration in hours.",
    )
    parser.add_argument(
        "--bls-duration-max-hours",
        type=float,
        default=10.0,
        help="Maximum BLS transit duration in hours.",
    )
    parser.add_argument(
        "--bls-n-periods",
        type=int,
        default=2000,
        help="Number of trial periods in BLS period grid.",
    )
    parser.add_argument(
        "--bls-n-durations",
        type=int,
        default=12,
        help="Number of trial durations in BLS duration grid.",
    )
    parser.add_argument(
        "--bls-top-n",
        type=int,
        default=5,
        help="Number of ranked BLS candidates to return/log.",
    )
    parser.add_argument(
        "--bls-mode",
        choices=["stitched", "per-sector"],
        default="stitched",
        help="Run BLS on stitched prepared light curve or separately per prepared sector.",
    )
    return parser


def _load_batch_targets(path: Path) -> list[str]:
    targets: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        targets.append(line)
    return targets


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    args = build_parser().parse_args()
    if args.batch_targets_file:
        batch_targets_file = Path(args.batch_targets_file)
        targets = _load_batch_targets(batch_targets_file)
        if not targets:
            raise RuntimeError(f"No targets found in batch file: {batch_targets_file}")
        run_batch_analysis(
            targets=targets,
            cache_dir=Path(args.cache_dir),
            refresh_cache=args.refresh_cache,
            outlier_sigma=args.outlier_sigma,
            flatten_window_length=args.flatten_window_length,
            max_download_files=args.max_download_files,
            no_flatten=args.no_flatten,
            preprocess_mode=args.preprocess_mode,
            authors=args.authors,
            interactive_html=args.interactive_html,
            interactive_max_points=args.interactive_max_points,
            plot_time_start=args.plot_time_start,
            plot_time_end=args.plot_time_end,
            plot_sectors=args.plot_sectors,
            run_bls=not args.no_bls,
            bls_period_min_days=args.bls_period_min_days,
            bls_period_max_days=args.bls_period_max_days,
            bls_duration_min_hours=args.bls_duration_min_hours,
            bls_duration_max_hours=args.bls_duration_max_hours,
            bls_n_periods=args.bls_n_periods,
            bls_n_durations=args.bls_n_durations,
            bls_top_n=args.bls_top_n,
            bls_mode=args.bls_mode,
            resume=args.batch_resume,
            state_path=Path(args.batch_state_path) if args.batch_state_path else None,
            status_path=Path(args.batch_status_path) if args.batch_status_path else None,
        )
    else:
        fetch_and_plot(
            args.target,
            cache_dir=Path(args.cache_dir),
            refresh_cache=args.refresh_cache,
            outlier_sigma=args.outlier_sigma,
            flatten_window_length=args.flatten_window_length,
            max_download_files=args.max_download_files,
            no_flatten=args.no_flatten,
            preprocess_mode=args.preprocess_mode,
            authors=args.authors,
            interactive_html=args.interactive_html,
            interactive_max_points=args.interactive_max_points,
            plot_time_start=args.plot_time_start,
            plot_time_end=args.plot_time_end,
            plot_sectors=args.plot_sectors,
            run_bls=not args.no_bls,
            bls_period_min_days=args.bls_period_min_days,
            bls_period_max_days=args.bls_period_max_days,
            bls_duration_min_hours=args.bls_duration_min_hours,
            bls_duration_max_hours=args.bls_duration_max_hours,
            bls_n_periods=args.bls_n_periods,
            bls_n_durations=args.bls_n_durations,
            bls_top_n=args.bls_top_n,
            bls_mode=args.bls_mode,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
