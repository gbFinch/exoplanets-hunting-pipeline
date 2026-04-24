from __future__ import annotations

from pathlib import Path
from typing import Any

import lightkurve as lk
import matplotlib.pyplot as plt
import numpy as np

from exohunt.bls import BLSCandidate
from exohunt.cache import _safe_target_name, _target_artifact_dir


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


def _safe_plot_key(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in value)


def save_raw_vs_prepared_plot(
    target: str,
    lc_raw: lk.LightCurve,
    lc_prepared: lk.LightCurve,
    boundaries: list[float],
    output_key: str = "stitched",
    smoothing_window: int = 5,
) -> Path:
    # Fix: Change 14 — Redesign raw-vs-prepared plot (PL1)
    output_dir = _target_artifact_dir(target, "plots")
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = (
        output_dir / f"{_safe_target_name(target)}_prepared_{_safe_plot_key(output_key)}.png"
    )

    raw_time = np.asarray(lc_raw.time.value, dtype=float)
    raw_flux = np.asarray(lc_raw.flux.value, dtype=float)
    prep_time = np.asarray(lc_prepared.time.value, dtype=float)
    prep_flux = np.asarray(lc_prepared.flux.value, dtype=float)
    prep_flux_ppm = _relative_flux_to_ppm(prep_flux)

    fig, (ax_overlay, ax_residual, ax_prepared_new) = plt.subplots(
        3, 1, figsize=(10, 9), sharex=True
    )

    # Panel 1: Overlay raw (gray) and prepared (purple) on same axes
    ax_overlay.plot(raw_time, raw_flux, ".", markersize=0.4, alpha=0.3, color="#999999",
                    label="Raw", rasterized=True)
    ax_overlay.plot(prep_time, prep_flux, ".", markersize=0.4, alpha=0.5, color="#4b2e83",
                    label="Prepared", rasterized=True)
    ax_overlay.set_title(f"Raw vs Prepared Overlay: {target}")
    ax_overlay.set_ylabel("Flux")
    ax_overlay.legend(loc="upper right", markerscale=6, fontsize=8)

    # Panel 2: Residual (removed trend) — interpolate prepared onto raw time grid
    if len(raw_time) > 0 and len(prep_time) > 0:
        # Compute binned medians for both raw and prepared on a common grid
        raw_bx, _, raw_b50, _ = _binned_summary(raw_time, raw_flux, bin_width_days=0.05)
        prep_bx, _, prep_b50, _ = _binned_summary(prep_time, prep_flux, bin_width_days=0.05)
        if len(raw_bx) > 0 and len(prep_bx) > 0:
            raw_b50_s = _smooth_series(raw_b50, window=15)
            prep_b50_s = _smooth_series(prep_b50, window=15)
            # Interpolate prepared trend onto raw time grid
            from numpy import interp as np_interp
            prep_on_raw = np_interp(raw_bx, prep_bx, prep_b50_s)
            residual = raw_b50_s - prep_on_raw
            ax_residual.fill_between(raw_bx, 0, residual, color="#e76f51", alpha=0.3)
            ax_residual.plot(raw_bx, residual, color="#e76f51", linewidth=1.0, alpha=0.8)
    ax_residual.axhline(0, color="gray", linewidth=0.5, alpha=0.5)
    ax_residual.set_title("Removed Trend (Raw − Prepared baseline)")
    ax_residual.set_ylabel("Residual Flux")

    # Panel 3: Prepared with binned percentile bands (kept from original)
    prep_x, prep_p10, prep_p50, prep_p90 = _binned_summary(prep_time, prep_flux_ppm)
    if len(prep_x):
        prep_p10_s = _smooth_series(prep_p10, window=smoothing_window)
        prep_p50_s = _smooth_series(prep_p50, window=smoothing_window)
        prep_p90_s = _smooth_series(prep_p90, window=smoothing_window)
        ax_prepared_new.fill_between(
            prep_x, prep_p10_s, prep_p90_s, color="#6a3d9a", alpha=0.12, linewidth=0
        )
        ax_prepared_new.plot(prep_x, prep_p50_s, color="#4b2e83", linewidth=1.2, alpha=0.95)
    ax_prepared_new.set_title("Prepared (density + trend band)")
    ax_prepared_new.set_xlabel("Time [BTJD]")
    ax_prepared_new.set_ylabel("Relative Flux [ppm]")
    ax_prepared_new.set_ylim(*_robust_ylim(prep_flux_ppm))

    for boundary in boundaries:
        ax_overlay.axvline(boundary, color="gray", alpha=0.2, linewidth=0.8)
        ax_residual.axvline(boundary, color="gray", alpha=0.2, linewidth=0.8)
        ax_prepared_new.axvline(boundary, color="gray", alpha=0.2, linewidth=0.8)

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
    output_key: str = "stitched",
) -> Path:
    try:
        import plotly.graph_objects as go
        from plotly.subplots import make_subplots
    except Exception as exc:  # pragma: no cover - optional dependency path
        raise RuntimeError(
            "Interactive plotting requires plotly. Install it to use --interactive-html."
        ) from exc

    output_dir = _target_artifact_dir(target, "plots")
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = (
        output_dir / f"{_safe_target_name(target)}_prepared_{_safe_plot_key(output_key)}.html"
    )

    raw_time = np.asarray(lc_raw.time.value, dtype=float)
    raw_flux = np.asarray(lc_raw.flux.value, dtype=float)
    prep_time = np.asarray(lc_prepared.time.value, dtype=float)
    prep_flux = np.asarray(lc_prepared.flux.value, dtype=float)

    raw_time_ds, raw_flux_ds = _downsample_minmax(raw_time, raw_flux, max_points=max_points)
    prep_time_ds, prep_flux_ds = _downsample_minmax(prep_time, prep_flux, max_points=max_points)
    prep_flux_ppm_ds = _relative_flux_to_ppm(prep_flux_ds)

    fig = make_subplots(rows=3, cols=1, shared_xaxes=True, vertical_spacing=0.06)

    # Fix: Change 14 — Redesign interactive plot with overlay + residual (PL1)
    # Panel 1: Overlay raw (gray) and prepared (purple)
    fig.add_trace(
        go.Scattergl(
            x=raw_time_ds, y=raw_flux_ds, mode="markers",
            marker={"size": 2, "opacity": 0.3, "color": "#999999"}, name="Raw",
        ), row=1, col=1,
    )
    fig.add_trace(
        go.Scattergl(
            x=prep_time_ds, y=prep_flux_ds, mode="markers",
            marker={"size": 2, "opacity": 0.5, "color": "#4b2e83"}, name="Prepared",
        ), row=1, col=1,
    )

    # Panel 2: Residual (removed trend)
    raw_bx, _, raw_b50, _ = _binned_summary(raw_time_ds, raw_flux_ds, bin_width_days=0.05)
    prep_bx, _, prep_b50, _ = _binned_summary(prep_time_ds, prep_flux_ds, bin_width_days=0.05)
    if len(raw_bx) > 0 and len(prep_bx) > 0:
        raw_b50_s = _smooth_series(raw_b50, window=15)
        prep_b50_s = _smooth_series(prep_b50, window=15)
        prep_on_raw = np.interp(raw_bx, prep_bx, prep_b50_s)
        residual = raw_b50_s - prep_on_raw
        fig.add_trace(
            go.Scatter(
                x=raw_bx, y=residual, mode="lines",
                line={"width": 1.2, "color": "rgba(231,111,81,0.8)"}, name="Residual",
                fill="tozeroy", fillcolor="rgba(231,111,81,0.2)",
            ), row=2, col=1,
        )

    # Panel 3: Prepared with binned percentile bands (kept from original)
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
        fig.add_vline(x=boundary, line_width=1, line_color="gray", opacity=0.25)

    fig.update_layout(
        title=f"TESS Light Curve (overlay + residual + prepared): {target}",
        showlegend=True,
        height=1050,
    )
    fig.update_xaxes(title_text="Time [BTJD]", row=3, col=1, rangeslider={"visible": True})
    fig.update_yaxes(title_text="Flux (overlay)", row=1, col=1)
    fig.update_yaxes(title_text="Residual Flux", row=2, col=1)
    fig.update_yaxes(title_text="Relative Flux [ppm]", row=3, col=1)
    prep_ymin, prep_ymax = _robust_ylim(prep_flux_ppm_ds)
    fig.update_yaxes(range=[prep_ymin, prep_ymax], row=3, col=1)

    fig.write_html(str(output_path), include_plotlyjs="cdn")
    return output_path


