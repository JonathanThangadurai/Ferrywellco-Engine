"""SandboxEngine, precompute job and serving snapshot (no network, synthetic forecasts)."""

from datetime import UTC, date, datetime, timedelta

import pandas as pd
import pytest

from gotime_api.locations import local_instant
from gotime_api.snapshots import SnapshotStore
from gotime_engine import SandboxEngine, WindowOptions
from gotime_engine import snapshot as snapshot_files
from gotime_engine.precompute import location_id, run
from gotime_engine.protocol import all_profiles, local_day_hours
from tests.api_support import write_forecast

NOW = datetime(2026, 11, 19, 10, tzinfo=UTC)


def test_profiles_cover_every_option():
    profiles = all_profiles()
    assert len(profiles) == 4 * 3 * 3
    assert len({p.id for p in profiles}) == len(profiles)


def test_location_id_is_the_grid_cell():
    assert location_id(52.4, 4.9) == "ecmwf0p1_n52.40_e4.90"
    assert location_id(-33.9, -70.65) == "ecmwf0p1_s33.90_w70.65"


@pytest.mark.parametrize(
    ("day", "tz", "n"),
    [
        (date(2026, 10, 25), "Europe/Amsterdam", 25),  # clocks go back
        (date(2027, 3, 28), "Europe/Amsterdam", 23),  # clocks go forward
        (date(2026, 11, 19), "Europe/Amsterdam", 24),
        (date(2026, 11, 19), "Asia/Kolkata", 25),  # UTC+5:30: first and last hour half outside the day
    ],
)
def test_local_day_hours(day, tz, n):
    hours = local_day_hours(day, tz)
    assert len(hours) == n
    assert all(h.minute == 0 for h in hours)


def test_local_instant_on_daylight_saving_days():
    # 02:30 does not exist on 2027-03-28: moved forward to 03:00 summer time.
    assert (
        local_instant(date(2027, 3, 28), "02:30", "Europe/Amsterdam").isoformat()
        == "2027-03-28T03:00:00+02:00"
    )
    # 02:30 happens twice on 2026-10-25: the first one (summer time).
    assert (
        local_instant(date(2026, 10, 25), "02:30", "Europe/Amsterdam").isoformat()
        == "2026-10-25T02:30:00+02:00"
    )


def test_precompute_writes_snapshot_once_per_cycle(storage):
    write_forecast("2026-11-19 00:00")
    sid = run(SandboxEngine(), NOW)
    assert sid and sid.startswith("20261119T00Z-")
    assert snapshot_files.current_id("sandbox") == sid
    assert run(SandboxEngine(), NOW + timedelta(hours=1)) is None  # same cycle, same dates: nothing to do
    snap = snapshot_files.load("sandbox", sid)
    assert {loc["alias"] for loc in snap["locations"]} == {"amsterdam", "chennai"}
    assert len(snap["assessments"]) == 2 * 4 * 36
    assert snap["mode"] == "sandbox"
    for loc in snap["locations"]:  # only per-location results; nothing about requests or people
        assert set(loc) == {"id", "alias", "name", "lat", "lon", "radius_km", "timezone", "dates", "sources"}

    write_forecast("2026-11-19 06:00")
    new = run(SandboxEngine(), NOW + timedelta(hours=9))
    assert new != sid and new.startswith("20261119T06Z-")
    assert snapshot_files.current_id("sandbox") == new


def test_old_snapshots_are_pruned(storage):
    for i in range(6):
        write_forecast(f"2026-11-19 {i:02d}:00", locations=("amsterdam",))
        run(SandboxEngine(), NOW + timedelta(hours=i))
    files = list((storage / "serving" / "sandbox").glob("*.json.gz"))
    assert len(files) == snapshot_files.KEEP


def test_api_keeps_old_snapshot_when_new_one_is_broken(storage):
    write_forecast("2026-11-19 00:00", locations=("amsterdam",))
    sid = run(SandboxEngine(), NOW)
    store = SnapshotStore("sandbox")
    assert store.reload() and store.current.id == sid
    (storage / "serving" / "sandbox" / "CURRENT").write_text("20261119T06Z-deadbeef\n")  # file does not exist
    assert not store.reload()
    assert store.current.id == sid


def test_api_refuses_snapshot_from_another_engine_version(storage, monkeypatch):
    write_forecast("2026-11-19 00:00", locations=("amsterdam",))
    run(SandboxEngine(), NOW)
    monkeypatch.setattr(SandboxEngine, "version", "9.9.9")
    store = SnapshotStore("sandbox")
    assert not store.reload() and store.current is None


def _assessment(levels: list[int | None], start: str = "2026-11-19 00:00") -> dict:
    t0 = pd.Timestamp(start, tz="UTC")
    return {
        "variables_used": ["t2m_c"],
        "hours": [
            {
                "start_utc": (t0 + pd.Timedelta(hours=i)).isoformat(),
                "status": "missing" if lv is None else "ok",
                "level": lv or 0,
                "reasons": [],
            }
            for i, lv in enumerate(levels)
        ],
    }


def _options(earliest: str, latest: str, minutes: int) -> WindowOptions:
    day = "2026-11-19"
    return WindowOptions(
        pd.Timestamp(f"{day} {earliest}", tz="UTC").to_pydatetime(),
        pd.Timestamp(f"{day} {latest}", tz="UTC").to_pydatetime(),
        minutes,
    )


def test_window_fits_the_whole_activity():
    levels = [0] * 24
    levels[8] = None  # 08:00-09:00 has no data
    result = SandboxEngine().choose(_assessment(levels), _options("07:00", "12:00", 90))
    # 07:00 + 90 min would touch 08:00 (missing); the period starts after the gap at 09:00 and lasts until
    # the latest allowed start (12:00) plus the duration.
    assert result.window_status == "found"
    assert result.window_start.hour == 9 and result.window_start.minute == 0
    assert result.latest_start.hour == 12 and result.latest_start.minute == 0
    assert result.window_end == result.latest_start + timedelta(minutes=90)


def test_window_prefers_lower_level_then_earliest():
    levels = [2] * 24
    levels[14] = levels[15] = 0
    result = SandboxEngine().choose(_assessment(levels), _options("06:00", "18:00", 60))
    assert result.window_start.hour == 14 and result.window_start.minute == 0
    assert result.latest_start.hour == 15  # 15:00-16:00 is still level 0; 15:15 would touch 16:00
    assert result.window_end.hour == 16 and result.window_end.minute == 0  # the period is 14:00-16:00
    assert result.intensity_hint == "as_planned"
    assert result.indoor_alternative == "not_suggested"


def test_no_window_when_nothing_fits():
    levels = [0] * 24
    levels[10] = None
    result = SandboxEngine().choose(_assessment(levels), _options("09:30", "10:00", 60))
    assert result.window_status == "none" and result.window_start is None and result.intensity_hint is None


def test_sandbox_output_is_always_flagged(storage):
    write_forecast("2026-11-19 00:00")
    snap = snapshot_files.load("sandbox", run(SandboxEngine(), NOW))
    engine = SandboxEngine()
    for key, assessment in snap["assessments"].items():
        for h in assessment["hours"]:
            for r in h["reasons"]:
                assert r["code"].startswith("sandbox_") and r["text"] == "Sandbox example, not guidance."
        day = date.fromisoformat(key.split("|")[1])
        opts = WindowOptions(
            local_instant(day, "06:00", assessment["timezone"]),
            local_instant(day, "20:00", assessment["timezone"]),
            60,
        )
        result = engine.choose(assessment, opts)
        assert result.reasons and all(r.code.startswith("sandbox_") for r in result.reasons)
