from __future__ import annotations

from dataclasses import dataclass

import lightkurve as lk
import numpy as np

from exohunt.bls import BLSCandidate


@dataclass(frozen=True)
class CandidateVettingResult:
    pass_min_transit_count: bool
    pass_odd_even_depth: bool
    pass_alias_harmonic: bool
    vetting_pass: bool
    transit_count_observed: int
    odd_depth_ppm: float
    even_depth_ppm: float
    odd_even_depth_mismatch_fraction: float
    alias_harmonic_with_rank: int
    vetting_reasons: str
    odd_even_status: str
    pass_secondary_eclipse: bool = True
    secondary_eclipse_depth_fraction: float = float("nan")
    pass_depth_consistency: bool = True
    depth_consistency_fraction: float = float("nan")


def _group_depth_ppm(
    time: np.ndarray,
    flux: np.ndarray,
    period_days: float,
    transit_time: float,
    duration_days: float,
    parity: int,
    min_unique_transits: int = 5,
) -> tuple[float, int]:
    if period_days <= 0 or duration_days <= 0 or len(time) == 0:
        return float("nan"), 0
    cycle = np.round((time - transit_time) / period_days).astype(int)
    dt = time - (transit_time + cycle * period_days)
    selected = (cycle % 2) == parity
    in_transit = selected & (np.abs(dt) <= 0.5 * duration_days)
    out_transit = selected & (np.abs(dt) >= duration_days)
    if int(np.count_nonzero(in_transit)) < 5 or int(np.count_nonzero(out_transit)) < 10:
        return float("nan"), 0
    observed_cycles = int(np.unique(cycle[in_transit]).size)
    if observed_cycles < min_unique_transits:
        return float("nan"), observed_cycles
    depth = float(np.nanmedian(flux[out_transit]) - np.nanmedian(flux[in_transit]))
    return depth * 1_000_000.0, observed_cycles


def _alias_harmonic_reference_rank(
    index: int,
    candidates: list[BLSCandidate],
    tolerance_fraction: float,
) -> int:
    if not candidates:
        return -1
    current = candidates[index]
    ratios = (0.5, 2.0, 1.0 / 3.0, 3.0, 2.0 / 3.0, 3.0 / 2.0)
    for j, other in enumerate(candidates):
        if j == index:
            continue
        if other.power < current.power:
            continue
        if other.period_days <= 0 or current.period_days <= 0:
            continue
        ratio = current.period_days / other.period_days
        for expected in ratios:
            if abs(ratio - expected) / expected <= tolerance_fraction:
                return int(other.rank)
    return -1


def _secondary_eclipse_check(
    time: np.ndarray,
    flux: np.ndarray,
    period_days: float,
    transit_time: float,
    duration_days: float,
) -> tuple[float, bool]:
    """Check for secondary eclipse at phase 0.5. Returns (depth_fraction, pass)."""
    if period_days <= 0 or duration_days <= 0 or len(time) < 20:
        return float("nan"), True
    phase = ((time - transit_time) / period_days) % 1.0
    half_dur_phase = 0.5 * duration_days / period_days
    # Primary: near phase 0
    in_primary = (phase < half_dur_phase) | (phase > 1.0 - half_dur_phase)
    # Secondary: near phase 0.5
    in_secondary = np.abs(phase - 0.5) < half_dur_phase
    out_of_transit = ~in_primary & ~in_secondary
    if np.count_nonzero(in_secondary) < 5 or np.count_nonzero(out_of_transit) < 10:
        return float("nan"), True
    if np.count_nonzero(in_primary) < 5:
        return float("nan"), True
    baseline = float(np.nanmedian(flux[out_of_transit]))
    primary_depth = baseline - float(np.nanmedian(flux[in_primary]))
    secondary_depth = baseline - float(np.nanmedian(flux[in_secondary]))
    if not np.isfinite(primary_depth) or primary_depth <= 0:
        return float("nan"), True
    fraction = secondary_depth / primary_depth if primary_depth > 0 else float("nan")
    return float(fraction), True  # pass determined by caller threshold


def _phase_fold_depth_consistency(
    time: np.ndarray,
    flux: np.ndarray,
    period_days: float,
    transit_time: float,
    duration_days: float,
) -> tuple[float, bool]:
    """Check transit depth consistency between first and second halves of data."""
    if period_days <= 0 or duration_days <= 0 or len(time) < 20:
        return float("nan"), True
    mid_time = 0.5 * (float(np.nanmin(time)) + float(np.nanmax(time)))
    phase = ((time - transit_time) / period_days) % 1.0
    half_dur_phase = 0.5 * duration_days / period_days
    in_transit = (phase < half_dur_phase) | (phase > 1.0 - half_dur_phase)
    out_transit = ~in_transit

    def _half_depth(mask: np.ndarray) -> float:
        it = in_transit & mask
        ot = out_transit & mask
        if np.count_nonzero(it) < 5 or np.count_nonzero(ot) < 10:
            return float("nan")
        return float(np.nanmedian(flux[ot]) - np.nanmedian(flux[it]))

    d1 = _half_depth(time < mid_time)
    d2 = _half_depth(time >= mid_time)
    if not np.isfinite(d1) or not np.isfinite(d2):
        return float("nan"), True
    denom = max(abs(d1), abs(d2), 1e-12)
    fraction = abs(d1 - d2) / denom
    return float(fraction), True  # pass determined by caller threshold


