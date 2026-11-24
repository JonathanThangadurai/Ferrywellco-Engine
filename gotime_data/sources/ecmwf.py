"""ECMWF IFS open data, 0.1 deg, runs 00/06/12/18 UTC, steps 0-96 h every 3 h (weather forecast).

fetch(): find the newest published cycle, download only our fields by HTTP byte range (from the
.index file), crop each step to the city boxes right away, and delete the global GRIB.
Mirror order: AWS first, ECMWF's own server as fallback (AWS throttles with 503 SlowDown). After a
fallback the rest of the cycle uses the fallback; every fallback is logged and kept in the manifest.

to_points(): pick the nearest land cell (land-sea mask from step 0), convert units, fill the 3-hourly
steps to hourly (status 'interpolated'), and return canonical rows.
"""

import json
import logging
import time
from datetime import timedelta
from pathlib import Path

import httpx
import pandas as pd
import xarray as xr

from .. import convert, grid
from ..config import locations, raw_dir
from ..schema import SITE_COLUMNS, Batch
from ._common import batch_dir, cycle_rows, now_utc, read_manifest, rows, utc, write_manifest

log = logging.getLogger(__name__)

NAME = "ecmwf_ifs"
RESOL = "0p1"
MAX_STEP = 96
STEPS = list(range(0, MAX_STEP + 1, 3))
PARAMS = ["2t", "2d", "10u", "10v", "ssrd"]
STATIC_PARAMS = ["lsm"]  # land-sea mask, fetched at step 0 only
MIRRORS = {
    "aws": "https://ecmwf-forecasts.s3.eu-central-1.amazonaws.com",
    "ecmwf": "https://data.ecmwf.int/forecasts",
}
MIRROR_ORDER = ["aws", "ecmwf"]
RETRIES = 6  # 1+2+4+8+16+32 s of waiting at most per request
# Measured publish delays (Last-Modified on data.ecmwf.int, Nov 2026): 00/12 UTC +9h34, 06/18 UTC +8h27.
PUBLISH_DELAY = {
    0: timedelta(hours=9, minutes=34),
    12: timedelta(hours=9, minutes=34),
    6: timedelta(hours=8, minutes=27),
    18: timedelta(hours=8, minutes=27),
}


def get_with_retry(http: httpx.Client, url: str, headers: dict | None = None) -> httpx.Response:
    """GET with a fixed number of retries on 429/503; raises after the last one."""
    for attempt in range(RETRIES):
        r = http.get(url, headers=headers)
        if r.status_code not in (429, 503):
            r.raise_for_status()
            return r
        time.sleep(2**attempt)
    r.raise_for_status()
    return r


def step_path(run: pd.Timestamp, step: int) -> str:
    """Path of one step file below the mirror root, without .index/.grib2. 0.1 deg uses '-sfc' files."""
    return f"/{run:%Y%m%d}/{run:%H}z/ifs/{RESOL}/oper/{run:%Y%m%d%H}0000-{step}h-oper-fc-sfc"


class Downloader:
    """Fetches from the mirrors in MIRROR_ORDER and remembers a fallback for the rest of the cycle."""

    def __init__(self, http: httpx.Client):
        self.http = http
        self.order = list(MIRROR_ORDER)
        self.fallbacks: list[str] = []

    def get(self, path: str, headers: dict | None = None) -> httpx.Response:
        last_error = None
        for mirror in list(self.order):
            try:
                return get_with_retry(self.http, MIRRORS[mirror] + path, headers)
            except (httpx.HTTPError, httpx.TransportError) as exc:
                last_error = exc
                if mirror == self.order[-1]:
                    break
                note = f"{mirror} failed for {path} ({type(exc).__name__}); falling back"
                log.warning(note)
                self.fallbacks.append(note)
                self.order.remove(mirror)  # use the fallback for the rest of this cycle
        raise RuntimeError(f"all mirrors failed for {path}: {last_error}")

    def fields(self, path: str, params: list[str], target: Path) -> None:
        """Read the .index and download only the wanted params by byte range into one GRIB file."""
        index = [json.loads(x) for x in self.get(path + ".index").text.splitlines() if x.strip()]
        wanted = [e for e in index if e["param"] in params and e.get("levtype") == "sfc"]
        if len(wanted) != len(params):
            raise RuntimeError(f"{path}: expected {len(params)} fields, index has {len(wanted)}")
        with open(target, "wb") as f:
            for e in wanted:
                start, end = e["_offset"], e["_offset"] + e["_length"] - 1
                f.write(self.get(path + ".grib2", {"Range": f"bytes={start}-{end}"}).content)


def latest_run(http: httpx.Client, now=None) -> tuple[pd.Timestamp, pd.Timestamp | None]:
    """Newest cycle whose last step is on the server: (issue time, publish time from Last-Modified)."""
    now = pd.Timestamp(now or now_utc())
    base = now.floor("6h")
    for k in range(8):
        run = base - pd.Timedelta(hours=6 * k)
        if now < run + PUBLISH_DELAY[run.hour] - pd.Timedelta(minutes=30):
            continue
        for mirror in MIRROR_ORDER:
            r = http.head(MIRRORS[mirror] + step_path(run, MAX_STEP) + ".index")
            if r.status_code == 200:
                lm = r.headers.get("last-modified")
                return run, (utc(pd.to_datetime(lm)) if lm else None)
    raise RuntimeError("no complete ECMWF cycle found in the last 48 h")


