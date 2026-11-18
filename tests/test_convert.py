"""Tests for gotime_data.convert (no network)."""

import math

import numpy as np
import pandas as pd

from gotime_data.convert import deaccumulate, eea_to_utc, fill_to_hourly, mmr_to_ugm3


def test_eea_is_fixed_utc_plus_one_winter_and_summer():
    """Same rule in January and July: EEA Start minus 1 h, no summer time."""
    local = pd.Series(pd.to_datetime(["2026-11-16 22:00", "2026-07-15 10:00"]))
    utc = eea_to_utc(local, "NL")
    assert utc[0] == pd.Timestamp("2026-11-16 21:00", tz="UTC")
    assert utc[1] == pd.Timestamp("2026-07-15 09:00", tz="UTC")


def test_eea_across_dst_change_has_no_gap_or_double():
    """2026-10-25 (Amsterdam 25-hour day): EEA hours stay evenly spaced in UTC."""
    local = pd.Series(pd.date_range("2026-10-25 00:00", periods=26, freq="h"))
    utc = eea_to_utc(local, "NL")
    assert (utc.diff().dropna() == pd.Timedelta(hours=1)).all()
    assert utc.is_unique


def test_deaccumulate_ssrd_to_mean_watts():
    """3 h of 100 W m-2 = 1 080 000 J m-2 added per 3-hour step."""
    idx = pd.date_range("2026-06-01 00:00", periods=4, freq="3h", tz="UTC")
    accum = pd.Series([0.0, 1_080_000.0, 2_160_000.0, 2_160_000.0], index=idx)
    rate = deaccumulate(accum)
    assert math.isnan(rate.iloc[0])
    assert rate.iloc[1] == 100.0
    assert rate.iloc[2] == 100.0
    assert rate.iloc[3] == 0.0  # night: nothing added


def test_deaccumulate_clips_small_negative():
    idx = pd.date_range("2026-06-01", periods=2, freq="3h", tz="UTC")
    rate = deaccumulate(pd.Series([1000.0, 990.0], index=idx))
    assert rate.iloc[1] == 0.0


def test_fill_3hourly_marks_interpolated():
    idx = pd.date_range("2026-06-01 00:00", periods=3, freq="3h", tz="UTC")
    out = fill_to_hourly(pd.Series([10.0, 13.0, 16.0], index=idx))
    assert len(out) == 7
    assert list(out["value"]) == [10.0, 11.0, 12.0, 13.0, 14.0, 15.0, 16.0]
    assert list(out["status"]) == ["ok", "interpolated", "interpolated", "ok"] + [
        "interpolated",
        "interpolated",
        "ok",
    ]


def test_fill_does_not_bridge_a_missing_step():
    idx = pd.date_range("2026-06-01 00:00", periods=3, freq="3h", tz="UTC")
    out = fill_to_hourly(pd.Series([10.0, np.nan, 16.0], index=idx))
    assert out["value"].iloc[0] == 10.0 and out["value"].iloc[6] == 16.0
    assert out["value"].iloc[1:6].isna().all()
    assert (out["status"].iloc[1:6] == "missing").all()


def test_mmr_to_ugm3_standard_air():
    """At 1013.25 hPa and 15 C, rho = 1.225 kg m-3; 20 ppb-mass-ish mmr 1e-8 -> 12.25 ug m-3."""
    value = mmr_to_ugm3(1e-8, 101325.0, 288.15)
    assert abs(value - 12.25) < 0.01


def test_mmr_to_ugm3_hot_air_is_less_dense():
    """Chennai-like 35 C gives a lower concentration than 15 C for the same mixing ratio."""
    assert mmr_to_ugm3(1e-8, 100800.0, 308.15) < mmr_to_ugm3(1e-8, 101325.0, 288.15)


def test_rh_from_t_and_dew_point():
    from gotime_data.convert import rh_from_t_td

    assert rh_from_t_td(10.0, 10.0) == 100.0
    assert 49 < rh_from_t_td(20.0, 9.3) < 51
