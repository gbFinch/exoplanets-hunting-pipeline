"""Stellar parameter query for TLS integration.

Queries TIC catalog for stellar radius, mass, and computes quadratic limb
darkening coefficients using TLS's built-in Claret tables.  Falls back to
solar defaults when catalog data is unavailable.
"""
from __future__ import annotations

import logging
import math
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

LOGGER = logging.getLogger(__name__)

SOLAR_LIMB_DARKENING: tuple[float, float] = (0.4804, 0.1867)
_SOLAR_U = SOLAR_LIMB_DARKENING


@dataclass(frozen=True)
class StellarParams:
    R_star: float
    R_star_min: float
    R_star_max: float
    M_star: float
    M_star_min: float
    M_star_max: float
    limb_darkening: tuple[float, float]
    used_defaults: bool


def _solar_defaults() -> StellarParams:
    return StellarParams(
        R_star=1.0, R_star_min=0.13, R_star_max=3.5,
        M_star=1.0, M_star_min=0.1, M_star_max=1.0,
        limb_darkening=SOLAR_LIMB_DARKENING, used_defaults=True,
    )


def _ok(v: object) -> bool:
    """Return True if v is a finite positive number."""
    if v is None:
        return False
    try:
        f = float(v)
    except (TypeError, ValueError):
        return False
    return math.isfinite(f) and f > 0


def query_stellar_params(tic_id: int, timeout_seconds: float = 30.0) -> StellarParams:
    """Query TIC via TLS catalog_info for stellar params + limb darkening.

    Uses transitleastsquares.catalog_info which queries TIC for R_star,
    M_star and their uncertainties, then looks up quadratic limb darkening
    coefficients from the Claret (2017) TESS tables using Teff and logg.

    Falls back to solar defaults on any failure.
    """
    def _query() -> StellarParams:
        from transitleastsquares import catalog_info

        (a, b), mass, mass_min, mass_max, radius, radius_min, radius_max = catalog_info(
            TIC_ID=tic_id
        )

        if not _ok(radius) or not _ok(mass):
            LOGGER.warning("TIC %d: missing R_star or M_star, using defaults.", tic_id)
            return _solar_defaults()

        R, M = float(radius), float(mass)
        # TLS catalog_info returns uncertainties, not absolute bounds.
        # radius_min/max and mass_min/max are ± error bars.
        # Convert to absolute bounds for TLS power().
        R_err = float(radius_max) if _ok(radius_max) else R * 0.2
        M_err = float(mass_max) if _ok(mass_max) else M * 0.2
        R_min = max(0.01, R - R_err)
        R_max = R + R_err
        M_min = max(0.01, M - M_err)
        M_max = M + M_err
        u = (float(a), float(b)) if _ok(a) and _ok(b) else _SOLAR_U

        LOGGER.info(
            "TIC %d: R=%.3f [%.3f,%.3f] M=%.3f [%.3f,%.3f] u=(%.4f,%.4f)",
            tic_id, R, R_min, R_max, M, M_min, M_max, u[0], u[1],
        )
        return StellarParams(
            R_star=R, R_star_min=R_min, R_star_max=R_max,
            M_star=M, M_star_min=M_min, M_star_max=M_max,
            limb_darkening=u, used_defaults=False,
        )

    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(_query).result(timeout=timeout_seconds)
    except Exception as exc:
        LOGGER.warning("Stellar query failed for TIC %d: %s. Using defaults.", tic_id, exc)
        return _solar_defaults()
