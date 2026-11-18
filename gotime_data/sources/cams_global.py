"""CAMS global composition forecast, runs 00/12 UTC, 0-120 h hourly, Chennai (ADS).

Two requests per run: PM2.5/PM10 with surface pressure and 2 m temperature at the surface, and NO2/O3
at model level 137 (the lowest level; they are silently dropped if asked together with surface fields).
PM comes in kg/m3 (x 1e9 -> ug/m3); NO2/O3 in kg/kg, converted with the air density from sp and t2m.

Point: nearest land cell. CAMS global has no land-sea mask in our files, so the land share of each
0.4 deg cell is the mean of the ECMWF 0.1 deg land-sea mask inside it (saved by the ECMWF fetch).
The plain nearest (coastal) cell is stored too, as an alternative site (see cells_to_store).
"""

import logging
from pathlib import Path

import numpy as np
import pandas as pd
import xarray as xr

from .. import convert, grid
from ..config import locations, raw_dir
from ..schema import ALTERNATIVE_SITE_SUFFIX, SITE_COLUMNS, Batch
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

NAME = "cams_global"
DATASET = "cams-global-atmospheric-composition-forecasts"
LOCATIONS = ["chennai"]
MAX_LEAD = 120
CELL_SIZE = 0.4
READY_AFTER = pd.Timedelta(hours=12)  # conservative; same-day timing still to be measured
SURFACE_VARS = ["particulate_matter_2.5um", "particulate_matter_10um", "surface_pressure", "2m_temperature"]
LEVEL_VARS = ["nitrogen_dioxide", "ozone"]


def latest_run(now=None) -> pd.Timestamp:
    now = pd.Timestamp(now or now_utc())
    run = now.floor("12h")
    return run if now >= run + READY_AFTER else run - pd.Timedelta(hours=12)


def fetch(now=None) -> Path:
    run = latest_run(now)
    out = batch_dir(NAME, f"{run:%Y%m%d%H}")
    for loc_id in LOCATIONS:
        base = {
            "date": [f"{run:%Y-%m-%d}/{run:%Y-%m-%d}"],
            "time": [f"{run:%H}:00"],
            "leadtime_hour": [str(h) for h in range(MAX_LEAD + 1)],
            "type": ["forecast"],
            "data_format": "netcdf",
            "area": ads_area(locations()[loc_id]["bbox"]),
        }
        parts = {
            "surface": {"variable": SURFACE_VARS},
            "level137": {"variable": LEVEL_VARS, "model_level": ["137"]},
        }
        for part, extra in parts.items():  # one request at a time
            target = out / f"{loc_id}_{part}.nc"
            if not target.exists():
                ads_retrieve("ADS_URL", DATASET, base | extra, target)
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


def lsm_file(loc_id: str) -> Path:
    """Where the ECMWF fetch keeps the cropped 0.1 deg land-sea mask per location (static field)."""
    return raw_dir().parent / "static" / f"ecmwf_lsm_{loc_id}.nc"


def choose_cell(lats, lons, loc: dict, lsm: xr.DataArray | None) -> grid.Cell:
    """Nearest land cell using the ECMWF land share per CAMS cell; plain nearest cell without a mask."""
    if lsm is None:
        cell = grid.nearest_cell(lats, lons, loc["lat"], loc["lon"])
        cell.method = "nearest cell (no land-sea mask available)"
        return cell
    share = grid.land_fraction_on_grid(lsm, lats, lons, CELL_SIZE)
    cell = grid.nearest_land_cell(lats, lons, share, loc["lat"], loc["lon"])
    cell.method += ", land share from ECMWF 0.1 deg lsm"
    return cell


def _at(ds: xr.Dataset, var: str, cell: grid.Cell) -> pd.Series:
    """Hourly series at the cell, indexed by valid time."""
    p = ds[var].sel(latitude=cell.lat, longitude=cell.lon, method="nearest").squeeze(drop=True)
    valid = pd.to_datetime(ds["valid_time"].values.ravel(), utc=True)
    return pd.Series(np.asarray(p.values, dtype="float64").ravel(), index=valid)


def cells_to_store(
    lats, lons, loc: dict, lsm: xr.DataArray | None, loc_id: str
) -> list[tuple[str, grid.Cell]]:
    """The default cell (nearest land cell) and, if different, the coastal cell (plain nearest cell).

    Decided after stage C review: the land cell is the default; the coastal cell is stored too under
    site_id grid:cams_global:<location>:coastal so the algorithm side can compare and choose.
    """
    land = choose_cell(lats, lons, loc, lsm)
    out = [(f"grid:{NAME}:{loc_id}", land)]
    coastal = grid.nearest_cell(lats, lons, loc["lat"], loc["lon"])
    if (coastal.lat, coastal.lon) != (land.lat, land.lon):
        coastal.method = "alternative: nearest cell incl. sea (coastal)"
        out.append((f"grid:{NAME}:{loc_id}{ALTERNATIVE_SITE_SUFFIX}", coastal))
    return out


def _cell_rows(sfc, lev, cell: grid.Cell, loc_id: str, site_id: str, run, full) -> list[pd.DataFrame]:
    """Canonical rows for one cell: PM from kg/m3, NO2/O3 from kg/kg with the air density from sp and t2m."""
    sp, t2m = _at(sfc, "sp", cell).reindex(full), _at(sfc, "t2m", cell).reindex(full)
    level = "kg/kg x air density (sp, t2m), model level 137"
    series = {
        "pm25_ugm3": (convert.kgm3_to_ugm3(_at(sfc, "pm2p5", cell)).reindex(full), None),
        "pm10_ugm3": (convert.kgm3_to_ugm3(_at(sfc, "pm10", cell)).reindex(full), None),
        "no2_ugm3": (convert.mmr_to_ugm3(_at(lev, "no2", cell).reindex(full), sp, t2m), level),
        "o3_ugm3": (convert.mmr_to_ugm3(_at(lev, "go3", cell).reindex(full), sp, t2m), level),
    }
    return [
        rows(loc_id, var, values, "ok", NAME, "forecast", run, site_id, method)
        for var, (values, method) in series.items()
    ]


def to_points(raw: Path) -> Batch:
    manifest = read_manifest(raw)
    run = utc(manifest["issue_time_utc"])
    full = pd.date_range(run, run + pd.Timedelta(hours=MAX_LEAD), freq="h")
    frames, sites = [], []
    for loc_id in LOCATIONS:
        loc = locations()[loc_id]
        lsm = None
        if lsm_file(loc_id).exists():
            with xr.open_dataset(lsm_file(loc_id)) as m:
                lsm = m["lsm"].load()
        with (
            xr.open_dataset(raw / f"{loc_id}_surface.nc") as sfc,
            xr.open_dataset(raw / f"{loc_id}_level137.nc") as lev,
        ):
            for site_id, cell in cells_to_store(sfc.latitude.values, sfc.longitude.values, loc, lsm, loc_id):
                share = f" (land {cell.land_fraction})" if cell.land_fraction is not None else ""
                sites.append(
                    [
                        site_id,
                        f"{cell.method} to {loc['lat']}, {loc['lon']}{share}",
                        cell.lat,
                        cell.lon,
                        "grid",
                        NAME,
                    ]
                )
                frames += _cell_rows(sfc, lev, cell, loc_id, site_id, run, full)
    hourly = pd.concat(frames, ignore_index=True)
    return Batch(hourly, pd.DataFrame(sites, columns=SITE_COLUMNS), cycle_rows(hourly, manifest))