def fetch(now=None) -> Path:
    """Download and crop the newest cycle. Returns the raw batch folder."""
    import cfgrib

    with httpx.Client(timeout=120) as http:
        run, published = latest_run(http, now)
        out = batch_dir(NAME, f"{run:%Y%m%d%H}")
        if (out / "manifest.json").exists() and not read_manifest(out).get("missing_steps"):
            log.info("ECMWF %s already fetched", run)
            return out
        dl = Downloader(http)
        missing = []
        for step in STEPS:
            grib = out / f"step{step:03d}.grib2"
            try:
                dl.fields(step_path(run, step), PARAMS + (STATIC_PARAMS if step == 0 else []), grib)
            except RuntimeError as exc:
                log.error("ECMWF %s step %d missing: %s", run, step, exc)
                missing.append(step)
                continue
            datasets = cfgrib.open_datasets(str(grib), backend_kwargs={"indexpath": ""})
            for loc_id, loc in locations().items():
                parts = [grid.crop(ds, loc["bbox"]).load() for ds in datasets]
                merged = xr.merge(parts, compat="override", join="exact")
                merged = merged.drop_vars(["heightAboveGround", "surface"], errors="ignore")
                merged.to_netcdf(out / f"{loc_id}_step{step:03d}.nc", engine="h5netcdf")
                if "lsm" in merged:  # static land-sea mask, also used for CAMS global point selection
                    static = raw_dir().parent / "static"
                    static.mkdir(parents=True, exist_ok=True)
                    merged[["lsm"]].to_netcdf(static / f"ecmwf_lsm_{loc_id}.nc", engine="h5netcdf")
            for ds in datasets:
                ds.close()
            grib.unlink()  # never keep the global field
    write_manifest(
        out,
        {
            "source": NAME,
            "issue_time_utc": run,
            "publish_time_utc": published,
            "fetched_time_utc": now_utc(),
            "steps": STEPS,
            "missing_steps": missing,
            "mirror_fallbacks": dl.fallbacks,
        },
    )
    return out


def _land_cell(raw: Path, loc_id: str, loc: dict) -> grid.Cell:
    """Nearest land cell from the step-0 land-sea mask; plain nearest cell if lsm is missing."""
    with xr.open_dataset(raw / f"{loc_id}_step000.nc") as ds:
        if "lsm" not in ds:
            cell = grid.nearest_cell(ds.latitude, ds.longitude, loc["lat"], loc["lon"])
            cell.method = "nearest cell (lsm not available)"
            return cell
        return grid.nearest_land_cell(ds.latitude, ds.longitude, ds["lsm"].values, loc["lat"], loc["lon"])


def _series(raw: Path, loc_id: str, cell: grid.Cell, run: pd.Timestamp, steps: list[int]) -> pd.DataFrame:
    """Raw values at the chosen cell, one row per step (NaN for missing step files)."""
    records = []
    for step in steps:
        valid = run + pd.Timedelta(hours=step)
        path = raw / f"{loc_id}_step{step:03d}.nc"
        rec = {"valid": valid}
        if path.exists():
            with xr.open_dataset(path) as ds:
                p = ds.sel(latitude=cell.lat, longitude=cell.lon, method="nearest")
                rec |= {v: float(p[v]) for v in ("t2m", "d2m", "u10", "v10", "ssrd") if v in p}
        records.append(rec)
    return pd.DataFrame(records).set_index("valid").reindex(columns=["t2m", "d2m", "u10", "v10", "ssrd"])


def ssrd_hourly(accum: pd.Series) -> pd.DataFrame:
    """Accumulated ssrd (J/m2 since run start, 3-hourly) -> hourly rows of W/m2.

    Each 3-hour mean is given to the 3 hours inside its interval (valid_time = start of the hour), with
    status 'interpolated' because the value is a 3-hour mean, not that hour's own mean.
    """
    rate = convert.deaccumulate(accum)  # mean over the interval ending at each step
    out = {}
    for end, value in rate.iloc[1:].items():
        for h in (3, 2, 1):
            out[end - pd.Timedelta(hours=h)] = value
    values = pd.Series(out, dtype="float64").sort_index()
    return pd.DataFrame({"value": values, "status": "interpolated"})


def to_points(raw: Path) -> Batch:
    """Turn one raw cycle into canonical rows, sites (the chosen cell) and cycle records."""
    manifest = read_manifest(raw)
    run = utc(manifest["issue_time_utc"])
    frames, sites = [], []
    for loc_id, loc in locations().items():
        if not (raw / f"{loc_id}_step000.nc").exists():
            log.warning("ECMWF %s: no step 0 for %s", run, loc_id)
            continue
        cell = _land_cell(raw, loc_id, loc)
        site_id = f"grid:{NAME}:{loc_id}"
        sites.append(
            [
                site_id,
                f"{cell.method} to {loc['lat']}, {loc['lon']} (lsm {cell.land_fraction})",
                cell.lat,
                cell.lon,
                "grid",
                NAME,
            ]
        )
        s = _series(raw, loc_id, cell, run, manifest.get("steps", STEPS))
        t_c, td_c = convert.kelvin_to_celsius(s["t2m"]), convert.kelvin_to_celsius(s["d2m"])
        instant = {
            "t2m_c": t_c,
            "td2m_c": td_c,
            "rh2m_pct": convert.rh_from_t_td(t_c, td_c),
            "wind10m_ms": convert.wind_speed(s["u10"], s["v10"]),
        }
        for var, series in instant.items():
            filled = convert.fill_to_hourly(series)
            frames.append(
                rows(loc_id, var, filled["value"], filled["status"], NAME, "forecast", run, site_id)
            )
        ss = ssrd_hourly(s["ssrd"])
        frames.append(
            rows(
                loc_id,
                "ssrd_wm2",
                ss["value"],
                ss["status"],
                NAME,
                "forecast",
                run,
                site_id,
                method="3-hour mean spread over its hours",
            )
        )
    hourly = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    return Batch(hourly, pd.DataFrame(sites, columns=SITE_COLUMNS), cycle_rows(hourly, manifest))