def _phase_fold_days(time: np.ndarray, period_days: float, epoch_days: float) -> np.ndarray:
    phase = ((time - epoch_days + 0.5 * period_days) % period_days) - 0.5 * period_days
    return phase


def _detect_sectors_by_gap(
    time_days: np.ndarray, gap_threshold_days: float = 3.0
) -> list[tuple[int, int]]:
    """Return list of (start_idx, end_idx_exclusive) slices delimiting sectors.

    Splits a stitched TESS light curve at any gap longer than gap_threshold_days.
    """
    if len(time_days) == 0:
        return []
    t = np.asarray(time_days, dtype=float)
    dt = np.diff(t)
    breaks = np.where(dt > gap_threshold_days)[0]
    edges = [0, *(breaks + 1).tolist(), len(t)]
    return [(edges[i], edges[i + 1]) for i in range(len(edges) - 1)]


def _batman_transit_model_ppm(
    phase_hours: np.ndarray,
    period_days: float,
    duration_hours: float,
    depth_ppm: float,
    limb_darkening: tuple[float, float] = (0.4804, 0.1867),
) -> np.ndarray | None:
    """Return a Mandel-Agol transit model evaluated at the given phase.

    Uses batman with a central-transit geometry inferred from the candidate's
    depth and duration. Returns flux depression in ppm, or None on failure.
    """
    try:
        import batman
    except Exception:
        return None
    try:
        rp_rs = np.sqrt(max(depth_ppm, 1.0) * 1e-6)
        # Central-transit assumption: duration ~ P/pi * arcsin(R_star/a)
        # => a/R_star = 1 / sin(pi * duration_days / period_days)
        duration_days = duration_hours / 24.0
        arg = np.pi * duration_days / max(period_days, 1e-6)
        arg = float(np.clip(arg, 1e-6, np.pi / 2.0 - 1e-6))
        a_rs = 1.0 / np.sin(arg)
        time_days = np.asarray(phase_hours, dtype=float) / 24.0
        params = batman.TransitParams()
        params.t0 = 0.0
        params.per = float(period_days)
        params.rp = float(rp_rs)
        params.a = float(max(a_rs, 2.0))
        params.inc = 90.0
        params.ecc = 0.0
        params.w = 90.0
        params.u = list(limb_darkening)
        params.limb_dark = "quadratic"
        m = batman.TransitModel(params, time_days)
        flux = m.light_curve(params)
        return (flux - 1.0) * 1e6
    except Exception:
        return None


