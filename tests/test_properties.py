"""Property-based tests (hypothesis): rules that must hold for any input, not only the examples.

- A found window lasts exactly the duration, starts inside [earliest, latest], stays inside the local day,
  and touches only hours with data: on normal days, both daylight-saving days and in UTC+5:30.
- Snapping never returns a location beyond its radius, and "not covered" means every location is beyond.
"""

from datetime import date, timedelta

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from gotime_api.locations import local_instant, snap
from gotime_api.problems import Problem
from gotime_data.grid import distance_km
from gotime_engine import SandboxEngine, WindowOptions
from gotime_engine.protocol import local_day_hours

DAYS = [
    (date(2026, 10, 25), "Europe/Amsterdam"),  # 25 hours
    (date(2027, 3, 28), "Europe/Amsterdam"),  # 23 hours
    (date(2026, 11, 19), "Europe/Amsterdam"),
    (date(2026, 11, 19), "Asia/Kolkata"),  # UTC+5:30
    (date(2027, 6, 1), "Asia/Kolkata"),
]
hhmm = st.builds(lambda h, m: f"{h:02d}:{m:02d}", st.integers(0, 23), st.integers(0, 59))


@st.composite
def window_case(draw):
    day, tz = draw(st.sampled_from(DAYS))
    hours = local_day_hours(day, tz)
    levels = draw(st.lists(st.one_of(st.none(), st.integers(0, 2)), min_size=len(hours), max_size=len(hours)))
    a, b = sorted([draw(hhmm), draw(hhmm)])
    duration = draw(st.integers(10, 240))
    assessment = {
        "variables_used": [],
        "hours": [
            {
                "start_utc": h.isoformat(),
                "status": "missing" if lv is None else "ok",
                "level": lv or 0,
                "reasons": [],
            }
            for h, lv in zip(hours, levels, strict=True)
        ],
    }
    return day, tz, assessment, a, b, duration


@settings(max_examples=300, deadline=None)
@given(window_case())
def test_window_rules(case):
    day, tz, assessment, a, b, duration = case
    earliest, latest = local_instant(day, a, tz), local_instant(day, b, tz)
    result = SandboxEngine().choose(assessment, WindowOptions(earliest, latest, duration))
    if result.window_status == "none":
        assert result.window_start is None and result.intensity_hint is None
        return
    start, end = result.window_start, result.window_end
    assert end - start == timedelta(minutes=duration)
    assert earliest <= start <= latest
    assert start <= result.latest_start <= latest
    hours = local_day_hours(day, tz)
    assert hours[0] <= start and end <= hours[-1] + timedelta(hours=1)  # inside the hours of the local day
    ok = {h["start_utc"] for h in assessment["hours"] if h["status"] == "ok"}
    h = start.replace(minute=0, second=0, microsecond=0)
    while h < end:
        assert h.isoformat() in ok, "the window touches an hour without data"
        h += timedelta(hours=1)


LOCATIONS = {
    "a": {"id": "a", "lat": 52.4, "lon": 4.9, "radius_km": 15.0},
    "c": {"id": "c", "lat": 13.1, "lon": 80.2, "radius_km": 20.0},
}
near = st.one_of(
    st.tuples(st.floats(52.0, 52.8), st.floats(4.3, 5.5)),
    st.tuples(st.floats(12.8, 13.4), st.floats(79.9, 80.5)),
    st.tuples(st.floats(-90, 90), st.floats(-180, 180)),
)


@settings(max_examples=500, deadline=None)
@given(near)
def test_snapping_respects_radius(point):
    lat, lon = round(point[0], 2), round(point[1], 2)
    distances = {k: float(distance_km(lat, lon, v["lat"], v["lon"])) for k, v in LOCATIONS.items()}
    try:
        loc, d = snap(lat, lon, LOCATIONS)
    except Problem as p:
        assert p.code == "not-covered"
        assert all(distances[k] > v["radius_km"] for k, v in LOCATIONS.items())
        return
    assert d <= loc["radius_km"] + 0.05  # distance_km is rounded to 0.1
    assert distances[loc["id"]] == pytest.approx(min(distances.values()), abs=1e-9) or all(
        distances[k] > v["radius_km"] for k, v in LOCATIONS.items() if k != loc["id"]
    )
