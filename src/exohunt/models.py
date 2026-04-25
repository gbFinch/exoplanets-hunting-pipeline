from __future__ import annotations

from dataclasses import dataclass

import lightkurve as lk


def parse_tic_id(target: str) -> int:
    """Extract numeric TIC ID from a target string like 'TIC 261136679'."""
    cleaned = target.replace("TIC", "").strip()
    if not cleaned.isdigit():
        raise ValueError(f"Cannot parse TIC ID from: {target!r}")
    return int(cleaned)


@dataclass(frozen=True)
class LightCurveSegment:
    segment_id: str
    sector: int
    author: str
    cadence: float
    lc: lk.LightCurve

from pathlib import Path

from exohunt.bls import BLSCandidate
from exohunt.vetting import CandidateVettingResult


@dataclass(frozen=True)
class IngestResult:
    """Output of the ingest stage."""
    lc: lk.LightCurve
    lc_prepared: lk.LightCurve
    boundaries: list[float]
    data_source: str
    raw_cache_path: Path
    prepared_cache_path: Path
    prepared_segments_for_bls: list[LightCurveSegment]
    raw_segments_for_plot: list[LightCurveSegment]
    prepared_segments_for_plot: list[LightCurveSegment]
    tpf: object | None = None  # TargetPixelFile for centroid vetting


@dataclass(frozen=True)
class SearchResult:
    """Output of the search + output stage."""
    bls_candidates: list[BLSCandidate]
    candidate_output_key: str | None
    candidate_csv_paths: list[Path]
    candidate_json_paths: list[Path]
    diagnostic_assets: list[tuple[Path, Path]]
    stitched_vetting_by_rank: dict[int, CandidateVettingResult]


@dataclass(frozen=True)
class PlotResult:
    """Output of the plotting stage."""
    output_paths: list[Path]
    interactive_paths: list[Path]