def _phase_binned_median(
    phase_hours: np.ndarray,
    flux_ppm: np.ndarray,
    n_bins: int = 120,
    *,
    bin_width_hours: float | None = None,
    min_count: int = 12,
    phase_range: tuple[float, float] | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    if len(phase_hours) == 0:
        return np.asarray([], dtype=float), np.asarray([], dtype=float)
    if phase_range is not None:
        p_min, p_max = float(phase_range[0]), float(phase_range[1])
    else:
        p_min = float(np.nanmin(phase_hours))
        p_max = float(np.nanmax(phase_hours))
    if not np.isfinite(p_min) or not np.isfinite(p_max) or p_max <= p_min:
        return np.asarray([], dtype=float), np.asarray([], dtype=float)
    if bin_width_hours is not None and bin_width_hours > 0:
        n = max(2, int(np.ceil((p_max - p_min) / float(bin_width_hours))))
        edges = np.linspace(p_min, p_max, n + 1)
    else:
        edges = np.linspace(p_min, p_max, max(20, int(n_bins)) + 1)
    centers = []
    medians = []
    idx = np.digitize(phase_hours, edges) - 1
    for i in range(len(edges) - 1):
        mask = idx == i
        if int(np.count_nonzero(mask)) < int(min_count):
            continue
        centers.append(float((edges[i] + edges[i + 1]) * 0.5))
        medians.append(float(np.nanmedian(flux_ppm[mask])))
    return np.asarray(centers, dtype=float), np.asarray(medians, dtype=float)


def _empirical_depth_ppm(
    phase_hours: np.ndarray, flux_ppm: np.ndarray, duration_hours: float
) -> float:
    """Return in-transit minus out-of-transit median flux in ppm.

    In-transit window: |phase| < D/4. OOT window: D < |phase| < 3D. Returns
    NaN if either side has no samples.
    """
    if len(phase_hours) == 0 or not np.isfinite(duration_hours) or duration_hours <= 0:
        return float("nan")
    abs_ph = np.abs(phase_hours)
    in_mask = abs_ph < (duration_hours / 4.0)
    oot_mask = (abs_ph > duration_hours) & (abs_ph < 3.0 * duration_hours)
    if not np.any(in_mask) or not np.any(oot_mask):
        return float("nan")
    return float(np.nanmedian(flux_ppm[in_mask]) - np.nanmedian(flux_ppm[oot_mask]))


def save_candidate_diagnostics(
    target: str,
    output_key: str,
    lc_prepared: lk.LightCurve,
    candidates: list[BLSCandidate],
    period_grid_days: np.ndarray,
    power_grid: np.ndarray,
    *,
    vetting_results: dict[int, Any] | None = None,
    parameter_estimates: dict[int, Any] | None = None,
    stellar_params: Any | None = None,
) -> list[tuple[Path, Path]]:
    output_dir = _target_artifact_dir(target, "diagnostics")
    output_dir.mkdir(parents=True, exist_ok=True)
    time = np.asarray(lc_prepared.time.value, dtype=float)
    flux = np.asarray(lc_prepared.flux.value, dtype=float)
    finite = np.isfinite(time) & np.isfinite(flux)
    time = time[finite]
    flux = flux[finite]
    flux_ppm = _relative_flux_to_ppm(flux)

    written: list[tuple[Path, Path]] = []
    for candidate in candidates:
        rank_tag = f"candidate_{candidate.rank:02d}"
        base = f"{_safe_target_name(target)}__bls_{output_key}__{rank_tag}"
        periodogram_path = output_dir / f"{base}_periodogram.png"
        phasefold_path = output_dir / f"{base}_phasefold.png"

        fig_p, ax_p = plt.subplots(figsize=(9, 3.5))
        if len(period_grid_days) and len(power_grid):
            ax_p.plot(period_grid_days, power_grid, color="#264653", linewidth=1.0)
        ax_p.axvline(candidate.period_days, color="#e76f51", linewidth=1.2, alpha=0.9)
        ax_p.set_xlabel("Period [days]")
        ax_p.set_ylabel("BLS Power")
        ax_p.set_title(f"BLS Periodogram: {target} (candidate #{candidate.rank})")
        # R12: SNR annotation
        if len(power_grid):
            peak_idx = np.argmin(np.abs(period_grid_days - candidate.period_days))
            ax_p.annotate(
                f"SNR={candidate.snr:.1f}",
                xy=(candidate.period_days, power_grid[peak_idx] if peak_idx < len(power_grid) else 0),
                xytext=(10, 10), textcoords="offset points",
                fontsize=8, color="#e76f51", fontweight="bold",
            )
        fig_p.tight_layout()
        fig_p.savefig(periodogram_path, dpi=150)
        plt.close(fig_p)

        # -----------------------------------------------------------
        # Discovery-quality diagnostic: 5-panel figure.
        # Panels (top-to-bottom):
        #   1. Zoom phase-fold with Mandel-Agol model overlay
        #   2. Residuals (data - model) at the zoom scale
        #   3. Per-sector phase-fold stack (small multiples, shared y)
        #   4. Odd vs Even transit overlay
        #   5. Secondary-eclipse check (zoom at phase = P/2)
        # -----------------------------------------------------------
        phase_days = _phase_fold_days(time, candidate.period_days, candidate.transit_time)
        phase_hours_time_order = phase_days * 24.0  # time-ordered phase
        order = np.argsort(phase_days)
        phase_hours = phase_days[order] * 24.0  # phase-ordered phase
        flux_ppm_ordered = flux_ppm[order]
        half_window_hours = candidate.duration_hours * 0.5

        # Common zoom window and fine binning for Panel 1 / 2
        zoom_half_width = max(3.0 * candidate.duration_hours, 4.0)
        zoom_mask = np.abs(phase_hours) <= zoom_half_width
        phase_zoom = phase_hours[zoom_mask]
        flux_zoom = flux_ppm_ordered[zoom_mask]
        x_fine, y_fine = _phase_binned_median(
            phase_zoom, flux_zoom,
            bin_width_hours=5.0 / 60.0, min_count=3,
            phase_range=(-zoom_half_width, zoom_half_width),
        )

        # Empirical depth (vetting sanity check)
        empirical_depth_ppm = _empirical_depth_ppm(
            phase_zoom, flux_zoom, float(candidate.duration_hours)
        )

        # Y-limits based on binned-point MAD (not raw cadence scatter)
        if len(x_fine):
            oot_fine_mask = (np.abs(x_fine) > candidate.duration_hours) & (
                np.abs(x_fine) < 3.0 * candidate.duration_hours
            )
            oot_fine_mad = float(np.nanmedian(np.abs(
                y_fine[oot_fine_mask] - np.nanmedian(y_fine[oot_fine_mask])
            ))) if np.any(oot_fine_mask) else 0.0
        else:
            oot_fine_mad = 0.0
        y_half = max(3.0 * float(candidate.depth_ppm), 8.0 * oot_fine_mad, 200.0)

        # Mandel-Agol transit model (for Panel 1 overlay + Panel 2 residuals)
        model_x = np.linspace(-zoom_half_width, zoom_half_width, 400)
        ld_u = (0.4804, 0.1867)
        if stellar_params is not None and not getattr(stellar_params, "used_defaults", True):
            ld_u = tuple(stellar_params.limb_darkening)
        model_y = _batman_transit_model_ppm(
            model_x, candidate.period_days, candidate.duration_hours,
            candidate.depth_ppm, limb_darkening=ld_u,
        )
        model_at_bins = _batman_transit_model_ppm(
            x_fine, candidate.period_days, candidate.duration_hours,
            candidate.depth_ppm, limb_darkening=ld_u,
        ) if len(x_fine) else None

        # Sectors from time-gap detection (>3d gap = new sector) on TIME-ORDERED time
        sector_slices = _detect_sectors_by_gap(time, gap_threshold_days=3.0)

        # Odd/even masks — transit-number based (in TIME order)
        t0_cand = float(candidate.transit_time)
        P_cand = float(candidate.period_days)
        n_transit = np.floor((time - t0_cand) / P_cand + 0.5).astype(int)
        odd_mask_t = (n_transit % 2) != 0  # time-ordered
        even_mask_t = ~odd_mask_t

        # Secondary-eclipse phase (half-period offset) on time-ordered time
        phase_sec_hours_t = (((time - t0_cand - 0.5 * P_cand) % P_cand) - 0.5 * P_cand) * 24.0
        sec_zoom_mask_t = np.abs(phase_sec_hours_t) <= zoom_half_width
        xs_fine, ys_fine = _phase_binned_median(
            phase_sec_hours_t[sec_zoom_mask_t], flux_ppm[sec_zoom_mask_t],
            bin_width_hours=5.0 / 60.0, min_count=3,
            phase_range=(-zoom_half_width, zoom_half_width),
        )

        # ------------------- Figure layout -------------------
        n_sectors = max(len(sector_slices), 1)
        sector_cols = min(4, n_sectors)
        sector_rows = int(np.ceil(n_sectors / sector_cols))
        fig_f = plt.figure(figsize=(10, 2.5 + 1.1 + 1.4 * sector_rows + 1.7 + 1.7))
        gs = fig_f.add_gridspec(
            nrows=4 + sector_rows, ncols=sector_cols,
            height_ratios=[3.0, 1.2, *([1.6] * sector_rows), 2.2, 2.2],
            hspace=0.45, wspace=0.25,
        )

        # Panel 1: Zoom + model
        ax_zoom = fig_f.add_subplot(gs[0, :])
        ax_zoom.plot(phase_zoom, flux_zoom, ".", markersize=0.4, alpha=0.12, color="#4c566a")
        if len(x_fine):
            ax_zoom.plot(x_fine, y_fine, "-s", color="#d62728", markersize=3.0,
                         linewidth=0.8, alpha=0.9, label="5-min bins")
        if model_y is not None:
            ax_zoom.plot(model_x, model_y, color="#2a9d8f", linewidth=1.8, alpha=0.9,
                         label="Transit model (Mandel-Agol)")
        ax_zoom.axvspan(-half_window_hours, half_window_hours, color="#ffb703",
                        alpha=0.35, label="Transit window")
        ax_zoom.axvline(0.0, color="#e76f51", linewidth=1.0, alpha=0.9)
        if np.isfinite(empirical_depth_ppm):
            ax_zoom.axhline(empirical_depth_ppm, color="#1f77b4", linewidth=1.0,
                            linestyle="--", alpha=0.85,
                            label=f"Empirical depth ({empirical_depth_ppm:.0f} ppm)")
        ax_zoom.set_xlim(-zoom_half_width, zoom_half_width)
        ax_zoom.set_ylim(-y_half, y_half)
        ax_zoom.set_xlabel("")
        ax_zoom.set_ylabel("Flux [ppm]")
        ax_zoom.set_title(
            f"Phase Folded (Zoom ±{zoom_half_width:.1f} h, ~{zoom_half_width / candidate.duration_hours:.1f}D)"
        )
        ax_zoom.legend(loc="upper right", fontsize=7)

        # Panel 2: Residuals (data bins - model at bin centers)
        ax_res = fig_f.add_subplot(gs[1, :], sharex=ax_zoom)
        if len(x_fine) and model_at_bins is not None:
            residuals = y_fine - model_at_bins
            ax_res.plot(x_fine, residuals, "-s", color="#4c566a", markersize=2.5,
                        linewidth=0.6, alpha=0.9)
            res_rms = float(np.nanstd(residuals))
            ax_res.axhline(0.0, color="#e76f51", linewidth=0.8, alpha=0.8)
            ax_res.text(0.99, 0.95, f"RMS={res_rms:.0f} ppm",
                        transform=ax_res.transAxes, fontsize=7, ha="right", va="top",
                        bbox=dict(boxstyle="round", facecolor="white", alpha=0.7))
        ax_res.set_ylim(-max(2.0 * float(candidate.depth_ppm), 4.0 * oot_fine_mad, 100.0),
                         max(2.0 * float(candidate.depth_ppm), 4.0 * oot_fine_mad, 100.0))
        ax_res.set_ylabel("Residuals\n[ppm]", fontsize=8)
        ax_res.set_xlabel("")
        ax_res.tick_params(labelsize=7)

        # Panel 3: Per-sector stack (work in time order)
        sector_axes = []
        zoom_mask_t = np.abs(phase_hours_time_order) <= zoom_half_width
        for si, (s_start, s_end) in enumerate(sector_slices):
            r = 2 + si // sector_cols
            c = si % sector_cols
            ax_s = fig_f.add_subplot(gs[r, c])
            sector_axes.append(ax_s)
            in_sector = np.zeros(len(time), dtype=bool)
            in_sector[s_start:s_end] = True
            m = zoom_mask_t & in_sector
            ph_s = phase_hours_time_order[m]
            fx_s = flux_ppm[m]
            x_sfine, y_sfine = _phase_binned_median(
                ph_s, fx_s, bin_width_hours=5.0 / 60.0, min_count=2,
                phase_range=(-zoom_half_width, zoom_half_width),
            )
            if len(x_sfine):
                ax_s.plot(x_sfine, y_sfine, "-s", color="#d62728",
                          markersize=1.8, linewidth=0.6, alpha=0.85)
            ax_s.axvspan(-half_window_hours, half_window_hours,
                         color="#ffb703", alpha=0.25)
            ax_s.axvline(0.0, color="#e76f51", linewidth=0.5, alpha=0.7)
            ax_s.axhline(0.0, color="#888", linewidth=0.4, alpha=0.5)
            ax_s.set_xlim(-zoom_half_width, zoom_half_width)
            ax_s.set_ylim(-1.5 * y_half, 1.5 * y_half)
            s_depth = _empirical_depth_ppm(ph_s, fx_s, float(candidate.duration_hours))
            label_text = f"S{si + 1}"
            if np.isfinite(s_depth):
                label_text += f"  d={s_depth:.0f}"
            ax_s.text(0.03, 0.95, label_text, transform=ax_s.transAxes,
                      fontsize=7, va="top", ha="left",
                      bbox=dict(boxstyle="round", facecolor="white", alpha=1.0,
                                edgecolor="#888"))
            ax_s.tick_params(labelsize=6)
            if c != 0:
                ax_s.set_yticklabels([])
            if si < n_sectors - sector_cols:
                ax_s.set_xticklabels([])
        if sector_axes:
            sector_axes[0].set_ylabel("Per-sector\n[ppm]", fontsize=8)

        # Panel 4: Odd vs Even overlay (time-ordered masks)
        ax_oe = fig_f.add_subplot(gs[2 + sector_rows, :])
        for label, mask, color in [
            ("Odd transits", odd_mask_t & zoom_mask_t, "#1f77b4"),
            ("Even transits", even_mask_t & zoom_mask_t, "#d62728"),
        ]:
            ph_g = phase_hours_time_order[mask]
            fx_g = flux_ppm[mask]
            x_g, y_g = _phase_binned_median(
                ph_g, fx_g, bin_width_hours=5.0 / 60.0, min_count=2,
                phase_range=(-zoom_half_width, zoom_half_width),
            )
            depth_g = _empirical_depth_ppm(ph_g, fx_g, float(candidate.duration_hours))
            if len(x_g):
                lbl = f"{label} ({depth_g:.0f} ppm)" if np.isfinite(depth_g) else label
                ax_oe.plot(x_g, y_g, "-s", color=color, markersize=2.5,
                           linewidth=0.7, alpha=0.9, label=lbl)
        ax_oe.axvspan(-half_window_hours, half_window_hours,
                      color="#ffb703", alpha=0.3)
        ax_oe.axvline(0.0, color="#888", linewidth=0.6, alpha=0.7)
        ax_oe.axhline(0.0, color="#888", linewidth=0.4, alpha=0.5)
        ax_oe.set_xlim(-zoom_half_width, zoom_half_width)
        ax_oe.set_ylim(-y_half, y_half)
        ax_oe.set_xlabel("Phase [hours]")
        ax_oe.set_ylabel("Odd/Even\n[ppm]", fontsize=8)
        ax_oe.set_title("Odd vs Even transits")
        ax_oe.legend(loc="upper right", fontsize=7)

        # Panel 5: Secondary-eclipse check (phase = P/2)
        ax_sec = fig_f.add_subplot(gs[3 + sector_rows, :])
        if len(xs_fine):
            ax_sec.plot(xs_fine, ys_fine, "-s", color="#6a51a3", markersize=2.5,
                        linewidth=0.7, alpha=0.9, label="5-min bins")
        ax_sec.axvspan(-half_window_hours, half_window_hours,
                       color="#ffb703", alpha=0.3, label="Secondary window")
        ax_sec.axvline(0.0, color="#888", linewidth=0.6, alpha=0.7)
        ax_sec.axhline(0.0, color="#888", linewidth=0.4, alpha=0.5)
        ax_sec.set_xlim(-zoom_half_width, zoom_half_width)
        ax_sec.set_ylim(-y_half, y_half)
        sec_depth = _empirical_depth_ppm(
            phase_sec_hours_t[sec_zoom_mask_t], flux_ppm[sec_zoom_mask_t],
            float(candidate.duration_hours),
        )
        if np.isfinite(sec_depth):
            ax_sec.axhline(sec_depth, color="#1f77b4", linewidth=0.8,
                           linestyle="--", alpha=0.8,
                           label=f"Secondary depth ({sec_depth:.0f} ppm)")
        ax_sec.set_xlabel("Phase from secondary [hours]")
        ax_sec.set_ylabel("Secondary\n[ppm]", fontsize=8)
        ax_sec.set_title(f"Secondary eclipse check (phase = P/2 = {0.5 * candidate.period_days:.3f} d)")
        ax_sec.legend(loc="upper right", fontsize=7)

        # Parameter text box (bottom-left of figure)
        vr = (vetting_results or {}).get(int(candidate.rank))
        vetting_str = "N/A"
        if vr is not None:
            vetting_str = "PASS" if vr.vetting_pass else "FAIL"
        param_text = (
            f"P={candidate.period_days:.4f}d  D={candidate.duration_hours:.2f}h\n"
            f"Depth={candidate.depth_ppm:.1f}ppm  SNR={candidate.snr:.1f}\n"
            f"Empirical={empirical_depth_ppm:.1f}ppm  Sectors={n_sectors}\n"
            f"Vetting: {vetting_str}"
        )
        fig_f.text(0.01, 0.005, param_text, fontsize=7, family="monospace",
                   verticalalignment="bottom",
                   bbox=dict(boxstyle="round", facecolor="wheat", alpha=0.5))
        fig_f.suptitle(
            f"{target} — candidate #{candidate.rank}  "
            f"(P={candidate.period_days:.4f}d, depth={candidate.depth_ppm:.0f}ppm, SNR={candidate.snr:.1f})",
            fontsize=10,
        )
        fig_f.savefig(phasefold_path, dpi=130)
        plt.close(fig_f)

        written.append((periodogram_path, phasefold_path))
    return written
