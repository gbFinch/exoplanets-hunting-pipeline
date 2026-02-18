from __future__ import annotations

from pathlib import Path

import lightkurve as lk
import matplotlib.pyplot as plt
import numpy as np

from exohunt.cache import _safe_target_name


def _to_bjd_minus_2450000(btjd: np.ndarray) -> np.ndarray:
    return btjd + 7000.0


def _relative_flux_to_ppm(relative_flux: np.ndarray) -> np.ndarray:
    return (relative_flux - 1.0) * 1_000_000.0


def _point_density(
    time: np.ndarray,
    flux: np.ndarray,
    bins_time: int = 300,
    bins_flux: int = 180,
) -> np.ndarray:
    if len(time) == 0:
        return np.asarray([], dtype=float)
    hist, xedges, yedges = np.histogram2d(time, flux, bins=[bins_time, bins_flux])
    x_idx = np.clip(np.digitize(time, xedges) - 1, 0, hist.shape[0] - 1)
    y_idx = np.clip(np.digitize(flux, yedges) - 1, 0, hist.shape[1] - 1)
    return hist[x_idx, y_idx]


def _density_strength(density: np.ndarray) -> np.ndarray:
    if len(density) == 0:
        return np.asarray([], dtype=float)
    logd = np.log10(np.maximum(density, 1.0))
    lo, hi = np.nanpercentile(logd, [5, 95])
    if not np.isfinite(lo) or not np.isfinite(hi) or hi <= lo:
        return np.full(len(density), 0.5, dtype=float)
    return np.clip((logd - lo) / (hi - lo), 0.0, 1.0)


def _plot_brightness_scatter(ax: plt.Axes, time: np.ndarray, flux: np.ndarray) -> None:
    density = _point_density(time, flux)
    strength = _density_strength(density)
    order = np.argsort(strength)
    cmap = plt.get_cmap("Purples")
    rgba = cmap(strength[order])
    rgba[:, 3] = 0.15 + 0.85 * strength[order]
    ax.scatter(time[order], flux[order], c=rgba, s=1.4, linewidths=0, rasterized=True)


def _filter_singular_points(
    time: np.ndarray,
    flux: np.ndarray,
    min_density_percentile: float = 20.0,
) -> tuple[np.ndarray, np.ndarray]:
    if len(time) == 0:
        return time, flux
    density = _point_density(time, flux)
    threshold = float(np.nanpercentile(density, min_density_percentile))
    keep = density >= threshold
    return time[keep], flux[keep]


