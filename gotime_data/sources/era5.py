"""ERA5 single levels (reanalysis), hourly, one day per request, both cities (CDS). Used for climatology.

One request at a time (the CDS rejects new requests while old ones are queued). ERA5 ssrd is the
accumulation over the hour ENDING at the time stamp (J/m2) -> W/m2 = / 3600, valid_time = stamp - 1 h.
Point: nearest land cell with ERA5's own land-sea mask (requested with the data).
"""

import logging
from pathlib import Path

import numpy as np
import pandas as pd
import xarray as xr

from .. import convert, grid
from ..config import locations
from ..schema import SITE_COLUMNS, Batch
from ._common import ads_area, ads_retrieve, batch_dir, now_utc, read_manifest, rows, write_manifest

log = logging.getLogger(__name__)

NAME = "era5"
DATASET = "reanalysis-era5-single-levels"
VARIABLES = [
    "2m_temperature",
    "2m_dewpoint_temperature",
    "10m_u_component_of_wind",
    "10m_v_component_of_wind",
    "surface_solar_radiation_downwards",
    "land_sea_mask",
]
DELAY_DAYS = 6  # ERA5T is about 5 days behind real time


def fetch(now=None, day=None) -> Path:
    day = (
        pd.Timestamp(day)
        if day is not None
        else pd.Timestamp(now or now_utc()).floor("D") - pd.Timedelta(days=DELAY_DAYS)
    )
    out = batch_dir(NAME, f"{day:%Y%m%d}")
    for loc_id, loc in locations().items():  # one request at a time
        target = out / f"{loc_id}.nc"
        if not target.exists():
            ads_retrieve(
                "CDS_URL",
                DATASET,
                {
                    "product_type": ["reanalysis"],
                    "variable": VARIABLES,
                    "year": [f"{day:%Y}"],
                    "month": [f"{day:%m}"],
                    "day": [f"{day:%d}"],
                    "time": [f"{h:02d}:00" for h in range(24)],
                    "data_format": "netcdf",
                    "download_format": "unarchived",
                    "area": ads_area(loc["bbox"]),
                },
                target,
            )
    write_manifest(out, {"source": NAME, "day": day, "fetched_time_utc": now_utc()})
    return out


def dataset_to_rows(ds: xr.Dataset, loc_id: str) -> tuple[pd.DataFrame, list]:
    loc = locations()[loc_id]
    lats, lons = ds.latitude.values, ds.longitude.values
    if "lsm" in ds:
        lsm = ds["lsm"].isel(valid_time=0).values
        cell = grid.nearest_land_cell(lats, lons, lsm, loc["lat"], loc["lon"])
    else:
        cell = grid.nearest_cell(lats, lons, loc["lat"], loc["lon"])
    p = ds.sel(latitude=cell.lat, longitude=cell.lon, method="nearest")
    stamps = pd.to_datetime(p["valid_time"].values, utc=True)

    def s(v, shift_h=0):
        return pd.Series(np.asarray(p[v].values, dtype="float64"), index=stamps - pd.Timedelta(hours=shift_h))

    t_c, td_c = convert.kelvin_to_celsius(s("t2m")), convert.kelvin_to_celsius(s("d2m"))
    series = {
        "t2m_c": t_c,
        "td2m_c": td_c,
        "rh2m_pct": convert.rh_from_t_td(t_c, td_c),
        "wind10m_ms": convert.wind_speed(s("u10"), s("v10")),
        "ssrd_wm2": (s("ssrd", shift_h=1) / 3600).clip(lower=0),
    }
    site_id = f"grid:{NAME}:{loc_id}"
    frames = [
        rows(loc_id, var, v, "ok", NAME, "reanalysis", pd.Series(v.index, index=v.index), site_id)
        for var, v in series.items()
    ]
    site = [
        site_id,
        f"{cell.method} to {loc['lat']}, {loc['lon']} (lsm {cell.land_fraction})",
        cell.lat,
        cell.lon,
        "grid",
        NAME,
    ]
    return pd.concat(frames, ignore_index=True), site


def to_points(raw: Path) -> Batch:
    read_manifest(raw)
    frames, sites = [], []
    for loc_id in locations():
        path = raw / f"{loc_id}.nc"
        if path.exists():
            with xr.open_dataset(path) as ds:
                df, site = dataset_to_rows(ds.load(), loc_id)
            frames.append(df)
            sites.append(site)
    hourly = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    return Batch(hourly, pd.DataFrame(sites, columns=SITE_COLUMNS))
