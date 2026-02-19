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