def _binned_summary(
    time: np.ndarray,
    flux: np.ndarray,
    bin_width_days: float = 0.02,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    if len(time) == 0:
        empty = np.asarray([], dtype=float)
        return empty, empty, empty, empty
    t_min = float(np.nanmin(time))
    t_max = float(np.nanmax(time))
    if not np.isfinite(t_min) or not np.isfinite(t_max) or t_max <= t_min:
        empty = np.asarray([], dtype=float)
        return empty, empty, empty, empty
    n_bins = max(50, int(np.ceil((t_max - t_min) / bin_width_days)))
    edges = np.linspace(t_min, t_max, n_bins + 1)
    indices = np.digitize(time, edges) - 1
    centers: list[float] = []
    p10: list[float] = []
    p50: list[float] = []
    p90: list[float] = []
    for i in range(n_bins):
        mask = indices == i
        if int(np.count_nonzero(mask)) < 8:
            continue
        f = flux[mask]
        centers.append(float((edges[i] + edges[i + 1]) * 0.5))
        q10, q50, q90 = np.nanpercentile(f, [10, 50, 90])
        p10.append(float(q10))
        p50.append(float(q50))
        p90.append(float(q90))
    return np.asarray(centers), np.asarray(p10), np.asarray(p50), np.asarray(p90)


def _robust_ylim(flux: np.ndarray, low_q: float = 0.5, high_q: float = 99.5) -> tuple[float, float]:
    if len(flux) == 0:
        return (-1.0, 1.0)
    low = float(np.nanpercentile(flux, low_q))
    high = float(np.nanpercentile(flux, high_q))
    if not np.isfinite(low) or not np.isfinite(high) or high <= low:
        center = float(np.nanmedian(flux))
        span = float(np.nanstd(flux))
        if not np.isfinite(span) or span <= 0:
            span = 1.0
        return (center - span, center + span)
    pad = 0.08 * (high - low)
    return (low - pad, high + pad)


def _smooth_series(values: np.ndarray, window: int = 9) -> np.ndarray:
    if len(values) == 0:
        return values
    w = min(window, len(values))
    if w < 3:
        return values
    if w % 2 == 0:
        w -= 1
    if w < 3:
        return values
    kernel = np.ones(w, dtype=float) / float(w)
    padded = np.pad(values, (w // 2, w // 2), mode="edge")
    return np.convolve(padded, kernel, mode="valid")


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
    prep_flux_ppm = _relative_flux_to_ppm(prep_flux)

    fig, (ax_raw_old, ax_prepared_old, ax_prepared_new) = plt.subplots(
        3, 1, figsize=(10, 9), sharex=True
    )

    # Figure 1: raw old style
    ax_raw_old.plot(raw_time, raw_flux, ".", markersize=0.5, alpha=0.7)
    ax_raw_old.set_title(f"TESS Light Curve (Raw, Old Style): {target}")
    ax_raw_old.set_ylabel("Flux")

    # Figure 2: prepared old style
    ax_prepared_old.plot(prep_time, prep_flux, ".", markersize=0.5, alpha=0.7)
    ax_prepared_old.set_title("Prepared (Old Style Scatter)")
    ax_prepared_old.set_ylabel("Relative Flux")

    # Figure 3: prepared new style
    prep_x, prep_p10, prep_p50, prep_p90 = _binned_summary(prep_time, prep_flux_ppm)
    if len(prep_x):
        prep_p10_s = _smooth_series(prep_p10, window=9)
        prep_p50_s = _smooth_series(prep_p50, window=9)
        prep_p90_s = _smooth_series(prep_p90, window=9)
        ax_prepared_new.fill_between(
            prep_x, prep_p10_s, prep_p90_s, color="#6a3d9a", alpha=0.12, linewidth=0
        )
        ax_prepared_new.plot(prep_x, prep_p50_s, color="#4b2e83", linewidth=1.2, alpha=0.95)
    ax_prepared_new.set_title("Prepared (New Style: density + trend band)")
    ax_prepared_new.set_xlabel("Time [BJD - 2450000]")
    ax_prepared_new.set_ylabel("Relative Flux [ppm]")
    ax_prepared_new.set_ylim(*_robust_ylim(prep_flux_ppm))

    for boundary in boundaries:
        boundary_bjd = boundary + 7000.0
        if plot_time_start is not None and boundary_bjd < plot_time_start:
            continue
        if plot_time_end is not None and boundary_bjd > plot_time_end:
            continue
        ax_raw_old.axvline(boundary_bjd, color="gray", alpha=0.2, linewidth=0.8)
        ax_prepared_old.axvline(boundary_bjd, color="gray", alpha=0.2, linewidth=0.8)
        ax_prepared_new.axvline(boundary_bjd, color="gray", alpha=0.2, linewidth=0.8)

    fig.tight_layout()
    fig.savefig(output_path, dpi=150)
    plt.close(fig)
    return output_path


def _downsample_minmax(
    time: np.ndarray, flux: np.ndarray, max_points: int
) -> tuple[np.ndarray, np.ndarray]:
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
        raise RuntimeError(
            "Interactive plotting requires plotly. Install it to use --interactive-html."
        ) from exc

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
    prep_flux_ppm_ds = _relative_flux_to_ppm(prep_flux_ds)

    fig = make_subplots(rows=3, cols=1, shared_xaxes=True, vertical_spacing=0.06)

    # Figure 1: raw old style
    fig.add_trace(
        go.Scattergl(
            x=raw_time_ds,
            y=raw_flux_ds,
            mode="markers",
            marker={"size": 2, "opacity": 0.7},
            name="Raw (old)",
        ),
        row=1,
        col=1,
    )

    # Figure 2: prepared old style
    fig.add_trace(
        go.Scattergl(
            x=prep_time_ds,
            y=prep_flux_ds,
            mode="markers",
            marker={"size": 2, "opacity": 0.7},
            name="Prepared (old)",
        ),
        row=2,
        col=1,
    )

    # Figure 3: prepared new style
    prep_bx, prep_bp10, prep_bp50, prep_bp90 = _binned_summary(prep_time_ds, prep_flux_ppm_ds)
    if len(prep_bx):
        prep_bp10_s = _smooth_series(prep_bp10, window=9)
        prep_bp50_s = _smooth_series(prep_bp50, window=9)
        prep_bp90_s = _smooth_series(prep_bp90, window=9)
        fig.add_trace(
            go.Scatter(
                x=prep_bx,
                y=prep_bp90_s,
                mode="lines",
                line={"width": 0, "color": "rgba(75,46,131,0)"},
                hoverinfo="skip",
                showlegend=False,
            ),
            row=3,
            col=1,
        )
        fig.add_trace(
            go.Scatter(
                x=prep_bx,
                y=prep_bp10_s,
                mode="lines",
                line={"width": 0, "color": "rgba(75,46,131,0)"},
                fill="tonexty",
                fillcolor="rgba(106,61,154,0.15)",
                hoverinfo="skip",
                showlegend=False,
            ),
            row=3,
            col=1,
        )
        fig.add_trace(
            go.Scatter(
                x=prep_bx,
                y=prep_bp50_s,
                mode="lines",
                line={"width": 1.4, "color": "rgba(75,46,131,0.95)"},
                hoverinfo="skip",
                name="Prepared (new)",
                showlegend=False,
            ),
            row=3,
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
        title=f"TESS Light Curve (3-panel comparison): {target}",
        showlegend=False,
        height=1050,
    )
    fig.update_xaxes(title_text="Time [BJD - 2450000]", row=3, col=1, rangeslider={"visible": True})
    fig.update_yaxes(title_text="Flux", row=1, col=1)
    fig.update_yaxes(title_text="Relative Flux", row=2, col=1)
    fig.update_yaxes(title_text="Relative Flux [ppm]", row=3, col=1)
    prep_ymin, prep_ymax = _robust_ylim(prep_flux_ppm_ds)
    fig.update_yaxes(range=[prep_ymin, prep_ymax], row=3, col=1)

    fig.write_html(str(output_path), include_plotlyjs="cdn")
    return output_path