def vet_bls_candidates(
    lc_prepared: lk.LightCurve,
    candidates: list[BLSCandidate],
    min_transit_count: int = 2,
    odd_even_mismatch_max_fraction: float = 0.3,
    alias_tolerance_fraction: float = 0.02,
    secondary_eclipse_max_fraction: float = 0.3,
    depth_consistency_max_fraction: float = 0.5,
) -> dict[int, CandidateVettingResult]:
    time = np.asarray(lc_prepared.time.value, dtype=float)
    flux = np.asarray(lc_prepared.flux.value, dtype=float)
    finite = np.isfinite(time) & np.isfinite(flux)
    time = time[finite]
    flux = flux[finite]
    if len(time) == 0:
        return {}

    results: dict[int, CandidateVettingResult] = {}
    for idx, candidate in enumerate(candidates):
        duration_days = max(candidate.duration_hours / 24.0, 1e-6)
        odd_depth_ppm, odd_count = _group_depth_ppm(
            time=time,
            flux=flux,
            period_days=candidate.period_days,
            transit_time=candidate.transit_time,
            duration_days=duration_days,
            parity=1,
        )
        even_depth_ppm, even_count = _group_depth_ppm(
            time=time,
            flux=flux,
            period_days=candidate.period_days,
            transit_time=candidate.transit_time,
            duration_days=duration_days,
            parity=0,
        )
        observed_count = int(odd_count + even_count)
        pass_min_count = observed_count >= int(min_transit_count)

        mismatch_fraction = float("nan")
        pass_odd_even = False
        odd_even_status = "fail"
        # Estimate actual observed transits using data duty cycle.
        # With gapped TESS data (typical duty cycle 10-30%), the raw
        # cycle count is inflated because every cycle with any data
        # gets counted even when no transit was observed.
        _span = float(np.nanmax(time) - np.nanmin(time))
        if _span > 0 and len(time) > 1:
            _cadence_days = float(np.nanmedian(np.diff(time)))
            _duty_cycle = min(len(time) * _cadence_days / _span, 1.0)
        else:
            _duty_cycle = 0.0
        _est_real_transits_per_parity = (
            _duty_cycle * candidate.transit_count_estimate / 2.0
        )
        _min_parity_transits = 5
        _enough_odd_even = _est_real_transits_per_parity >= _min_parity_transits
        if (np.isfinite(odd_depth_ppm) and np.isfinite(even_depth_ppm)
                and _enough_odd_even):
            denom = max(abs(odd_depth_ppm), abs(even_depth_ppm), 1e-9)
            mismatch_fraction = abs(odd_depth_ppm - even_depth_ppm) / denom
            pass_odd_even = mismatch_fraction <= float(odd_even_mismatch_max_fraction)
            odd_even_status = "pass" if pass_odd_even else "fail"
        else:
            pass_odd_even = True
            odd_even_status = "inconclusive"

        alias_rank = _alias_harmonic_reference_rank(
            index=idx,
            candidates=candidates,
            tolerance_fraction=alias_tolerance_fraction,
        )
        pass_alias = alias_rank < 0

        sec_fraction, _ = _secondary_eclipse_check(
            time, flux, candidate.period_days, candidate.transit_time, duration_days,
        )
        if np.isfinite(sec_fraction):
            pass_secondary = sec_fraction <= float(secondary_eclipse_max_fraction)
        else:
            pass_secondary = True

        cons_fraction, _ = _phase_fold_depth_consistency(
            time, flux, candidate.period_days, candidate.transit_time, duration_days,
        )
        if np.isfinite(cons_fraction):
            pass_consistency = cons_fraction <= float(depth_consistency_max_fraction)
        else:
            pass_consistency = True

        reasons = []
        if not pass_min_count:
            reasons.append(f"min_transit_count<{min_transit_count}")
        if not pass_odd_even:
            reasons.append("odd_even_depth_mismatch")
        elif odd_even_status == "inconclusive":
            reasons.append("odd_even_inconclusive")
        if not pass_alias:
            reasons.append(f"alias_or_harmonic_of_rank_{alias_rank}")
        if not pass_secondary:
            reasons.append("secondary_eclipse")
        if not pass_consistency:
            reasons.append("depth_inconsistent")
        if not reasons:
            reasons.append("pass")

        overall = (
            pass_min_count and pass_odd_even and pass_alias
            and pass_secondary and pass_consistency
        )
        results[int(candidate.rank)] = CandidateVettingResult(
            pass_min_transit_count=pass_min_count,
            pass_odd_even_depth=pass_odd_even,
            pass_alias_harmonic=pass_alias,
            pass_secondary_eclipse=pass_secondary,
            pass_depth_consistency=pass_consistency,
            vetting_pass=overall,
            transit_count_observed=observed_count,
            odd_depth_ppm=odd_depth_ppm,
            even_depth_ppm=even_depth_ppm,
            odd_even_depth_mismatch_fraction=mismatch_fraction,
            secondary_eclipse_depth_fraction=float(sec_fraction),
            depth_consistency_fraction=float(cons_fraction),
            alias_harmonic_with_rank=int(alias_rank),
            vetting_reasons=";".join(reasons),
            odd_even_status=odd_even_status,
        )
    return results
