"""Read interface on a temporary store: latest complete cycle, internal sources, averages, DST days."""

from datetime import UTC, datetime

import pandas as pd
import pytest

from gotime_data.read import get_hourly, max_8h_mean, mean_24h
from gotime_data.schema import Batch, normalise
from gotime_data.sources import openaq
from gotime_data.store import clean, read_hourly, write_batch
from gotime_data.store.write import write_hourly


def forecast(issue: str, value: float, hours: int = 24, missing_at: int | None = None) -> pd.DataFrame:
    issue_t = pd.Timestamp(issue, tz="UTC")
    t = pd.date_range(issue_t, periods=hours, freq="h")
    values = [None if i == missing_at else value for i in range(hours)]
    return normalise(
        pd.DataFrame(
            {
                "location_id": "amsterdam",
                "valid_time_utc": t,
                "variable": "pm25_ugm3",
                "value": values,
                "source": "cams_eu",
                "kind": "forecast",
                "issue_time_utc": issue_t,
                "lead_h": range(hours),
                "status": ["missing" if v is None else "ok" for v in values],
            }
        )
    )


def cycles(df: pd.DataFrame) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "source": "cams_eu",
            "location_id": "amsterdam",
            "variable": "pm25_ugm3",
            "issue_time_utc": [df["issue_time_utc"].iloc[0]],
            "publish_time_utc": None,
            "fetched_time_utc": None,
            "n_expected": len(df),
            "n_missing": int(df["value"].isna().sum()),
            "complete": bool(df["value"].notna().all()),
        }
    )


START, END = datetime(2026, 11, 16, tzinfo=UTC), datetime(2026, 11, 18, tzinfo=UTC)


def test_latest_complete_cycle_is_served(storage):
    old, new = forecast("2026-11-16 00:00", 10.0), forecast("2026-11-17 00:00", 20.0, missing_at=3)
    write_batch(Batch(old, cycles=cycles(old)), "a")
    write_batch(Batch(new, cycles=cycles(new)), "b")
    df = get_hourly("amsterdam", START, END, ["pm25_ugm3"])
    assert set(df["issue_time_utc"]) == {pd.Timestamp("2026-11-16", tz="UTC")}  # newer one is incomplete
    complete = forecast("2026-11-17 00:00", 30.0, hours=24)
    complete["issue_time_utc"] = pd.Timestamp("2026-11-17 12:00", tz="UTC")
    write_batch(Batch(complete, cycles=cycles(complete)), "c")
    df = get_hourly("amsterdam", START, END, ["pm25_ugm3"])
    assert (df["value"] == 30.0).all()
    assert len(get_hourly("amsterdam", START, END, ["pm25_ugm3"], latest_only=False)) == 72


def test_writing_twice_adds_no_duplicates(storage):
    old = forecast("2026-11-16 00:00", 10.0)
    assert write_batch(Batch(old), "a") == 24
    assert write_batch(Batch(old), "a") == 0


def test_naive_times_are_refused(storage):
    with pytest.raises(ValueError):
        get_hourly("amsterdam", datetime(2026, 11, 16), END)


def test_openaq_is_left_out_unless_internal(storage, raw_batch):
    write_batch(openaq.to_points(raw_batch("openaq")), "x")
    s, e = datetime(2026, 11, 15, tzinfo=UTC), datetime(2026, 11, 18, tzinfo=UTC)
    assert get_hourly("chennai", s, e, kind="observation").empty
    assert not get_hourly("chennai", s, e, kind="observation", include_internal=True).empty


def test_retention_moves_old_partitions_to_archive_and_read_still_finds_them(storage):
    write_hourly(forecast("2026-10-01 00:00", 5.0))
    result = clean(now=datetime(2026, 11, 18, tzinfo=UTC))
    assert result["live_partitions_archived"] == 1
    assert not list((storage / "live" / "hourly").rglob("*.parquet"))
    df = read_hourly("amsterdam", datetime(2026, 10, 1, tzinfo=UTC), datetime(2026, 10, 2, tzinfo=UTC))
    assert len(df) == 24


def hourly(start_local: str, end_local: str, tz: str, value=1.0) -> pd.DataFrame:
    """Observation rows covering a local period, built in UTC."""
    t = pd.date_range(
        pd.Timestamp(start_local, tz=tz), pd.Timestamp(end_local, tz=tz), freq="h", inclusive="left"
    )
    t = t.tz_convert("UTC")
    return normalise(
        pd.DataFrame(
            {
                "location_id": "amsterdam",
                "valid_time_utc": t,
                "variable": "o3_ugm3",
                "value": value if not callable(value) else [value(x) for x in t],
                "source": "eea_e2a",
                "kind": "observation",
                "issue_time_utc": t,
                "status": "ok",
                "site_id": "eea:test",
            }
        )
    )


@pytest.mark.parametrize("day, hours", [("2026-10-25", 25), ("2027-03-28", 23), ("2026-11-16", 24)])
def test_max_8h_mean_per_local_day_on_dst_days(day, hours):
    tz = "Europe/Amsterdam"
    nxt = (pd.Timestamp(day) + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    peak = pd.Timestamp(f"{day} 14:00", tz=tz).tz_convert("UTC")  # 8 hours of 80 ending at 14:00 local
    df = hourly(day, nxt, tz, value=lambda t: 80.0 if peak - pd.Timedelta(hours=7) <= t <= peak else 20.0)
    out = max_8h_mean(df, "o3_ugm3", tz)
    row = out[out["local_date"] == pd.Timestamp(day).date()].iloc[0]
    assert row["n_hours"] == hours
    assert row["value"] == pytest.approx(80.0)


def test_mean_24h_needs_coverage():
    df = hourly("2026-11-16 00:00", "2026-11-17 00:00", "UTC", value=10.0)
    df = df.assign(variable="pm25_ugm3")
    out = mean_24h(df, "pm25_ugm3")
    assert out["value"].iloc[-1] == 10.0 and out["n_hours"].iloc[-1] == 24
    assert pd.isna(out["value"].iloc[16]) and out["n_hours"].iloc[16] == 17  # 17 < 18 hours
    gappy = df.drop(index=range(0, 8))  # 16 of 24 hours left
    assert pd.isna(mean_24h(gappy, "pm25_ugm3")["value"].iloc[-1])


def test_alternative_coastal_cell_only_on_request(storage, raw_batch, monkeypatch):
    from gotime_data.sources import cams_global

    raw = raw_batch("cams_global")
    monkeypatch.setattr(
        cams_global, "lsm_file", lambda loc: raw.parents[2] / "static" / f"ecmwf_lsm_{loc}.nc"
    )
    write_batch(cams_global.to_points(raw), "g")
    s, e = datetime(2026, 11, 16, tzinfo=UTC), datetime(2026, 11, 21, tzinfo=UTC)
    assert set(get_hourly("chennai", s, e)["site_id"]) == {"grid:cams_global:chennai"}
    both = get_hourly("chennai", s, e, include_alternatives=True)
    assert set(both["site_id"]) == {"grid:cams_global:chennai", "grid:cams_global:chennai:coastal"}
