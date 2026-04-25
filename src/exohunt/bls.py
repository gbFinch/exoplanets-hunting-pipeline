from __future__ import annotations

import logging
from dataclasses import dataclass

from astropy.timeseries import BoxLeastSquares
import lightkurve as lk
import numpy as np

LOGGER = logging.getLogger(__name__)


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
    snr: float
    fap: float = float("nan")
    iteration: int = 0


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


class _BLSInputs:
    """Validated and prepared inputs for BLS computation."""

    __slots__ = ("time", "flux", "model", "periods", "durations")

    def __init__(
        self,
        time: np.ndarray,
        flux: np.ndarray,
        model: BoxLeastSquares,
        periods: np.ndarray,
        durations: np.ndarray,
    ) -> None:
        self.time = time
        self.flux = flux
        self.model = model
        self.periods = periods
        self.durations = durations


def _prepare_bls_inputs(
    lc_prepared: lk.LightCurve,
    period_min_days: float,
    period_max_days: float,
    duration_min_hours: float,
    duration_max_hours: float,
    n_periods: int,
    n_durations: int,
) -> _BLSInputs | None:
    """Validate inputs, build period/duration grids, and instantiate BLS model.

    Returns None if inputs are invalid (too few points, zero span, bad period range).
    """
    time = np.asarray(lc_prepared.time.value, dtype=float)
    flux = np.asarray(lc_prepared.flux.value, dtype=float)
    finite = np.isfinite(time) & np.isfinite(flux)
    time = time[finite]
    flux = flux[finite]
    if len(time) < 50:
        return None
    order = np.argsort(time)
    time = time[order]
    flux = flux[order]

    span_days = float(np.nanmax(time) - np.nanmin(time))
    if not np.isfinite(span_days) or span_days <= 0:
        return None

    p_min = max(0.05, float(period_min_days))
    p_max_limit = max(p_min * 1.05, span_days * 0.95)
    p_max = min(float(period_max_days), p_max_limit)
    if p_max <= p_min:
        return None

    periods = np.geomspace(p_min, p_max, num=max(200, int(n_periods)))
    durations = _duration_grid_days(
        min_hours=duration_min_hours,
        max_hours=duration_max_hours,
        n_durations=n_durations,
    )
    durations = durations[durations < 0.25 * p_max]
    durations = durations[durations < p_min]
    if len(durations) == 0:
        durations = np.asarray([min(0.1, 0.1 * p_max)], dtype=float)

    model = BoxLeastSquares(time, flux)
    return _BLSInputs(time=time, flux=flux, model=model, periods=periods, durations=durations)


def _bootstrap_fap(
    time: np.ndarray,
    flux: np.ndarray,
    observed_power: float,
    periods: np.ndarray,
    durations: np.ndarray,
    n_iterations: int = 1000,
) -> float:
    """Compute false-alarm probability via bootstrap flux shuffling."""
    rng = np.random.default_rng()
    reduced_periods = np.geomspace(periods[0], periods[-1], min(200, len(periods)))
    count_above = 0
    valid = 0
    for _ in range(n_iterations):
        shuffled = flux.copy()
        rng.shuffle(shuffled)
        try:
            model = BoxLeastSquares(time, shuffled)
            result = model.power(reduced_periods, durations)
            max_power = float(np.nanmax(result.power))
            if np.isfinite(max_power):
                valid += 1
                if max_power >= observed_power:
                    count_above += 1
        except Exception:
            continue
    return count_above / valid if valid > 0 else float("nan")


