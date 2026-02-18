from __future__ import annotations

from dataclasses import dataclass

from astropy.timeseries import BoxLeastSquares
import lightkurve as lk
import numpy as np


@dataclass(frozen=True)
class BLSCandidate:
    rank: int
    period_days: float
    duration_hours: float
    depth: float
    depth_ppm: float
    power: float
    transit_time: float
    transit_count_estimate: float


def _duration_grid_days(
    min_hours: float,
    max_hours: float,
    n_durations: int,
) -> np.ndarray:
    lo = max(0.1, float(min_hours)) / 24.0
    hi = max(lo, float(max_hours)) / 24.0
    count = max(4, int(n_durations))
    if np.isclose(lo, hi):
        return np.asarray([lo], dtype=float)
    return np.linspace(lo, hi, count)


def _unique_period(
    existing: list[BLSCandidate], period_days: float, min_separation_frac: float
) -> bool:
    for candidate in existing:
        denom = max(candidate.period_days, period_days, 1e-12)
        if abs(candidate.period_days - period_days) / denom < min_separation_frac:
            return False
    return True


def run_bls_search(
    lc_prepared: lk.LightCurve,
    period_min_days: float = 0.5,
    period_max_days: float = 20.0,
    duration_min_hours: float = 0.5,
    duration_max_hours: float = 10.0,
    n_periods: int = 2000,
    n_durations: int = 12,
    top_n: int = 5,
    unique_period_separation_fraction: float = 0.02,
) -> list[BLSCandidate]:
    """Run Box Least Squares and return ranked transit candidates.

    Theory: BLS searches periodic box-shaped dips expected for transits by
    fitting a repeating box model over a period-duration grid and ranking peaks
    by detection power.
    """
    time = np.asarray(lc_prepared.time.value, dtype=float)
    flux = np.asarray(lc_prepared.flux.value, dtype=float)
    finite = np.isfinite(time) & np.isfinite(flux)
    time = time[finite]
    flux = flux[finite]
    if len(time) < 50:
        return []
    order = np.argsort(time)
    time = time[order]
    flux = flux[order]

    span_days = float(np.nanmax(time) - np.nanmin(time))
    if not np.isfinite(span_days) or span_days <= 0:
        return []

    p_min = max(0.05, float(period_min_days))
    p_max_limit = max(p_min * 1.05, span_days * 0.95)
    p_max = min(float(period_max_days), p_max_limit)
    if p_max <= p_min:
        return []

    periods = np.geomspace(p_min, p_max, num=max(200, int(n_periods)))
    durations = _duration_grid_days(
        min_hours=duration_min_hours,
        max_hours=duration_max_hours,
        n_durations=n_durations,
    )
    durations = durations[durations < 0.25 * p_max]
    if len(durations) == 0:
        durations = np.asarray([min(0.1, 0.1 * p_max)], dtype=float)

    model = BoxLeastSquares(time, flux)
    result = model.power(periods, durations)

    power = np.asarray(result.power, dtype=float)
    period = np.asarray(result.period, dtype=float)
    duration = np.asarray(result.duration, dtype=float)
    depth = np.asarray(result.depth, dtype=float)
    transit_time = np.asarray(result.transit_time, dtype=float)

    finite_power = np.where(np.isfinite(power))[0]
    if len(finite_power) == 0:
        return []
    ranked_indices = finite_power[np.argsort(power[finite_power])[::-1]]

    picked: list[BLSCandidate] = []
    for idx in ranked_indices:
        p = float(period[idx])
        if p <= 0 or not np.isfinite(p):
            continue
        if not _unique_period(picked, p, unique_period_separation_fraction):
            continue
        d = float(depth[idx])
        t_count = span_days / p
        picked.append(
            BLSCandidate(
                rank=len(picked) + 1,
                period_days=p,
                duration_hours=float(duration[idx]) * 24.0,
                depth=d,
                depth_ppm=d * 1_000_000.0,
                power=float(power[idx]),
                transit_time=float(transit_time[idx]),
                transit_count_estimate=float(t_count),
            )
        )
        if len(picked) >= max(1, int(top_n)):
            break
    return picked


def compute_bls_periodogram(
    lc_prepared: lk.LightCurve,
    period_min_days: float = 0.5,
    period_max_days: float = 20.0,
    duration_min_hours: float = 0.5,
    duration_max_hours: float = 10.0,
    n_periods: int = 2000,
    n_durations: int = 12,
) -> tuple[np.ndarray, np.ndarray]:
    """Compute BLS periodogram (period grid and power) for diagnostics."""
    time = np.asarray(lc_prepared.time.value, dtype=float)
    flux = np.asarray(lc_prepared.flux.value, dtype=float)
    finite = np.isfinite(time) & np.isfinite(flux)
    time = time[finite]
    flux = flux[finite]
    if len(time) < 50:
        return np.asarray([], dtype=float), np.asarray([], dtype=float)
    order = np.argsort(time)
    time = time[order]
    flux = flux[order]

    span_days = float(np.nanmax(time) - np.nanmin(time))
    if not np.isfinite(span_days) or span_days <= 0:
        return np.asarray([], dtype=float), np.asarray([], dtype=float)

    p_min = max(0.05, float(period_min_days))
    p_max_limit = max(p_min * 1.05, span_days * 0.95)
    p_max = min(float(period_max_days), p_max_limit)
    if p_max <= p_min:
        return np.asarray([], dtype=float), np.asarray([], dtype=float)

    periods = np.geomspace(p_min, p_max, num=max(200, int(n_periods)))
    durations = _duration_grid_days(
        min_hours=duration_min_hours,
        max_hours=duration_max_hours,
        n_durations=n_durations,
    )
    durations = durations[durations < 0.25 * p_max]
    if len(durations) == 0:
        durations = np.asarray([min(0.1, 0.1 * p_max)], dtype=float)

    model = BoxLeastSquares(time, flux)
    result = model.power(periods, durations)
    return np.asarray(result.period, dtype=float), np.asarray(result.power, dtype=float)


def refine_bls_candidates(
    lc_prepared: lk.LightCurve,
    candidates: list[BLSCandidate],
    period_min_days: float,
    period_max_days: float,
    duration_min_hours: float,
    duration_max_hours: float,
    n_periods: int = 12000,
    n_durations: int = 20,
    window_fraction: float = 0.02,
) -> list[BLSCandidate]:
    """Refine top BLS candidates by dense local re-search around each period."""
    if not candidates:
        return []
    refined: list[BLSCandidate] = []
    for candidate in candidates:
        window = max(1e-4, candidate.period_days * float(window_fraction))
        local_min = max(float(period_min_days), candidate.period_days - window)
        local_max = min(float(period_max_days), candidate.period_days + window)
        if local_max <= local_min:
            refined.append(candidate)
            continue
        local = run_bls_search(
            lc_prepared=lc_prepared,
            period_min_days=local_min,
            period_max_days=local_max,
            duration_min_hours=duration_min_hours,
            duration_max_hours=duration_max_hours,
            n_periods=max(int(n_periods), 400),
            n_durations=max(int(n_durations), 6),
            top_n=1,
            unique_period_separation_fraction=0.0,
        )
        if local:
            best = local[0]
            refined.append(
                BLSCandidate(
                    rank=candidate.rank,
                    period_days=best.period_days,
                    duration_hours=best.duration_hours,
                    depth=best.depth,
                    depth_ppm=best.depth_ppm,
                    power=best.power,
                    transit_time=best.transit_time,
                    transit_count_estimate=best.transit_count_estimate,
                )
            )
        else:
            refined.append(candidate)
    return refined
