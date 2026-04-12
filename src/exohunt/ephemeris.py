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
    # Batman model params (only for confirmed planets with published values)
    rp_rs: float | None = None       # Rp/Rs
    a_rs: float | None = None        # a/Rs
    impact_param: float | None = None # impact parameter
    confirmed: bool = False


def query_known_ephemerides(tic_id: int, timeout: float = 15.0) -> list[KnownPlanetEphemeris]:
    """Query NASA Exoplanet Archive for confirmed planets around a TIC.

    Returns ephemerides with period, transit midpoint (BJD), and duration
    needed to build a pre-masking transit mask.
    """
    query = (
        f"select pl_name,pl_orbper,pl_tranmid,pl_trandur,pl_ratror,pl_ratdor,pl_imppar "
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
        dur_h = float(dur) if dur is not None else 3.0
        # Batman params — may be None
        rp_rs = row.get("pl_ratror")
        a_rs = row.get("pl_ratdor")
        imp = row.get("pl_imppar")
        results.append(KnownPlanetEphemeris(
            name=name, period_days=float(period),
            t0_bjd=float(t0), duration_hours=dur_h,
            rp_rs=float(rp_rs) if rp_rs is not None else None,
            a_rs=float(a_rs) if a_rs is not None else None,
            impact_param=float(imp) if imp is not None else None,
            confirmed=True,
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


def query_toi_ephemerides(tic_id: int, timeout: float = 15.0) -> list[KnownPlanetEphemeris]:
    """Query NASA Exoplanet Archive TOI table for planet candidates.

    Returns ephemerides for TOI candidates (not yet confirmed) so they
    can be pre-masked alongside confirmed planets.
    """
    query = (
        f"select toi,pl_orbper,pl_tranmid,pl_trandurh "
        f"from toi where tid={tic_id}"
    )
    url = f"{_NASA_TAP}?query={urllib.parse.quote(query)}&format=json"
    try:
        resp = urllib.request.urlopen(url, timeout=timeout)
        rows = json.loads(resp.read())
    except Exception as exc:
        LOGGER.warning("TOI query failed for TIC %d: %s", tic_id, exc)
        return []

    results: list[KnownPlanetEphemeris] = []
    for row in rows:
        period = row.get("pl_orbper")
        t0 = row.get("pl_tranmid")
        dur = row.get("pl_trandurh")
        toi = row.get("toi", "unknown")
        if period is None or t0 is None:
            continue
        dur_h = float(dur) if dur is not None else 3.0
        results.append(KnownPlanetEphemeris(
            name=f"TOI-{toi}", period_days=float(period),
            t0_bjd=float(t0), duration_hours=dur_h,
        ))

    if results:
        LOGGER.info(
            "TIC %d: %d TOI candidate(s): %s",
            tic_id, len(results),
            ", ".join(f"{e.name} P={e.period_days:.3f}d" for e in results),
        )
    return results


def query_all_ephemerides(tic_id: int, timeout: float = 15.0) -> list[KnownPlanetEphemeris]:
    """Query both confirmed planets and TOI candidates, deduplicated by period."""
    confirmed = query_known_ephemerides(tic_id, timeout)
    tois = query_toi_ephemerides(tic_id, timeout)

    # Deduplicate: if a TOI period matches a confirmed planet within 3%, skip it
    combined = list(confirmed)
    confirmed_periods = [e.period_days for e in confirmed]
    for toi in tois:
        if any(abs(toi.period_days - cp) / max(cp, 1e-9) < 0.03 for cp in confirmed_periods):
            continue
        combined.append(toi)
    return combined
