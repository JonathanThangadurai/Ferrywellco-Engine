"""Where the guidance engine plugs in (docs/engine-interface.md).

The science belongs to the health engine. This file only fixes the shape of what goes in and comes out, so
the precompute job and the API can call any engine the same way. Every output field is a draft, to agree
with the health engine.

Two steps, because request options such as start times and duration are continuous:
    assess(...)  once per forecast cycle, per location, local date and profile (slow is fine)
    choose(...)  per request, from the stored assessment (must be fast and pure)
"""

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from itertools import product
from typing import Any, Literal, Protocol

import pandas as pd

# Draft list, to agree with the partner and the health engine.
ACTIVITY_TYPES = ["walk", "run", "cycle", "outdoor_workout"]
YES_NO_UNKNOWN = ["yes", "no", "unknown"]
MODES = ("live", "sandbox")


@dataclass(frozen=True)
class Profile:
    """The discrete request options; every combination is precomputed."""

    activity_type: str
    heat_adapted: str = "unknown"
    hay_fever: str = "unknown"

    @property
    def id(self) -> str:
        return f"{self.activity_type}.{self.heat_adapted}.{self.hay_fever}"


def all_profiles() -> list[Profile]:
    """Every combination of activity type, heat_adapted and hay_fever (4 x 3 x 3 = 36)."""
    return [Profile(a, h, f) for a, h, f in product(ACTIVITY_TYPES, YES_NO_UNKNOWN, YES_NO_UNKNOWN)]


@dataclass(frozen=True)
class LocationInfo:
    """A supported location: id = its weather grid cell, alias = the city name."""

    id: str
    alias: str
    timezone: str
    lat: float
    lon: float


@dataclass(frozen=True)
class WindowOptions:
    """Per-request options, already turned into timezone-aware instants by the API."""

    earliest_start: datetime
    latest_start: datetime
    duration_min: int


@dataclass
class Reason:
    """Why the result is what it is. Draft, to agree with the health engine."""

    code: str
    source: str
    params: dict[str, float] = field(default_factory=dict)
    text: str | None = None


@dataclass
class GuidanceResult:
    """What choose() returns. Every field: draft, to agree with the health engine.

    window_status 'found' -> window_start, window_end and latest_start are set (aware datetimes):
        window_start..window_end  the suitable period (at least duration long, inside the options)
        latest_start              window_end - duration: the last start that fits the activity in the period
        so window_start <= latest_start and latest_start + duration == window_end (the API checks this).
    window_status 'none'  -> they are None and intensity_hint is None.
    """

    window_status: Literal["found", "none"]
    window_start: datetime | None
    window_end: datetime | None
    latest_start: datetime | None
    intensity_hint: str | None
    indoor_alternative: Literal["suggested", "not_suggested"]
    reasons: list[Reason]
    variables_used: list[str]


# An assessment is whatever choose() needs; it must be JSON-serialisable, because it is stored in the
# snapshot. The API never looks inside it.
Assessment = dict[str, Any]


class GuidanceEngine(Protocol):
    name: str
    version: str  # change it whenever results can change; it is part of the snapshot id
    mode: Literal["live", "sandbox"]

    def required_variables(self) -> list[str]:
        """Variables without which the engine gives no answer (they decide the data status)."""
        ...

    def optional_variables(self) -> list[str]:
        """Variables used when present (e.g. pollen); their absence makes the data status 'degraded'."""
        ...

    def profiles(self) -> list[Profile]:
        """Every discrete option combination to precompute."""
        ...

    def assess(
        self,
        hourly: pd.DataFrame,
        location: LocationInfo,
        local_date: date,
        profile: Profile,
        now_utc: datetime,
    ) -> Assessment:
        """Judge each hour of local_date for this profile. hourly covers that date plus the 24 h before."""
        ...

    def choose(self, assessment: Assessment, options: WindowOptions) -> GuidanceResult:
        """Pick the window for one request. Pure: no I/O, no clock. Must take under 5 ms."""
        ...


def local_day_hours(local_date: date, tz: str) -> pd.DatetimeIndex:
    """Start (UTC) of every UTC hour that overlaps a local calendar day.

    23, 24 or 25 hours on daylight-saving days. In zones with a half-hour offset (Chennai, UTC+5:30) the
    first and last hour lie half outside the day, because forecast hours start on the full UTC hour.
    """
    start = pd.Timestamp(local_date).tz_localize(tz).tz_convert("UTC").floor("h")
    end = pd.Timestamp(local_date + timedelta(days=1)).tz_localize(tz).tz_convert("UTC").ceil("h")
    return pd.date_range(start, end, freq="h", inclusive="left")
