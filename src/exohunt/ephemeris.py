"""Query known planet ephemerides from NASA Exoplanet Archive.

Used for pre-masking known transits before the search stage so the
first TLS pass is immediately sensitive to new, weaker signals.
"""
from __future__ import annotations

import json
import logging
import urllib.parse
import urllib.request
from dataclasses import dataclass

LOGGER = logging.getLogger(__name__)

_NASA_TAP = "https://exoplanetarchive.ipac.caltech.edu/TAP/sync"


@dataclass(frozen=True)
class KnownPlanetEphemeris:
    name: str
    period_days: float
    t0_bjd: float
    duration_hours: float


def query_known_ephemerides(tic_id: int, timeout: float = 15.0) -> list[KnownPlanetEphemeris]:
    """Query NASA Exoplanet Archive for confirmed planets around a TIC.

    Returns ephemerides with period, transit midpoint (BJD), and duration
    needed to build a pre-masking transit mask.
    """
    query = (
        f"select pl_name,pl_orbper,pl_tranmid,pl_trandur "
        f"from ps where tic_id='TIC {tic_id}' and default_flag=1"
    )
    url = f"{_NASA_TAP}?query={urllib.parse.quote(query)}&format=json"
    try:
        resp = urllib.request.urlopen(url, timeout=timeout)
        rows = json.loads(resp.read())
    except Exception as exc:
        LOGGER.warning("Ephemeris query failed for TIC %d: %s", tic_id, exc)
        return []

    results: list[KnownPlanetEphemeris] = []
    for row in rows:
        period = row.get("pl_orbper")
        t0 = row.get("pl_tranmid")
        dur = row.get("pl_trandur")
        name = row.get("pl_name", "unknown")
        if period is None or t0 is None:
            continue
        # Duration may be missing; use a default of 3 hours
        dur_h = float(dur) if dur is not None else 3.0
        results.append(KnownPlanetEphemeris(
            name=name, period_days=float(period),
            t0_bjd=float(t0), duration_hours=dur_h,
        ))

    if results:
        LOGGER.info(
            "TIC %d: %d known planet(s): %s",
            tic_id, len(results),
            ", ".join(f"{e.name} P={e.period_days:.3f}d" for e in results),
        )
    else:
        LOGGER.info("TIC %d: no known planets in NASA archive.", tic_id)
    return results
