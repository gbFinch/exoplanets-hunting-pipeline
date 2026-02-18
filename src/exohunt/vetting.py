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


def _group_depth_ppm(
    time: np.ndarray,
    flux: np.ndarray,
    period_days: float,
    transit_time: float,
    duration_days: float,
    parity: int,
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
    depth = float(np.nanmedian(flux[out_transit]) - np.nanmedian(flux[in_transit]))
    observed_cycles = int(np.unique(cycle[in_transit]).size)
    return depth * 1_000_000.0, observed_cycles


def _alias_harmonic_reference_rank(
    index: int,
    candidates: list[BLSCandidate],
    tolerance_fraction: float,
) -> int:
    if not candidates:
        return -1
    current = candidates[index]
    ratios = (0.5, 2.0, 1.0 / 3.0, 3.0)
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


def vet_bls_candidates(
    lc_prepared: lk.LightCurve,
    candidates: list[BLSCandidate],
    min_transit_count: int = 2,
    odd_even_mismatch_max_fraction: float = 0.3,
    alias_tolerance_fraction: float = 0.02,
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
        if np.isfinite(odd_depth_ppm) and np.isfinite(even_depth_ppm):
            denom = max(abs(odd_depth_ppm), abs(even_depth_ppm), 1e-9)
            mismatch_fraction = abs(odd_depth_ppm - even_depth_ppm) / denom
            pass_odd_even = mismatch_fraction <= float(odd_even_mismatch_max_fraction)

        alias_rank = _alias_harmonic_reference_rank(
            index=idx,
            candidates=candidates,
            tolerance_fraction=alias_tolerance_fraction,
        )
        pass_alias = alias_rank < 0

        reasons = []
        if not pass_min_count:
            reasons.append(f"min_transit_count<{min_transit_count}")
        if not pass_odd_even:
            reasons.append("odd_even_depth_mismatch")
        if not pass_alias:
            reasons.append(f"alias_or_harmonic_of_rank_{alias_rank}")
        if not reasons:
            reasons.append("pass")

        overall = pass_min_count and pass_odd_even and pass_alias
        results[int(candidate.rank)] = CandidateVettingResult(
            pass_min_transit_count=pass_min_count,
            pass_odd_even_depth=pass_odd_even,
            pass_alias_harmonic=pass_alias,
            vetting_pass=overall,
            transit_count_observed=observed_count,
            odd_depth_ppm=odd_depth_ppm,
            even_depth_ppm=even_depth_ppm,
            odd_even_depth_mismatch_fraction=mismatch_fraction,
            alias_harmonic_with_rank=int(alias_rank),
            vetting_reasons=";".join(reasons),
        )
    return results