def run_bls_search(
    lc_prepared: lk.LightCurve,
    period_min_days: float = 0.5,
    period_max_days: float = 20.0,
    duration_min_hours: float = 0.5,
    duration_max_hours: float = 10.0,
    n_periods: int = 2000,
    n_durations: int = 12,
    top_n: int = 5,
    unique_period_separation_fraction: float = 0.05,
    min_snr: float = 7.0,
    normalized: bool = True,
    compute_fap: bool = False,
    fap_iterations: int = 1000,
) -> list[BLSCandidate]:
    """Run Box Least Squares and return ranked transit candidates.

    Theory: BLS searches periodic box-shaped dips expected for transits by
    fitting a repeating box model over a period-duration grid and ranking peaks
    by detection power.
    """
    inputs = _prepare_bls_inputs(
        lc_prepared, period_min_days, period_max_days,
        duration_min_hours, duration_max_hours, n_periods, n_durations,
    )
    if inputs is None:
        return []

    time, flux, periods, durations = inputs.time, inputs.flux, inputs.periods, inputs.durations
    result = inputs.model.power(periods, durations)

    power = np.asarray(result.power, dtype=float)
    period = np.asarray(result.period, dtype=float)
    duration = np.asarray(result.duration, dtype=float)
    depth = np.asarray(result.depth, dtype=float)
    transit_time = np.asarray(result.transit_time, dtype=float)

    finite_power = np.where(np.isfinite(power))[0]
    if len(finite_power) == 0:
        return []

    median_power = float(np.nanmedian(power[finite_power]))
    mad_power = float(np.nanmedian(np.abs(power[finite_power] - median_power)))
    snr_scale = 1.4826 * mad_power if mad_power > 0 else 1e-30
    median_flux = float(np.nanmedian(flux)) if not normalized else 1.0
    span_days = float(np.nanmax(time) - np.nanmin(time))
    ranked_indices = finite_power[np.argsort(power[finite_power])[::-1]]

    picked: list[BLSCandidate] = []
    for idx in ranked_indices:
        p = float(period[idx])
        if p <= 0 or not np.isfinite(p):
            continue
        if not _unique_period(picked, p, unique_period_separation_fraction):
            continue
        candidate_snr = (float(power[idx]) - median_power) / snr_scale
        if candidate_snr < min_snr:
            continue
        d = float(depth[idx])
        t_count = span_days / p
        if normalized:
            d_ppm = d * 1_000_000.0
        else:
            d_ppm = (d / median_flux) * 1_000_000.0 if abs(median_flux) > 1e-12 else float("nan")
        picked.append(
            BLSCandidate(
                rank=len(picked) + 1,
                period_days=p,
                duration_hours=float(duration[idx]) * 24.0,
                depth=d,
                depth_ppm=d_ppm,
                power=float(power[idx]),
                transit_time=float(transit_time[idx]),
                transit_count_estimate=float(t_count),
                snr=candidate_snr,
                fap=float("nan"),
            )
        )
        if len(picked) >= max(1, int(top_n)):
            break

    if compute_fap and picked:
        fap_candidates = []
        for c in picked:
            fap_val = _bootstrap_fap(
                time, flux, c.power, periods, durations, n_iterations=fap_iterations,
            )
            fap_candidates.append(
                BLSCandidate(
                    rank=c.rank, period_days=c.period_days,
                    duration_hours=c.duration_hours, depth=c.depth,
                    depth_ppm=c.depth_ppm, power=c.power,
                    transit_time=c.transit_time,
                    transit_count_estimate=c.transit_count_estimate,
                    snr=c.snr, fap=fap_val,
                )
            )
        picked = fap_candidates

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
    inputs = _prepare_bls_inputs(
        lc_prepared, period_min_days, period_max_days,
        duration_min_hours, duration_max_hours, n_periods, n_durations,
    )
    if inputs is None:
        return np.asarray([], dtype=float), np.asarray([], dtype=float)

    result = inputs.model.power(inputs.periods, inputs.durations)
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

    inputs = _prepare_bls_inputs(
        lc_prepared, period_min_days, period_max_days,
        duration_min_hours, duration_max_hours, n_periods, n_durations,
    )
    if inputs is None:
        return list(candidates)

    refined: list[BLSCandidate] = []
    for candidate in candidates:
        window = max(1e-4, candidate.period_days * float(window_fraction))
        local_min = max(float(period_min_days), candidate.period_days - window)
        local_max = min(float(period_max_days), candidate.period_days + window)
        if local_max <= local_min:
            refined.append(candidate)
            continue

        local_periods = np.geomspace(local_min, local_max, num=max(400, int(n_periods)))
        try:
            result = inputs.model.power(local_periods, inputs.durations)
        except Exception:
            refined.append(candidate)
            continue

        power = np.asarray(result.power, dtype=float)
        finite_mask = np.isfinite(power)
        if not np.any(finite_mask):
            refined.append(candidate)
            continue

        best_idx = int(np.argmax(np.where(finite_mask, power, -np.inf)))
        median_p = float(np.nanmedian(power[finite_mask]))
        mad_p = float(np.nanmedian(np.abs(power[finite_mask] - median_p)))
        snr_scale = 1.4826 * mad_p if mad_p > 0 else 1e-30
        best_snr = (float(power[best_idx]) - median_p) / snr_scale

        d = float(result.depth[best_idx])
        p = float(result.period[best_idx])
        span = float(np.nanmax(inputs.time) - np.nanmin(inputs.time))
        refined.append(
            BLSCandidate(
                rank=candidate.rank,
                period_days=p,
                duration_hours=float(result.duration[best_idx]) * 24.0,
                depth=d,
                depth_ppm=d * 1_000_000.0,
                power=float(power[best_idx]),
                transit_time=float(result.transit_time[best_idx]),
                transit_count_estimate=span / p if p > 0 else 0.0,
                snr=best_snr,
                fap=candidate.fap,
                iteration=candidate.iteration,
            )
        )
    return refined


def _build_transit_mask(
    time: np.ndarray,
    candidates: list[BLSCandidate],
    padding_factor: float,
) -> np.ndarray:
    """Build boolean mask marking in-transit points for all candidates."""
    mask = np.zeros(len(time), dtype=bool)
    if not candidates:
        return mask
    t_min, t_max = float(np.nanmin(time)), float(np.nanmax(time))
    for cand in candidates:
        duration_days = cand.duration_hours / 24.0
        half_width = 0.5 * duration_days * padding_factor
        period = cand.period_days
        if period <= 0:
            continue
        n_start = int(np.floor((t_min - cand.transit_time) / period)) - 1
        n_end = int(np.ceil((t_max - cand.transit_time) / period)) + 1
        for n in range(n_start, n_end + 1):
            epoch = cand.transit_time + n * period
            mask |= np.abs(time - epoch) < half_width
    return mask


