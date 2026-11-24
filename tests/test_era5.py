"""ERA5 to_points on a synthetic download shaped like the real CDS file: a zip with one NetCDF file per
step type (instant and accum). Synthetic values, so no ERA5 data is committed."""

import json
import zipfile

import numpy as np
import pandas as pd
import pytest
import xarray as xr

from gotime_data.schema import validate
from gotime_data.sources import era5

DAY = pd.Timestamp("2026-11-08")
LATS, LONS = [52.75, 52.5, 52.25, 52.0], [4.5, 4.75, 5.0, 5.25, 5.5]


def grid(value: float) -> np.ndarray:
    return np.full((24, len(LATS), len(LONS)), value, dtype="float32")


def dataset(**variables) -> xr.Dataset:
    times = pd.date_range(DAY, periods=24, freq="h")
    coords = {"valid_time": times, "latitude": LATS, "longitude": LONS}
    dims = ("valid_time", "latitude", "longitude")
    return xr.Dataset({k: (dims, v) for k, v in variables.items()}, coords=coords)


INSTANT = dict(t2m=grid(283.15), d2m=grid(281.15), u10=grid(3.0), v10=grid(4.0), lsm=grid(1.0))
ACCUM = dict(ssrd=grid(360_000.0))  # J/m2 over the hour -> 100 W/m2


@pytest.fixture
def batch(storage):
    out = storage / "raw" / "era5" / "20261108"
    out.mkdir(parents=True)
    (out / "manifest.json").write_text(json.dumps({"source": "era5", "day": str(DAY)}))
    return out


def test_zip_with_instant_and_accum_files(batch, tmp_path):
    dataset(**INSTANT).to_netcdf(tmp_path / "data_stream-oper_stepType-instant.nc")
    dataset(**ACCUM).to_netcdf(tmp_path / "data_stream-oper_stepType-accum.nc")
    with zipfile.ZipFile(batch / "amsterdam.nc", "w") as z:
        for name in ("data_stream-oper_stepType-instant.nc", "data_stream-oper_stepType-accum.nc"):
            z.write(tmp_path / name, name)

    df = era5.to_points(batch).hourly
    assert validate(df) == []
    assert set(df["variable"]) == {"t2m_c", "td2m_c", "rh2m_pct", "wind10m_ms", "ssrd_wm2"}
    assert set(df["kind"]) == {"reanalysis"} and set(df["location_id"]) == {"amsterdam"}
    value = df.groupby("variable")["value"].first()
    assert value["t2m_c"] == pytest.approx(10.0)
    assert value["wind10m_ms"] == pytest.approx(5.0)
    assert value["ssrd_wm2"] == pytest.approx(100.0)
    ssrd = df[df["variable"] == "ssrd_wm2"]["valid_time_utc"]
    assert ssrd.min() == pd.Timestamp(DAY, tz="UTC") - pd.Timedelta(hours=1)  # end-of-hour accumulation


def test_plain_netcdf_still_works(batch):
    dataset(**INSTANT, **ACCUM).to_netcdf(batch / "amsterdam.nc")
    df = era5.to_points(batch).hourly
    assert validate(df) == [] and len(df) == 5 * 24
