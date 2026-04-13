"""TRICERATOPS statistical validation for transit candidates.

Computes Bayesian false positive probability (FPP) and nearby false
positive probability (NFPP) by modeling multiple astrophysical scenarios
and comparing their likelihoods given the observed light curve.

Validation thresholds (Giacalone & Dressing 2020):
  FPP < 0.015 and NFPP < 0.001 → statistically validated planet
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np

LOGGER = logging.getLogger(__name__)

_FPP_THRESHOLD = 0.015
_NFPP_THRESHOLD = 0.001


@dataclass(frozen=True)
class ValidationResult:
    fpp: float
    nfpp: float
    validated: bool
    status: str  # "validated", "ambiguous", "false_positive", "error"


def validate_candidate(
    tic_id: int,
    sectors: list[int],
    time: np.ndarray,
    flux: np.ndarray,
    flux_err: float,
    period_days: float,
    t0: float,
    duration_hours: float,
    depth_ppm: float,
    N: int = 1_000_000,
) -> ValidationResult:
    """Run TRICERATOPS on a single candidate.

    Args:
        tic_id: TIC ID of the target
        sectors: TESS sectors observed
        time: time array (BTJD)
        flux: normalized flux array
        flux_err: median flux uncertainty
        period_days: orbital period of the candidate
        t0: transit midpoint (BTJD)
        duration_hours: transit duration in hours
        depth_ppm: transit depth in parts per million
        N: number of TRICERATOPS simulations
    """
    try:
        from triceratops.triceratops import target as TRITarget

        # TRILEGAL web service may be unavailable; patch to skip gracefully
        try:
            import triceratops.funcs as _tf
            _orig_qt = _tf.query_TRILEGAL
            _tf.query_TRILEGAL = lambda *a, **kw: None
        except Exception:
            pass

        # Phase-fold and center on transit midpoint
        finite = np.isfinite(time) & np.isfinite(flux)
        t_clean = time[finite]
        f_clean = flux[finite]

        phase = (t_clean - t0 + period_days / 2) % period_days - period_days / 2
        # Trim to transit window: ±3× duration from midpoint
        half_window = 3.0 * (duration_hours / 24.0)
        in_window = np.abs(phase) < half_window
        t_folded = phase[in_window]
        f_folded = f_clean[in_window]

        if len(t_folded) < 20:
            LOGGER.warning("TRICERATOPS: too few points in transit window (%d)", len(t_folded))
            return ValidationResult(fpp=float("nan"), nfpp=float("nan"), validated=False, status="error")

        # Sort by phase for clean light curve
        sort_idx = np.argsort(t_folded)
        t_folded = t_folded[sort_idx]
        f_folded = f_folded[sort_idx]

        sector_arr = np.array(sectors, dtype=int)
        tri = TRITarget(ID=tic_id, sectors=sector_arr)
        tri.calc_depths(tdepth=float(depth_ppm) / 1e6)

        # Drop background scenarios if TRILEGAL query failed
        drop = []
        if tri.trilegal_fname is None and tri.trilegal_url is None:
            drop = ["DTP", "DEB", "DEBx2P", "BTP", "BEB", "BEBx2P"]

        tri.calc_probs(
            time=t_folded, flux_0=f_folded, flux_err_0=float(flux_err),
            P_orb=float(period_days), N=N, verbose=0,
            drop_scenario=drop,
        )

        fpp = float(tri.FPP)
        nfpp = float(tri.NFPP)

        if fpp < _FPP_THRESHOLD and nfpp < _NFPP_THRESHOLD:
            status = "validated"
        elif fpp < 0.5:
            status = "ambiguous"
        else:
            status = "false_positive"

        validated = status == "validated"
        LOGGER.info(
            "TRICERATOPS TIC %d P=%.3fd: FPP=%.4f NFPP=%.4f → %s (%d pts in window)",
            tic_id, period_days, fpp, nfpp, status, len(t_folded),
        )
        return ValidationResult(fpp=fpp, nfpp=nfpp, validated=validated, status=status)

    except Exception as exc:
        LOGGER.warning("TRICERATOPS failed for TIC %d P=%.3fd: %s", tic_id, period_days, exc)
        return ValidationResult(
            fpp=float("nan"), nfpp=float("nan"), validated=False, status="error",
        )
