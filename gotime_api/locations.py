"""Snapping a request point to a supported location, and local dates and times there.

The point is already rounded to 2 decimals (models.py). Each location has a reference point (its
weather grid cell centre) and a radius; the nearest location within its radius wins.
"""

from datetime import date, datetime, timedelta

import pandas as pd

from gotime_data.grid import distance_km

from .problems import Problem


def snap(lat: float, lon: float, locations: dict) -> tuple[dict, float]:
    """(location entry, distance_km rounded to 0.1) or 422 not-covered."""
    best, best_d = None, float("inf")
    for loc in locations.values():
        d = float(distance_km(lat, lon, loc["lat"], loc["lon"]))
        if d <= loc["radius_km"] and d < best_d:
            best, best_d = loc, d
    if best is None:
        raise Problem(422, "not-covered", detail="No supported location within its radius; see /v1/coverage.")
    return best, round(best_d, 1)


def local_today(tz: str, now_utc: datetime) -> date:
    return pd.Timestamp(now_utc).tz_convert(tz).date()


def allowed_dates(tz: str, now_utc: datetime, days_ahead: int) -> list[date]:
    today = local_today(tz, now_utc)
    return [today + timedelta(days=i) for i in range(days_ahead + 1)]


def local_instant(day: date, hhmm: str, tz: str) -> datetime:
    """A local wall-clock time on a date as an aware instant.

    On the spring-forward day a time that does not exist (02:30 in Amsterdam) moves forward to the first
    valid time; on the fall-back day a time that occurs twice is the first one (summer time).
    """
    naive = pd.Timestamp(f"{day.isoformat()} {hhmm}")
    return naive.tz_localize(tz, nonexistent="shift_forward", ambiguous=True).to_pydatetime()
