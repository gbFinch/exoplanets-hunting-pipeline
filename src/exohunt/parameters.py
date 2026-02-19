from __future__ import annotations

from dataclasses import dataclass
import math

from exohunt.bls import BLSCandidate


_G_SI = 6.67430e-11
_DEFAULT_STELLAR_DENSITY_KG_M3 = 1408.0
_R_SUN_IN_R_EARTH = 109.076


@dataclass(frozen=True)
class CandidateParameterEstimate:
    radius_ratio_rp_over_rs: float
    radius_earth_radii_solar_assumption: float
    duration_expected_hours_central_solar_density: float
    duration_ratio_observed_to_expected: float
    pass_duration_plausibility: bool
    parameter_assumptions: str
    parameter_uncertainty_caveats: str


def _expected_central_duration_hours(
    period_days: float,
    stellar_density_kg_m3: float,
) -> float:
    if not math.isfinite(period_days) or period_days <= 0:
        return float("nan")
    if not math.isfinite(stellar_density_kg_m3) or stellar_density_kg_m3 <= 0:
        return float("nan")

    period_seconds = period_days * 86400.0
    a_over_rstar = (
        (_G_SI * stellar_density_kg_m3 * (period_seconds**2.0)) / (3.0 * math.pi)
    ) ** (1.0 / 3.0)
    if not math.isfinite(a_over_rstar) or a_over_rstar <= 1.0:
        return float("nan")

    argument = max(-1.0, min(1.0, 1.0 / a_over_rstar))
    duration_days = (period_days / math.pi) * math.asin(argument)
    return duration_days * 24.0


def estimate_candidate_parameters(
    candidates: list[BLSCandidate],
    stellar_density_kg_m3: float = _DEFAULT_STELLAR_DENSITY_KG_M3,
    duration_ratio_min: float = 0.05,
    duration_ratio_max: float = 1.8,
) -> dict[int, CandidateParameterEstimate]:
    """Estimate first-pass geometric parameters for BLS candidates.

    Theory:
    1) Transit depth gives radius ratio via depth ~= (Rp/Rs)^2 for small planets.
    2) A circular, central-transit reference predicts duration from period and
       assumed stellar density using Kepler's third law.
    3) A broad duration-ratio gate flags candidates whose fitted duration is
       inconsistent with that baseline expectation.
    """
    assumptions = (
        "depth~(Rp/Rs)^2; no flux dilution; solar-like host density "
        f"{stellar_density_kg_m3:.1f} kg/m^3; circular-orbit, central-transit "
        "duration baseline."
    )
    caveats = (
        "Preliminary estimate only: depth and duration are BLS box-fit values and "
        "can shift with detrending/noise. Unknown stellar properties, impact "
        "parameter, and eccentricity can dominate true uncertainty."
    )

    estimates: dict[int, CandidateParameterEstimate] = {}
    for candidate in candidates:
        depth = float(candidate.depth)
        if not math.isfinite(depth):
            depth = float("nan")
        depth_non_negative = max(0.0, depth) if math.isfinite(depth) else float("nan")
        if math.isfinite(depth_non_negative):
            radius_ratio = math.sqrt(depth_non_negative)
            radius_earth = radius_ratio * _R_SUN_IN_R_EARTH
        else:
            radius_ratio = float("nan")
            radius_earth = float("nan")

        expected_duration_hours = _expected_central_duration_hours(
            period_days=float(candidate.period_days),
            stellar_density_kg_m3=float(stellar_density_kg_m3),
        )
        observed_duration_hours = float(candidate.duration_hours)
        if (
            math.isfinite(expected_duration_hours)
            and expected_duration_hours > 0.0
            and math.isfinite(observed_duration_hours)
            and observed_duration_hours > 0.0
        ):
            duration_ratio = observed_duration_hours / expected_duration_hours
            pass_duration = duration_ratio_min <= duration_ratio <= duration_ratio_max
        else:
            duration_ratio = float("nan")
            pass_duration = False

        estimates[int(candidate.rank)] = CandidateParameterEstimate(
            radius_ratio_rp_over_rs=float(radius_ratio),
            radius_earth_radii_solar_assumption=float(radius_earth),
            duration_expected_hours_central_solar_density=float(expected_duration_hours),
            duration_ratio_observed_to_expected=float(duration_ratio),
            pass_duration_plausibility=bool(pass_duration),
            parameter_assumptions=assumptions,
            parameter_uncertainty_caveats=caveats,
        )
    return estimates
