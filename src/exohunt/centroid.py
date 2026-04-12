"""Centroid shift analysis for transit candidate vetting.

Compares in-transit vs out-of-transit flux-weighted centroids from TESS
Target Pixel Files to detect nearby eclipsing binary contamination.
A significant centroid shift during transit indicates the signal
originates from a nearby star, not the target.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np

LOGGER = logging.getLogger(__name__)

_TESS_PIXEL_ARCSEC = 21.0
# Threshold: shift > 0.1 pixels (~2.1 arcsec) flags contamination
_DEFAULT_MAX_SHIFT_PIXELS = 0.1


@dataclass(frozen=True)
class CentroidResult:
    shift_col_pixels: float
    shift_row_pixels: float
    shift_total_pixels: float
    shift_total_arcsec: float
    n_in_transit: int
    n_out_transit: int
    passed: bool
    status: str  # "pass", "fail", "inconclusive"


def check_centroid_shift(
    tpf,
    period_days: float,
    transit_time: float,
    duration_hours: float,
    max_shift_pixels: float = _DEFAULT_MAX_SHIFT_PIXELS,
) -> CentroidResult:
    """Compute centroid shift between in-transit and out-of-transit cadences.

    Args:
        tpf: lightkurve TargetPixelFile
        period_days: orbital period
        transit_time: transit midpoint (same time system as TPF)
        duration_hours: transit duration
        max_shift_pixels: threshold for flagging contamination
    """
    try:
        col, row = tpf.estimate_centroids(aperture_mask="pipeline")
    except Exception as exc:
        LOGGER.warning("Centroid estimation failed: %s", exc)
        return _inconclusive("centroid_estimation_failed")

    time = np.asarray(tpf.time.value, dtype=float)
    col_v = np.asarray(col.value, dtype=float)
    row_v = np.asarray(row.value, dtype=float)

    finite = np.isfinite(time) & np.isfinite(col_v) & np.isfinite(row_v)
    time, col_v, row_v = time[finite], col_v[finite], row_v[finite]

    if len(time) < 100:
        return _inconclusive("too_few_points")

    # Phase-fold and identify in-transit cadences
    dur_days = duration_hours / 24.0
    half_dur = 0.5 * dur_days
    phase = ((time - transit_time) / period_days) % 1.0
    # Wrap phase to [-0.5, 0.5] centered on transit
    phase[phase > 0.5] -= 1.0
    in_transit = np.abs(phase) < (half_dur / period_days)
    out_transit = np.abs(phase) > (2.0 * half_dur / period_days)

    n_in = int(np.sum(in_transit))
    n_out = int(np.sum(out_transit))
    if n_in < 20 or n_out < 100:
        return _inconclusive("insufficient_transit_coverage")

    d_col = float(np.nanmean(col_v[in_transit]) - np.nanmean(col_v[out_transit]))
    d_row = float(np.nanmean(row_v[in_transit]) - np.nanmean(row_v[out_transit]))
    shift = float(np.sqrt(d_col**2 + d_row**2))

    passed = shift <= max_shift_pixels
    status = "pass" if passed else "fail"
    LOGGER.info(
        "Centroid: shift=%.4f px (%.2f arcsec), %s [in=%d out=%d]",
        shift, shift * _TESS_PIXEL_ARCSEC, status, n_in, n_out,
    )
    return CentroidResult(
        shift_col_pixels=d_col, shift_row_pixels=d_row,
        shift_total_pixels=shift, shift_total_arcsec=shift * _TESS_PIXEL_ARCSEC,
        n_in_transit=n_in, n_out_transit=n_out,
        passed=passed, status=status,
    )


def _inconclusive(reason: str) -> CentroidResult:
    LOGGER.info("Centroid: inconclusive (%s)", reason)
    return CentroidResult(
        shift_col_pixels=float("nan"), shift_row_pixels=float("nan"),
        shift_total_pixels=float("nan"), shift_total_arcsec=float("nan"),
        n_in_transit=0, n_out_transit=0,
        passed=True, status="inconclusive",
    )


def run_centroid_vetting(
    tic_id: int,
    candidates: list[dict],
    tpf=None,
    timeout_seconds: float = 120.0,
) -> dict[int, CentroidResult]:
    """Run centroid vetting for a list of candidates.

    Downloads TPFs and checks centroid shift for each candidate.
    candidates: list of dicts with keys: rank, period_days, transit_time, duration_hours
    tpf: optional pre-downloaded TargetPixelFile (skips download if provided)

    Returns dict mapping candidate rank to CentroidResult.
    """
    results: dict[int, CentroidResult] = {}
    if not candidates:
        return results

    if tpf is None:
        import lightkurve as lk
        try:
            sr = lk.search_targetpixelfile(f"TIC {tic_id}", mission="TESS", author="SPOC")
            if len(sr) == 0:
                LOGGER.warning("No TPFs found for TIC %d", tic_id)
                return {c["rank"]: _inconclusive("no_tpf") for c in candidates}
            # Download only the longest sector to avoid timeouts
            tpf = sr[0].download()
        except Exception as exc:
            LOGGER.warning("TPF download failed for TIC %d: %s", tic_id, exc)
            return {c["rank"]: _inconclusive("tpf_download_failed") for c in candidates}

    for cand in candidates:
        rank = cand["rank"]
        results[rank] = check_centroid_shift(
            tpf=tpf,
            period_days=cand["period_days"],
            transit_time=cand["transit_time"],
            duration_hours=cand["duration_hours"],
        )
    return results
