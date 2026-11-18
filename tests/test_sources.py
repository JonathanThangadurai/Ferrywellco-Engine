"""to_points() of each source on saved fixtures: canonical rows, units, times, missing values."""

import pandas as pd

from gotime_data.schema import validate
from gotime_data.sources import cams_eu, cams_global, ecmwf, eea

RUN = pd.Timestamp("2026-11-16 00:00", tz="UTC")


def test_ecmwf_rows_hourly_with_interpolated_hours(raw_batch):
    batch = ecmwf.to_points(raw_batch("ecmwf_ifs"))
    df = batch.hourly
    assert validate(df) == []
    t = df[(df["location_id"] == "amsterdam") & (df["variable"] == "t2m_c")].set_index("valid_time_utc")
    assert list(t.index) == list(pd.date_range(RUN, RUN + pd.Timedelta(hours=12), freq="h"))
    assert t.loc[RUN + pd.Timedelta(hours=3), "status"] == "ok"
    assert t.loc[RUN + pd.Timedelta(hours=4), "status"] == "interpolated"
    assert t["value"].between(-5, 20).all()  # November in Amsterdam, deg C
    assert list(t["lead_h"]) == list(range(13))
    ssrd = df[(df["location_id"] == "amsterdam") & (df["variable"] == "ssrd_wm2")]
    assert ssrd["valid_time_utc"].min() == RUN and ssrd["valid_time_utc"].max() == RUN + pd.Timedelta(
        hours=11
    )
    assert (ssrd["value"] >= 0).all()
    assert batch.cycles["complete"].all()
    assert str(batch.cycles["publish_time_utc"].iloc[0]) == "2026-11-16 09:34:00+00:00"


def test_ecmwf_missing_step_gives_missing_rows(raw_batch):
    raw = raw_batch("ecmwf_ifs")
    (raw / "amsterdam_step006.nc").unlink()
    batch = ecmwf.to_points(raw)
    t = batch.hourly[(batch.hourly["location_id"] == "amsterdam") & (batch.hourly["variable"] == "t2m_c")]
    t = t.set_index("valid_time_utc")
    for h in (4, 5, 6, 7, 8):  # no bridging across the missing step
        assert t.loc[RUN + pd.Timedelta(hours=h), "status"] == "missing"
        assert pd.isna(t.loc[RUN + pd.Timedelta(hours=h), "value"])
    assert validate(batch.hourly) == []
    cyc = batch.cycles[(batch.cycles["location_id"] == "amsterdam") & (batch.cycles["variable"] == "t2m_c")]
    assert not cyc["complete"].iloc[0]


def test_cams_eu_units_and_pollen_per_type(raw_batch):
    batch = cams_eu.to_points(raw_batch("cams_eu"))
    df = batch.hourly
    assert validate(df) == []
    assert df["valid_time_utc"].nunique() == 97  # 0-96 h hourly
    assert {f"pollen_{p}_gm3" for p in ("alder", "birch", "grass", "mugwort", "olive", "ragweed")} <= set(
        df["variable"]
    )
    no2 = df[df["variable"] == "no2_ugm3"]["value"]
    assert no2.between(0, 200).all()  # ug/m3, plausible range
    assert batch.sites["station_type"].iloc[0] == "grid"


def test_cams_global_converts_kg_to_ug(raw_batch, monkeypatch):
    raw = raw_batch("cams_global")
    monkeypatch.setattr(
        cams_global, "lsm_file", lambda loc: raw.parents[2] / "static" / f"ecmwf_lsm_{loc}.nc"
    )
    df = cams_global.to_points(raw).hourly
    assert validate(df) == []
    means = df.groupby("variable")["value"].mean()
    assert 5 < means["pm25_ugm3"] < 200
    assert 0 < means["no2_ugm3"] < 100
    assert 20 < means["o3_ugm3"] < 250
    assert df[df["variable"] == "no2_ugm3"]["method"].str.contains("model level 137").all()


def test_eea_start_is_utc_plus_one_and_rows_are_valid(raw_batch):
    raw = raw_batch("eea_e2a")
    df = eea.to_points(raw).hourly
    assert validate(df) == []
    src = pd.read_parquet(next(raw.glob("*.parquet")))
    last = src.iloc[-1]
    stored = df.set_index("valid_time_utc").loc[
        pd.Timestamp(last["Start"]).tz_localize("UTC") - pd.Timedelta(hours=1)
    ]
    assert stored["value"] == float(last["Value"])
    assert stored["site_id"].startswith("eea:NL/")


def test_eea_invalid_values_become_missing():
    t = pd.date_range("2026-11-17 00:00", periods=3, freq="h")
    src = pd.DataFrame(
        {
            "Samplingpoint": "NL/SPO-TEST",
            "Pollutant": 8,
            "Start": t,
            "End": t + pd.Timedelta(hours=1),
            "Value": ["10.5", "-999", "12.0"],
            "Unit": "ug.m-3",
            "Validity": [1, -1, 1],
            "ResultTime": t[-1] + pd.Timedelta(hours=2),
        }
    )
    df = eea.file_to_rows(src, pd.Timestamp("2026-11-16 23:00", tz="UTC"))
    assert list(df["status"]) == ["ok", "missing", "ok"]
    assert pd.isna(df["value"].iloc[1])


def test_cams_global_stores_land_cell_default_and_coastal_alternative(raw_batch, monkeypatch):
    raw = raw_batch("cams_global")
    monkeypatch.setattr(
        cams_global, "lsm_file", lambda loc: raw.parents[2] / "static" / f"ecmwf_lsm_{loc}.nc"
    )
    batch = cams_global.to_points(raw)
    sites = batch.sites.set_index("site_id")
    assert tuple(sites.loc["grid:cams_global:chennai", ["lat", "lon"]]) == (13.2, 80.0)  # land, default
    assert tuple(sites.loc["grid:cams_global:chennai:coastal", ["lat", "lon"]]) == (13.2, 80.4)
    assert batch.hourly.groupby("site_id").size().nunique() == 1  # same rows for both cells
    assert validate(batch.hourly) == []
