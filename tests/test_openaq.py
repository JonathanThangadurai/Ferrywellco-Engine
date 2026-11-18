"""OpenAQ (internal only) on a SYNTHETIC fixture: duplicate sensors, ppb conversion, missing hours."""

import logging

import pandas as pd
import pytest

from gotime_data.convert import ppb_to_ugm3
from gotime_data.schema import validate
from gotime_data.sources import openaq


@pytest.fixture
def batch(raw_batch):
    return openaq.to_points(raw_batch("openaq"))


def test_duplicate_sensors_keep_non_empty_then_longest(raw_batch, caplog):
    with caplog.at_level(logging.WARNING):
        df = openaq.to_points(raw_batch("openaq")).hourly
    pm = df[df["variable"] == "pm25_ugm3"]
    assert pm["value"].max() < 50  # sensor 3 (48 h, ~40) kept, sensor 4 (40 h, 99) dropped
    assert "dropping 4 (40 h)" in caplog.text
    no2 = df[df["variable"] == "no2_ugm3"]
    assert no2["value"].notna().sum() == 47  # the empty duplicate sensor 1 is ignored; 1 gap hour


def test_no2_ppb_uses_measured_temperature_else_25c(batch):
    no2 = batch.hourly[batch.hourly["variable"] == "no2_ugm3"].set_index("valid_time_utc")
    first, late = no2.iloc[0], no2.iloc[-1]
    assert first["method"] == "ppb->ug/m3, measured T, 1013.25 hPa"  # 30 C measured, no pressure sensor
    assert late["method"] == "ppb->ug/m3, 25 C, 1013.25 hPa"  # no temperature for that hour
    assert late["value"] == pytest.approx(18.8, abs=0.01)  # 10 ppb x 1.88
    assert first["value"] < late["value"]  # warmer air is less dense


def test_conversion_constants_at_25c():
    one = pd.Series([1.0])
    assert ppb_to_ugm3(one, "no2")[0][0] == pytest.approx(1.88, abs=0.005)
    assert ppb_to_ugm3(one, "o3")[0][0] == pytest.approx(1.96, abs=0.005)


def test_gap_hour_is_missing_and_hours_start_at_half_past(batch):
    df = batch.hourly
    assert validate(df) == []
    no2 = df[df["variable"] == "no2_ugm3"]
    assert (no2["status"] == "missing").sum() == 1
    assert (no2["valid_time_utc"].dt.minute == 30).all()  # IST hours = :30 UTC
    assert (df["source"] == "openaq").all() and (df["kind"] == "observation").all()
