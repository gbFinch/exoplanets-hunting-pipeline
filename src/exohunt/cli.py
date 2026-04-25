"""CLI entrypoint for Exohunt workflows."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path
import sys

from exohunt.config import (
    ConfigValidationError,
    PresetMeta,
    get_builtin_preset_metadata,
    list_builtin_presets,
    resolve_runtime_config,
    write_preset_config,
)
from exohunt.batch import run_batch_analysis
from exohunt.pipeline import fetch_and_plot


DEFAULT_TARGET = "TIC 261136679"


def _load_batch_targets(path: Path) -> list[str]:
    targets: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        targets.append(line)
    return targets


def _resolve_config_reference(config_ref: str | None) -> tuple[str | None, Path | None]:
    if config_ref is None:
        return "science-default", None
    value = config_ref.strip()
    if not value:
        raise RuntimeError("--config cannot be empty.")
    if value in set(list_builtin_presets()):
        return value, None
    return None, Path(value)


def _resolve_runtime(
    *,
    config_ref: str | None,
    cli_overrides: dict[str, object] | None = None,
):
    preset_name, config_path = _resolve_config_reference(config_ref)
    try:
        runtime_config = resolve_runtime_config(
            config_path=config_path,
            preset_name=preset_name,
            cli_overrides=cli_overrides,
        )
    except ConfigValidationError as exc:
        raise RuntimeError(f"Invalid runtime configuration: {exc}") from exc

    preset_meta = PresetMeta()
    if runtime_config.preset is not None and runtime_config.preset in set(list_builtin_presets()):
        preset_meta = get_builtin_preset_metadata(runtime_config.preset)
    return runtime_config, preset_meta


def _run_single_target(*, target: str, config_ref: str | None) -> None:
    runtime_config, preset_meta = _resolve_runtime(config_ref=config_ref)
    fetch_and_plot(target, config=runtime_config, preset_meta=preset_meta)


def _run_batch_targets(
    *,
    targets_file: Path,
    config_ref: str | None,
    resume: bool,
    no_cache: bool = False,
    state_path: Path | None,
    status_path: Path | None,
) -> None:
    targets = _load_batch_targets(targets_file)
    if not targets:
        raise RuntimeError(f"No targets found in batch file: {targets_file}")

    runtime_config, preset_meta = _resolve_runtime(config_ref=config_ref)
    run_batch_analysis(
        targets=targets, config=runtime_config, preset_meta=preset_meta,
        resume=resume, no_cache=no_cache,
        state_path=state_path, status_path=status_path,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="exohunt", description="Exoplanet light-curve analysis")
    subparsers = parser.add_subparsers(dest="command")

    run_parser = subparsers.add_parser("run", help="Run analysis for a single target")
    run_parser.add_argument("--target", required=True, help="Target name, e.g. 'TIC 261136679'.")
    run_parser.add_argument(
        "--config",
        default="science-default",
        help="Preset name (quicklook/science-default/deep-search) or config TOML path.",
    )

    batch_parser = subparsers.add_parser("batch", help="Run analysis for many targets")
    batch_parser.add_argument(
        "--targets-file",
        required=True,
        help="Newline-delimited targets file (one target per line).",
    )
    batch_parser.add_argument(
        "--config",
        default="science-default",
        help="Preset name (quicklook/science-default/deep-search) or config TOML path.",
    )
    batch_parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume a prior batch run by skipping targets already marked completed in state.",
    )
    batch_parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Disable writing light curve cache files to save disk space.",
    )
    batch_parser.add_argument(
        "--state-path",
        default=None,
        help="Optional path for batch resumable state JSON.",
    )
    batch_parser.add_argument(
        "--status-path",
        default=None,
        help="Optional path for batch status CSV (JSON sidecar written next to it).",
    )

    init_parser = subparsers.add_parser("init-config", help="Write a starter config from preset")
    init_parser.add_argument(
        "--from", dest="from_preset", choices=list_builtin_presets(), required=True
    )
    init_parser.add_argument("--out", required=True, help="Destination config path.")

    return parser


def build_legacy_parser() -> argparse.ArgumentParser:
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
        "--no-flatten",
        action="store_true",
        help="Disable flatten detrending in preprocessing.",
    )
    parser.add_argument(
        "--preprocess-mode",
        choices=["stitched", "per-sector", "global"],
        default="per-sector",
        help="Preprocessing strategy (`global` is accepted as a legacy alias for `stitched`).",
    )
    parser.add_argument(
        "--no-preprocess",
        action="store_true",
        help="Disable preprocessing; raw downloaded flux is passed through as prepared flux.",
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
        "--plot-mode",
        choices=["stitched", "per-sector"],
        default="stitched",
        help="Plotting mode: one stitched plot or one plot per prepared sector.",
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


def _run_legacy(argv: list[str]) -> int:
    logging.warning(
        "Deprecated legacy CLI usage detected. Use command form: "
        "`exohunt run`, `exohunt batch`, or `exohunt init-config`."
    )
    args = build_legacy_parser().parse_args(argv)

    cli_overrides = {
        "io": {"refresh_cache": bool(args.refresh_cache)},
        "ingest": {
            "authors": [
                author.strip().upper() for author in str(args.authors).split(",") if author.strip()
            ]
        },
        "preprocess": {
            "enabled": bool(not args.no_preprocess),
            "mode": str(args.preprocess_mode),
            "outlier_sigma": float(args.outlier_sigma),
            "flatten_window_length": int(args.flatten_window_length),
            "flatten": bool(not args.no_flatten),
        },
        "plot": {
            "enabled": True,
            "mode": str(args.plot_mode),
            "interactive_html": bool(args.interactive_html),
            "interactive_max_points": int(args.interactive_max_points),
        },
        "bls": {
            "enabled": bool(not args.no_bls),
            "mode": str(args.bls_mode),
            "period_min_days": float(args.bls_period_min_days),
            "period_max_days": float(args.bls_period_max_days),
            "duration_min_hours": float(args.bls_duration_min_hours),
            "duration_max_hours": float(args.bls_duration_max_hours),
            "n_periods": int(args.bls_n_periods),
            "n_durations": int(args.bls_n_durations),
            "top_n": int(args.bls_top_n),
        },
    }

    runtime_config, preset_meta = _resolve_runtime(config_ref=None, cli_overrides=cli_overrides)

    if args.batch_targets_file:
        targets = _load_batch_targets(Path(args.batch_targets_file))
        if not targets:
            raise RuntimeError(f"No targets found in batch file: {args.batch_targets_file}")
        run_batch_analysis(
            targets=targets, config=runtime_config, preset_meta=preset_meta,
            resume=args.batch_resume,
            state_path=Path(args.batch_state_path) if args.batch_state_path else None,
            status_path=Path(args.batch_status_path) if args.batch_status_path else None,
        )
    else:
        fetch_and_plot(args.target, config=runtime_config, preset_meta=preset_meta)
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    argv = list(sys.argv[1:] if argv is None else argv)

    if argv and argv[0] in {"run", "batch", "init-config"}:
        args = build_parser().parse_args(argv)
        if args.command == "run":
            _run_single_target(target=str(args.target), config_ref=str(args.config))
            return 0
        if args.command == "batch":
            _run_batch_targets(
                targets_file=Path(str(args.targets_file)),
                config_ref=str(args.config),
                resume=bool(args.resume),
                no_cache=bool(getattr(args, 'no_cache', False)),
                state_path=Path(str(args.state_path)) if args.state_path else None,
                status_path=Path(str(args.status_path)) if args.status_path else None,
            )
            return 0
        if args.command == "init-config":
            out_path = write_preset_config(
                preset_name=str(args.from_preset),
                out_path=Path(str(args.out)),
            )
            logging.info("Wrote preset config: %s", out_path)
            return 0
        raise RuntimeError(f"Unsupported command: {args.command}")

    return _run_legacy(argv)


if __name__ == "__main__":
    raise SystemExit(main())
