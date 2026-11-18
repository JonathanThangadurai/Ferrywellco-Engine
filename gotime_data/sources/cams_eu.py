"""CAMS European air quality forecast (ensemble), 00 UTC run, 0-96 h hourly, Amsterdam (ADS).

Values are already in ug/m3 (pollutants) and grains/m3 (pollen). Pollen is experimental according
to CAMS. Whether an hourly value is an instant or an hourly mean is not documented (open question);
it is stored at run time + lead hour.
"""

import logging
from pathlib import Path

import pandas as pd
import xarray as xr

from .. import grid
from ..config import locations
from ..schema import SITE_COLUMNS, Batch
from ._common import (
    ads_area,
    ads_retrieve,
    batch_dir,
    cycle_rows,
    now_utc,
    read_manifest,
    rows,
    utc,
    write_manifest,
)

log = logging.getLogger(__name__)

NAME = "cams_eu"
DATASET = "cams-europe-air-quality-forecasts"
LOCATIONS = ["amsterdam"]
MAX_LEAD = 96
# ADS request name -> (file variable, our variable)
VARIABLES = {
    "particulate_matter_2.5um": ("pm2p5_conc", "pm25_ugm3"),
    "particulate_matter_10um": ("pm10_conc", "pm10_ugm3"),
    "nitrogen_dioxide": ("no2_conc", "no2_ugm3"),
    "ozone": ("o3_conc", "o3_ugm3"),
    "alder_pollen": ("apg_conc", "pollen_alder_gm3"),
    "birch_pollen": ("bpg_conc", "pollen_birch_gm3"),
    "grass_pollen": ("gpg_conc", "pollen_grass_gm3"),
    "mugwort_pollen": ("mpg_conc", "pollen_mugwort_gm3"),
    "olive_pollen": ("opg_conc", "pollen_olive_gm3"),
    "ragweed_pollen": ("rwpg_conc", "pollen_ragweed_gm3"),
}
READY_HOUR_UTC = 10  # 0-96 h are complete at about 08:30 UTC (ADS docs); fetch from 10:00 UTC


def latest_run(now=None) -> pd.Timestamp:
    """Today's 00 UTC run after READY_HOUR_UTC, else yesterday's."""
    now = pd.Timestamp(now or now_utc())
    day = now.floor("D")
    return day if now.hour >= READY_HOUR_UTC else day - pd.Timedelta(days=1)


def fetch(now=None) -> Path:
    run = latest_run(now)
    out = batch_dir(NAME, f"{run:%Y%m%d%H}")
    for loc_id in LOCATIONS:
        target = out / f"{loc_id}.nc"
        if target.exists():
            continue
        ads_retrieve(
            "ADS_URL",
            DATASET,
            {
                "variable": list(VARIABLES),
                "model": ["ensemble"],
                "level": ["0"],
                "date": [f"{run:%Y-%m-%d}/{run:%Y-%m-%d}"],
                "type": ["forecast"],
                "time": ["00:00"],
                "leadtime_hour": [str(h) for h in range(MAX_LEAD + 1)],
                "data_format": "netcdf",
                "area": ads_area(locations()[loc_id]["bbox"]),
            },
            target,
        )
    write_manifest(
        out,
        {
            "source": NAME,
            "issue_time_utc": run,
            "publish_time_utc": None,
            "fetched_time_utc": now_utc(),
            "missing_steps": [],
        },
    )
    return out


def to_points(raw: Path) -> Batch:
    manifest = read_manifest(raw)
    run = utc(manifest["issue_time_utc"])
    frames, sites = [], []
    for loc_id in LOCATIONS:
        loc = locations()[loc_id]
        with xr.open_dataset(raw / f"{loc_id}.nc") as ds:
            cell = grid.nearest_cell(ds.latitude, ds.longitude, loc["lat"], loc["lon"])
            site_id = f"grid:{NAME}:{loc_id}"
            sites.append(
                [site_id, f"{cell.method} to {loc['lat']}, {loc['lon']}", cell.lat, cell.lon, "grid", NAME]
            )
            p = ds.sel(latitude=cell.lat, longitude=cell.lon, method="nearest").squeeze("level", drop=True)
            # time is float hours since the run start (00 UTC)
            valid = run + pd.to_timedelta(p["time"].values.astype(float), unit="h")
            full = pd.date_range(run, run + pd.Timedelta(hours=MAX_LEAD), freq="h")
            for file_var, var in VARIABLES.values():
                values = (
                    pd.Series(p[file_var].values.astype("float64"), index=valid) if file_var in p else None
                )
                values = values.reindex(full) if values is not None else pd.Series(float("nan"), index=full)
                frames.append(rows(loc_id, var, values, "ok", NAME, "forecast", run, site_id))
    hourly = pd.concat(frames, ignore_index=True)
    return Batch(hourly, pd.DataFrame(sites, columns=SITE_COLUMNS), cycle_rows(hourly, manifest))