def _cross_iteration_unique(
    candidate: BLSCandidate,
    accepted: list[BLSCandidate],
    threshold: float = 0.01,
) -> bool:
    """Return True if candidate period is >threshold fractional separation from all accepted."""
    for acc in accepted:
        denom = max(acc.period_days, candidate.period_days, 1e-12)
        if abs(acc.period_days - candidate.period_days) / denom < threshold:
            return False
    return True


def run_iterative_bls_search(
    lc_prepared: lk.LightCurve,
    config: "BLSConfig",  # noqa: F821
    *,
    normalized: bool = True,
    preprocess_config: "PreprocessConfig | None" = None,  # noqa: F821
    lc: lk.LightCurve | None = None,
    stellar_params: "StellarParams | None" = None,
) -> list[BLSCandidate]:
    """Run iterative BLS/TLS with transit masking between passes.

    Each pass masks the top-power peak(s) and re-searches, accumulating all
    peaks across passes. Masking is deliberately decoupled from vetting: a
    dominant systematic must be masked to reveal weaker real signals beneath
    it. Vetting is applied by the caller on the full returned list.

    Stops early when a pass returns no non-duplicate candidates or when
    fewer than 100 valid points remain.
    """
    time = np.asarray(lc_prepared.time.value, dtype=float)
    flux = np.asarray(lc_prepared.flux.value, dtype=float).copy()

    all_candidates: list[BLSCandidate] = []
    accepted_for_masking: list[BLSCandidate] = []

    for iteration in range(config.iterative_passes):
        n_valid = int(np.sum(np.isfinite(flux)))
        if n_valid < 100:
            LOGGER.warning(
                "Iterative BLS: only %d valid points remain, stopping at iteration %d.",
                n_valid, iteration,
            )
            break

        iter_lc = lk.LightCurve(time=lc_prepared.time, flux=flux)
        if getattr(config, "search_method", "bls") == "tls":
            from exohunt.tls import run_tls_search
            candidates = run_tls_search(
                lc_prepared=iter_lc,
                period_min_days=config.period_min_days,
                period_max_days=config.period_max_days,
                top_n=config.top_n,
                min_sde=config.min_snr,
                unique_period_separation_fraction=config.unique_period_separation_fraction,
                stellar_params=stellar_params,
            )
        else:
            candidates = run_bls_search(
                lc_prepared=iter_lc,
                period_min_days=config.period_min_days,
                period_max_days=config.period_max_days,
                duration_min_hours=config.duration_min_hours,
                duration_max_hours=config.duration_max_hours,
                n_periods=config.n_periods,
                n_durations=config.n_durations,
                top_n=config.top_n,
                unique_period_separation_fraction=config.unique_period_separation_fraction,
                min_snr=config.min_snr,
                normalized=normalized,
            )

        # Data-sanity filter: drop candidates whose mask would cover more
        # than half the orbit. Not vetting — a transit this wide isn't a
        # transit and masking it would erase most of the LC.
        candidates = [
            c for c in candidates
            if (c.duration_hours / 24.0 * config.transit_mask_padding_factor
                / c.period_days) <= 0.5
        ]
        if not candidates:
            break

        # Tag every candidate from this pass with the iteration index.
        tagged_all = [
            BLSCandidate(
                rank=c.rank, period_days=c.period_days,
                duration_hours=c.duration_hours, depth=c.depth,
                depth_ppm=c.depth_ppm, power=c.power,
                transit_time=c.transit_time,
                transit_count_estimate=c.transit_count_estimate,
                snr=c.snr, fap=c.fap, iteration=iteration,
            )
            for c in candidates
        ]

        # Pick top-power peaks for masking (no vetting — see docstring).
        # Skip periods already masked in prior iterations.
        masking_candidates = [
            c for c in tagged_all[: config.iterative_top_n]
            if _cross_iteration_unique(c, accepted_for_masking)
        ]
        if not masking_candidates:
            break

        all_candidates.extend(tagged_all)
        accepted_for_masking.extend(masking_candidates)

        cumulative_mask = _build_transit_mask(
            time, accepted_for_masking, config.transit_mask_padding_factor,
        )

        if (
            preprocess_config is not None
            and preprocess_config.iterative_flatten
            and lc is not None
        ):
            from exohunt.preprocess import prepare_lightcurve

            reflattened, _ = prepare_lightcurve(
                lc, transit_mask=cumulative_mask,
            )
            flux = np.asarray(reflattened.flux.value, dtype=float).copy()
        else:
            flux = np.asarray(lc_prepared.flux.value, dtype=float).copy()
            flux[cumulative_mask] = np.nan

    return all_candidates
