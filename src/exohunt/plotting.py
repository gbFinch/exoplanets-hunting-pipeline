from __future__ import annotations

from pathlib import Path

import lightkurve as lk
import matplotlib.pyplot as plt
import numpy as np

from exohunt.cache import _safe_target_name


def _to_bjd_minus_2450000(btjd: np.ndarray) -> np.ndarray:
    return btjd + 7000.0


def _apply_time_window(
    time: np.ndarray,
    flux: np.ndarray,
    plot_time_start: float | None,
    plot_time_end: float | None,
) -> tuple[np.ndarray, np.ndarray]:
    if plot_time_start is None and plot_time_end is None:
        return time, flux
    mask = np.ones(len(time), dtype=bool)
    if plot_time_start is not None:
        mask &= time >= plot_time_start
    if plot_time_end is not None:
        mask &= time <= plot_time_end
    return time[mask], flux[mask]


def save_raw_vs_prepared_plot(
    target: str,
    lc_raw: lk.LightCurve,
    lc_prepared: lk.LightCurve,
    boundaries: list[float],
    plot_time_start: float | None = None,
    plot_time_end: float | None = None,
) -> Path:
    output_dir = Path("outputs/plots")
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{_safe_target_name(target)}_prepared.png"

    raw_time = _to_bjd_minus_2450000(np.asarray(lc_raw.time.value, dtype=float))
    raw_flux = np.asarray(lc_raw.flux.value, dtype=float)
    prep_time = _to_bjd_minus_2450000(np.asarray(lc_prepared.time.value, dtype=float))
    prep_flux = np.asarray(lc_prepared.flux.value, dtype=float)
    raw_time, raw_flux = _apply_time_window(raw_time, raw_flux, plot_time_start, plot_time_end)
    prep_time, prep_flux = _apply_time_window(prep_time, prep_flux, plot_time_start, plot_time_end)

    fig, (ax_raw, ax_prepared) = plt.subplots(2, 1, figsize=(10, 6), sharex=True)
    ax_raw.plot(raw_time, raw_flux, ".", markersize=0.5, alpha=0.7)
    ax_raw.set_title(f"TESS Light Curve (Raw): {target}")
    ax_raw.set_ylabel("Flux")
    ax_prepared.plot(prep_time, prep_flux, ".", markersize=0.5, alpha=0.7)
    ax_prepared.set_title("Prepared (normalized, outlier-filtered, flattened)")
    ax_prepared.set_xlabel("Time [BJD - 2450000]")
    ax_prepared.set_ylabel("Relative Flux")
    for boundary in boundaries:
        boundary_bjd = boundary + 7000.0
        if plot_time_start is not None and boundary_bjd < plot_time_start:
            continue
        if plot_time_end is not None and boundary_bjd > plot_time_end:
            continue
        ax_raw.axvline(boundary_bjd, color="gray", alpha=0.2, linewidth=0.8)
        ax_prepared.axvline(boundary_bjd, color="gray", alpha=0.2, linewidth=0.8)
    fig.tight_layout()
    fig.savefig(output_path, dpi=150)
    plt.close(fig)
    return output_path


def _downsample_minmax(time: np.ndarray, flux: np.ndarray, max_points: int) -> tuple[np.ndarray, np.ndarray]:
    n_points = len(time)
    if max_points <= 0 or n_points <= max_points:
        return time, flux

    n_bins = max(1, max_points // 2)
    edges = np.linspace(0, n_points, n_bins + 1, dtype=int)
    t_out = []
    f_out = []
    for start, end in zip(edges[:-1], edges[1:]):
        if end <= start:
            continue
        t_bin = time[start:end]
        f_bin = flux[start:end]
        if len(f_bin) == 0:
            continue
        min_idx = int(np.nanargmin(f_bin))
        max_idx = int(np.nanargmax(f_bin))
        pair = sorted([min_idx, max_idx], key=lambda idx: t_bin[idx])
        for idx in pair:
            t_out.append(t_bin[idx])
            f_out.append(f_bin[idx])
    return np.asarray(t_out), np.asarray(f_out)


def save_raw_vs_prepared_plot_interactive(
    target: str,
    lc_raw: lk.LightCurve,
    lc_prepared: lk.LightCurve,
    boundaries: list[float],
    max_points: int = 200_000,
    plot_time_start: float | None = None,
    plot_time_end: float | None = None,
) -> Path:
    try:
        import plotly.graph_objects as go
        from plotly.subplots import make_subplots
    except Exception as exc:  # pragma: no cover - optional dependency path
        raise RuntimeError("Interactive plotting requires plotly. Install it to use --interactive-html.") from exc

    output_dir = Path("outputs/plots")
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{_safe_target_name(target)}_prepared.html"

    raw_time = _to_bjd_minus_2450000(np.asarray(lc_raw.time.value, dtype=float))
    raw_flux = np.asarray(lc_raw.flux.value, dtype=float)
    prep_time = _to_bjd_minus_2450000(np.asarray(lc_prepared.time.value, dtype=float))
    prep_flux = np.asarray(lc_prepared.flux.value, dtype=float)
    raw_time, raw_flux = _apply_time_window(raw_time, raw_flux, plot_time_start, plot_time_end)
    prep_time, prep_flux = _apply_time_window(prep_time, prep_flux, plot_time_start, plot_time_end)

    raw_time_ds, raw_flux_ds = _downsample_minmax(raw_time, raw_flux, max_points=max_points)
    prep_time_ds, prep_flux_ds = _downsample_minmax(prep_time, prep_flux, max_points=max_points)

    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.08)
    fig.add_trace(
        go.Scattergl(x=raw_time_ds, y=raw_flux_ds, mode="markers", marker={"size": 2}, name="Raw"),
        row=1,
        col=1,
    )
    fig.add_trace(
        go.Scattergl(
            x=prep_time_ds,
            y=prep_flux_ds,
            mode="markers",
            marker={"size": 2},
            name="Prepared",
        ),
        row=2,
        col=1,
    )

    for boundary in boundaries:
        boundary_bjd = boundary + 7000.0
        if plot_time_start is not None and boundary_bjd < plot_time_start:
            continue
        if plot_time_end is not None and boundary_bjd > plot_time_end:
            continue
        fig.add_vline(x=boundary_bjd, line_width=1, line_color="gray", opacity=0.25)

    fig.update_layout(
        title=f"TESS Light Curve (Downsampled Interactive): {target}",
        showlegend=False,
        height=800,
    )
    fig.update_xaxes(title_text="Time [BJD - 2450000]", row=2, col=1, rangeslider={"visible": True})
    fig.update_yaxes(title_text="Flux", row=1, col=1)
    fig.update_yaxes(title_text="Relative Flux", row=2, col=1)

    fig.write_html(str(output_path), include_plotlyjs="cdn")
    return output_path
