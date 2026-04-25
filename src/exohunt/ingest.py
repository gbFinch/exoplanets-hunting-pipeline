from __future__ import annotations

from typing import Any

import lightkurve as lk
import numpy as np

from exohunt.models import LightCurveSegment


def _parse_authors(value: str | None) -> set[str] | None:
    if not value:
        return None
    items = [chunk.strip().upper() for chunk in value.split(",") if chunk.strip()]
    if not items:
        return None
    return set(items)


def _build_segment_id(index: int, lc: lk.LightCurve) -> str:
    sector = int(lc.meta.get("SECTOR", -1))
    return f"sector_{sector:04d}__idx_{index:03d}"


def _extract_segments(
    lcs: Any,
    selected_authors: set[str] | None,
) -> list[LightCurveSegment]:
    segments: list[LightCurveSegment] = []
    for idx, lc in enumerate(lcs):
        sector = int(lc.meta.get("SECTOR", -1))
        author = str(lc.meta.get("AUTHOR", "UNKNOWN")).upper()
        cadence = float(lc.meta.get("TIMEDEL", np.nan))
        if selected_authors is not None and author not in selected_authors:
            continue
        segments.append(
            LightCurveSegment(
                segment_id=_build_segment_id(idx, lc),
                sector=sector,
                author=author,
                cadence=cadence,
                lc=lc.remove_nans(),
            )
        )
    return segments


def _stitch_segments(lightcurves: list[lk.LightCurve]) -> tuple[lk.LightCurve, list[float]]:
    if not lightcurves:
        raise RuntimeError("No light-curve segments available to stitch.")
    ordered = sorted(lightcurves, key=lambda item: float(np.nanmin(item.time.value)))
    time_parts = []
    flux_parts = []
    boundaries: list[float] = []
    for idx, lc in enumerate(ordered):
        time_values = np.asarray(lc.time.value, dtype=float)
        flux_values = np.asarray(lc.flux.value, dtype=float)
        if time_values.size == 0:
            continue
        if idx > 0:
            boundaries.append(float(time_values[0]))
        time_parts.append(time_values)
        flux_parts.append(flux_values)
    if not time_parts:
        raise RuntimeError("All stitched segments were empty after preprocessing.")
    stitched = lk.LightCurve(time=np.concatenate(time_parts), flux=np.concatenate(flux_parts))
    return stitched, boundaries
