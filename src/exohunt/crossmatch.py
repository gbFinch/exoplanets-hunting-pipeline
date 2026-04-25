"""Cross-reference candidates against known exoplanet databases."""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from exohunt.ephemeris import _tap_query
from exohunt.models import parse_tic_id

LOGGER = logging.getLogger(__name__)

_PERIOD_MATCH_FRAC = 0.03  # 3% period match threshold
_CROSSMATCH_PERIOD_RATIOS = (0.5, 2.0, 1 / 3, 3.0, 2 / 3, 3 / 2)


def _query_nasa_archive(tic_id: int) -> list[dict]:
    """Query NASA Exoplanet Archive for confirmed planets around a TIC."""
    query = (
        f"select pl_name,pl_orbper,pl_trandep,pl_rade,tic_id "
        f"from ps where tic_id='TIC {tic_id}' and default_flag=1"
    )
    try:
        return _tap_query(query, timeout=15)
    except Exception as exc:
        LOGGER.warning("NASA archive query failed for TIC %s: %s", tic_id, exc)
        return []


def _is_harmonic(period: float, known_period: float) -> str | None:
    """Check if period is a harmonic of known_period. Returns ratio string or None."""
    for ratio in _CROSSMATCH_PERIOD_RATIOS:
        expected = known_period * ratio
        if abs(period - expected) / expected < _PERIOD_MATCH_FRAC:
            return f"{ratio:.2g}x"
    return None


def crossmatch(summary_path: Path, output_path: Path | None = None) -> dict:
    """Cross-reference candidates_summary.json against NASA Exoplanet Archive.

    Returns enriched summary with match status per candidate.
    """
    with summary_path.open() as f:
        summary = json.load(f)

    enriched_systems: dict[str, dict] = {}
    systems = summary.get("systems", {})
    total = len(systems)

    for i, (target, candidates) in enumerate(systems.items()):
        tic_id = parse_tic_id(target)
        print(f"  [{i + 1}/{total}] {target}...", end=" ", flush=True)

        known = _query_nasa_archive(tic_id)
        known_periods = [(p["pl_name"], p["pl_orbper"]) for p in known if p.get("pl_orbper")]
        print(f"{len(known)} known planet(s)")

        enriched_candidates = []
        for cand in candidates:
            period = cand["period_days"]
            match_status = "NEW"
            match_detail = ""

            # Check direct period match
            for name, kp in known_periods:
                if kp and abs(period - kp) / kp < _PERIOD_MATCH_FRAC:
                    match_status = "KNOWN"
                    match_detail = f"{name} (P={kp:.4f}d)"
                    break

            # Check harmonic match
            if match_status == "NEW":
                for name, kp in known_periods:
                    if not kp:
                        continue
                    ratio = _is_harmonic(period, kp)
                    if ratio:
                        match_status = "HARMONIC"
                        match_detail = f"{ratio} of {name} (P={kp:.4f}d)"
                        break

            enriched_candidates.append({
                **cand,
                "match_status": match_status,
                "match_detail": match_detail,
            })

        enriched_systems[target] = {
            "known_planets": [
                {"name": p["pl_name"], "period": p["pl_orbper"], "radius_earth": p.get("pl_rade")}
                for p in known
            ],
            "candidates": enriched_candidates,
        }

        time.sleep(0.3)  # rate-limit API calls

    new_count = sum(
        1 for sys in enriched_systems.values()
        for c in sys["candidates"] if c["match_status"] == "NEW"
    )
    known_count = sum(
        1 for sys in enriched_systems.values()
        for c in sys["candidates"] if c["match_status"] == "KNOWN"
    )
    harmonic_count = sum(
        1 for sys in enriched_systems.values()
        for c in sys["candidates"] if c["match_status"] == "HARMONIC"
    )

    result = {
        "total_candidates": summary["total_candidates"],
        "new_candidates": new_count,
        "known_matches": known_count,
        "harmonic_matches": harmonic_count,
        "filters": summary["filters"],
        "systems": enriched_systems,
    }

    out = output_path or summary_path.with_name("candidates_crossmatched.json")
    out.write_text(json.dumps(result, indent=2, sort_keys=False))
    print(f"\nSaved to: {out}")
    print(f"  NEW: {new_count}  KNOWN: {known_count}  HARMONIC: {harmonic_count}")

    if new_count:
        print(f"\n=== NEW candidates (not in NASA Exoplanet Archive) ===")
        for target, sys in enriched_systems.items():
            for c in sys["candidates"]:
                if c["match_status"] == "NEW":
                    print(f"  {target}  iter={c['iteration']} P={c['period_days']:.4f}d "
                          f"depth={c['depth_ppm']:.0f}ppm snr={c['snr']:.1f}")

    return result


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Cross-reference candidates against known planets")
    parser.add_argument("summary", type=Path,
                        help="Path to candidates_summary.json")
    parser.add_argument("-o", "--output", type=Path, default=None)
    args = parser.parse_args()

    crossmatch(args.summary, args.output)


if __name__ == "__main__":
    main()
