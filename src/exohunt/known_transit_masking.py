from __future__ import annotations

import logging

import lightkurve as lk
import numpy as np

from exohunt.ephemeris import KnownPlanetEphemeris
from exohunt.stellar import SOLAR_LIMB_DARKENING, StellarParams

LOGGER = logging.getLogger(__name__)


def mask_known_transits(
    lc_prepared: lk.LightCurve,
    known_ephemerides: list[KnownPlanetEphemeris],
    stellar_params: StellarParams | None = None,
) -> lk.LightCurve:
    """Subtract or mask known planet transits from a light curve.

    For confirmed planets with full orbital parameters (Rp/Rs, a/Rs,
    impact parameter), uses batman Mandel-Agol model subtraction.
    Falls back to NaN masking for TOI candidates or when batman fails.

    Returns a new LightCurve with known transits removed.
    """
    if not known_ephemerides:
        return lc_prepared

    time_arr = np.asarray(lc_prepared.time.value, dtype=float)
    flux_arr = np.asarray(lc_prepared.flux.value, dtype=float).copy()
    n_subtracted = 0
    n_masked = 0
    u1, u2 = SOLAR_LIMB_DARKENING
    if stellar_params and not stellar_params.used_defaults:
        u1, u2 = stellar_params.limb_darkening

    for eph in known_ephemerides:
        t0_btjd = eph.t0_bjd - 2457000.0
        period = eph.period_days
        if period <= 0:
            continue

        # Batman model subtraction for confirmed planets with full params
        if eph.confirmed and eph.rp_rs and eph.a_rs and eph.impact_param is not None:
            try:
                import batman
                params = batman.TransitParams()
                params.t0 = t0_btjd
                params.per = period
                params.rp = eph.rp_rs
                params.a = eph.a_rs
                params.inc = np.degrees(np.arccos(eph.impact_param / eph.a_rs))
                params.ecc = 0.0
                params.w = 90.0
                params.u = [u1, u2]
                params.limb_dark = "quadratic"
                m = batman.TransitModel(params, time_arr)
                model_flux = m.light_curve(params)
                finite = np.isfinite(flux_arr)
                flux_arr[finite] = flux_arr[finite] / model_flux[finite]
                n_subtracted += 1
                LOGGER.info("  Batman subtraction: %s P=%.3fd (Rp/Rs=%.4f)", eph.name, period, eph.rp_rs)
                continue
            except Exception as exc:
                LOGGER.warning("  Batman failed for %s, falling back to NaN mask: %s", eph.name, exc)

        # NaN masking fallback for TOI candidates or failed batman
        half_w = 0.5 * (eph.duration_hours / 24.0) * 1.5
        t_min, t_max = float(np.nanmin(time_arr)), float(np.nanmax(time_arr))
        n_start = int(np.floor((t_min - t0_btjd) / period)) - 1
        n_end = int(np.ceil((t_max - t0_btjd) / period)) + 1
        for n in range(n_start, n_end + 1):
            epoch = t0_btjd + n * period
            hit = np.abs(time_arr - epoch) < half_w
            n_masked += int(np.sum(hit & np.isfinite(flux_arr)))
            flux_arr[hit] = np.nan

    LOGGER.info(
        "Pre-masking: %d batman-subtracted, %d NaN-masked cadences, %d planet(s): %s",
        n_subtracted, n_masked, len(known_ephemerides),
        ", ".join(f"{e.name} P={e.period_days:.2f}d" for e in known_ephemerides),
    )
    return lk.LightCurve(time=lc_prepared.time, flux=flux_arr)
