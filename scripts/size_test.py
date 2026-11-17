"""Stage B size test: download one forecast cycle (or one day) per source, crop to the city boxes,
and record bytes downloaded, bytes kept, wall time and peak memory.

Run one source per process so peak memory is per source:
    uv run python scripts/size_test.py ecmwf --resol 0p1
    uv run python scripts/size_test.py ecmwf --resol 0p25
    uv run python scripts/size_test.py cams_eu | cams_global | era5 | eea | knmi

Results are appended to data/size-test/results.jsonl. Files land in data/size-test/<source>/.
This is a measurement script, not the pipeline (that is stage C).
"""

import argparse
import json
import resource
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import xarray as xr
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from gotime_data import keys  # noqa: E402

OUT = ROOT / "data" / "size-test"
LOCATIONS = yaml.safe_load((ROOT / "config" / "locations.yaml").read_text())["locations"]


def peak_rss_mb() -> float:
    """Peak resident memory of this process. macOS reports bytes, Linux kilobytes."""
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return peak / 1e6 if sys.platform == "darwin" else peak / 1e3


def ads_area(loc: dict) -> list[float]:
    """config bbox [lat_min, lat_max, lon_min, lon_max] -> ADS/CDS area [N, W, S, E]."""
    lat_min, lat_max, lon_min, lon_max = loc["bbox"]
    return [lat_max, lon_min, lat_min, lon_max]


def crop(ds: xr.Dataset, loc: dict) -> xr.Dataset:
    """Cut a lat/lon grid to the city box. Handles descending latitude (GRIB) and ascending."""
    lat_min, lat_max, lon_min, lon_max = loc["bbox"]
    lat = ds["latitude"]
    lat_slice = slice(lat_max, lat_min) if lat[0] > lat[-1] else slice(lat_min, lat_max)
    return ds.sel(latitude=lat_slice, longitude=slice(lon_min, lon_max))


def record(result: dict) -> None:
    """Append one result line and print it."""
    result["peak_rss_mb"] = round(peak_rss_mb(), 1)
    OUT.mkdir(parents=True, exist_ok=True)
    with open(OUT / "results.jsonl", "a") as f:
        f.write(json.dumps(result) + "\n")
    print(json.dumps(result))


def dir_bytes(path: Path) -> int:
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())


# ---------------------------------------------------------------- ECMWF

ECMWF_PARAMS = ["2t", "2d", "10u", "10v", "ssrd"]
ECMWF_MIRRORS = {
    "aws": "https://ecmwf-forecasts.s3.eu-central-1.amazonaws.com",
    "ecmwf": "https://data.ecmwf.int/forecasts",
}


def get_with_backoff(http: httpx.Client, url: str, headers: dict | None = None) -> httpx.Response:
    """GET with a bounded retry on 429/503 (AWS answers 503 SlowDown when throttling)."""
    for attempt in range(6):
        r = http.get(url, headers=headers)
        if r.status_code not in (429, 503):
            r.raise_for_status()
            return r
        time.sleep(2**attempt)
    r.raise_for_status()
    return r


def ecmwf_step_url(mirror: str, run: datetime, resol: str, step: int) -> str:
    """URL of one step file (without .grib2/.index). 0.1 deg files are split; we need '-sfc'."""
    suffix = "-sfc" if resol == "0p1" else ""
    return (
        f"{ECMWF_MIRRORS[mirror]}/{run:%Y%m%d}/{run:%H}z/ifs/{resol}/oper/"
        f"{run:%Y%m%d%H}0000-{step}h-oper-fc{suffix}"
    )


def fetch_ecmwf_fields(http: httpx.Client, base_url: str, target: Path) -> int:
    """Read the .index, then download only our fields by HTTP byte range into one GRIB file."""
    index = get_with_backoff(http, base_url + ".index").text.splitlines()
    entries = [json.loads(line) for line in index if line.strip().startswith("{")]
    wanted = [e for e in entries if e["param"] in ECMWF_PARAMS and e.get("levtype") == "sfc"]
    if len(wanted) != len(ECMWF_PARAMS):
        raise RuntimeError(f"expected {len(ECMWF_PARAMS)} fields, index has {len(wanted)}")
    with open(target, "wb") as f:
        for e in wanted:
            start, length = e["_offset"], e["_length"]
            rng = {"Range": f"bytes={start}-{start + length - 1}"}
            f.write(get_with_backoff(http, base_url + ".grib2", headers=rng).content)
    return target.stat().st_size


def run_ecmwf(resol: str, max_step: int, mirror: str) -> None:
    """One 00z run (yesterday), steps 0..max_step every 3 h, our 5 fields; each step cropped then deleted."""
    import cfgrib

    run = (datetime.now(UTC) - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    out_dir = OUT / f"ecmwf_{resol}"
    out_dir.mkdir(parents=True, exist_ok=True)
    downloaded = 0
    t0 = time.monotonic()
    http = httpx.Client(timeout=120)
    for step in range(0, max_step + 1, 3):
        grib = out_dir / f"step{step:03d}.grib2"
        downloaded += fetch_ecmwf_fields(http, ecmwf_step_url(mirror, run, resol, step), grib)
        datasets = cfgrib.open_datasets(str(grib), backend_kwargs={"indexpath": ""})
        for loc_id, loc in LOCATIONS.items():
            parts = [crop(ds, loc).load() for ds in datasets]
            merged = xr.merge(parts, compat="override", join="exact")
            merged = merged.drop_vars(["heightAboveGround"], errors="ignore")
            merged.to_netcdf(out_dir / f"{loc_id}_step{step:03d}.nc", engine="h5netcdf")
        for ds in datasets:
            ds.close()
        grib.unlink()  # never keep the global field
    record(
        {
            "source": f"ecmwf_{resol}",
            "mirror": mirror,
            "run": f"{run:%Y-%m-%d %H}z",
            "steps": f"0-{max_step} every 3 h",
            "downloaded_mb": round(downloaded / 1e6, 1),
            "kept_mb": round(dir_bytes(out_dir) / 1e6, 2),
            "seconds": round(time.monotonic() - t0, 1),
        }
    )


# ---------------------------------------------------------------- CAMS / ERA5 (ADS, CDS)

CAMS_EU_VARS = [
    "particulate_matter_2.5um",
    "particulate_matter_10um",
    "nitrogen_dioxide",
    "ozone",
    "alder_pollen",
    "birch_pollen",
    "grass_pollen",
    "mugwort_pollen",
    "olive_pollen",
    "ragweed_pollen",
]
# CAMS global: PM and the met fields for unit conversion are single-level ("surface") fields.
# NO2 and O3 exist only on model levels: asked together with the surface fields they are dropped
# silently, so they come in a second request at model level 137 (the lowest level, ~10 m).
CAMS_GLOBAL_SURFACE_VARS = [
    "particulate_matter_2.5um",
    "particulate_matter_10um",
    "surface_pressure",
    "2m_temperature",
]
CAMS_GLOBAL_LEVEL_VARS = ["nitrogen_dioxide", "ozone"]


def cds_client(url_name: str):
    import cdsapi

    return cdsapi.Client(url=keys.get(url_name), key=keys.get("COPERNICUS_KEY"), quiet=True)


def timed_retrieve(client, dataset: str, request: dict, target: Path) -> dict:
    target.parent.mkdir(parents=True, exist_ok=True)
    t0 = time.monotonic()
    client.retrieve(dataset, request, str(target))
    return {"seconds": round(time.monotonic() - t0, 1), "mb": round(target.stat().st_size / 1e6, 3)}


def open_and_touch(path: Path) -> list[str]:
    """Open the file and load it, so peak memory includes reading it. Returns variable names."""
    with xr.open_dataset(path) as ds:
        ds.load()
        return sorted(ds.data_vars)


def run_cams_eu(day: str) -> None:
    loc = LOCATIONS["amsterdam"]
    target = OUT / "cams_eu" / f"amsterdam_{day}.nc"
    stats = timed_retrieve(
        cds_client("ADS_URL"),
        "cams-europe-air-quality-forecasts",
        {
            "variable": CAMS_EU_VARS,
            "model": ["ensemble"],
            "level": ["0"],
            "date": [f"{day}/{day}"],
            "type": ["forecast"],
            "time": ["00:00"],
            "leadtime_hour": [str(h) for h in range(97)],
            "data_format": "netcdf",
            "area": ads_area(loc),
        },
        target,
    )
    record(
        {
            "source": "cams_eu",
            "run": f"{day} 00z",
            "steps": "0-96 h hourly",
            "vars": open_and_touch(target),
            **stats,
        }
    )


def run_cams_global(day: str) -> None:
    loc = LOCATIONS["chennai"]
    client = cds_client("ADS_URL")
    base = {
        "date": [f"{day}/{day}"],
        "time": ["00:00"],
        "leadtime_hour": [str(h) for h in range(121)],
        "type": ["forecast"],
        "data_format": "netcdf",
        "area": ads_area(loc),
    }
    parts = {
        "surface": {"variable": CAMS_GLOBAL_SURFACE_VARS},
        "level137": {"variable": CAMS_GLOBAL_LEVEL_VARS, "model_level": ["137"]},
    }
    for part, extra in parts.items():
        target = OUT / "cams_global" / f"chennai_{day}_{part}.nc"
        stats = timed_retrieve(client, "cams-global-atmospheric-composition-forecasts", base | extra, target)
        record(
            {
                "source": f"cams_global_{part}",
                "run": f"{day} 00z",
                "steps": "0-120 h hourly",
                "vars": open_and_touch(target),
                **stats,
            }
        )


def run_era5(day: datetime) -> None:
    """One day, both cities, our weather variables."""
    client = cds_client("CDS_URL")
    for loc_id, loc in LOCATIONS.items():
        target = OUT / "era5" / f"{loc_id}_{day:%Y-%m-%d}.nc"
        stats = timed_retrieve(
            client,
            "reanalysis-era5-single-levels",
            {
                "product_type": ["reanalysis"],
                "variable": [
                    "2m_temperature",
                    "2m_dewpoint_temperature",
                    "10m_u_component_of_wind",
                    "10m_v_component_of_wind",
                    "surface_solar_radiation_downwards",
                ],
                "year": [f"{day:%Y}"],
                "month": [f"{day:%m}"],
                "day": [f"{day:%d}"],
                "time": [f"{h:02d}:00" for h in range(24)],
                "data_format": "netcdf",
                "download_format": "unarchived",
                "area": ads_area(loc),
            },
            target,
        )
        record(
            {
                "source": "era5",
                "location": loc_id,
                "run": f"{day:%Y-%m-%d}",
                "vars": open_and_touch(target),
                **stats,
            }
        )


# ---------------------------------------------------------------- EEA and KNMI (observations)

EEA_POLLUTANTS = {"pm25": 6001, "pm10": 5, "no2": 8, "o3": 7}


def run_eea() -> None:
    """All Greater Amsterdam sampling points for our 4 pollutants (each file = year to date)."""
    import pandas as pd

    api = "https://eeadmz1-downloads-api-appservice.azurewebsites.net/ParquetFile/urls"
    out_dir = OUT / "eea"
    out_dir.mkdir(parents=True, exist_ok=True)
    t0 = time.monotonic()
    downloaded, files, rows_last_day = 0, 0, 0
    yesterday = datetime.now(UTC) - timedelta(days=1)
    with httpx.Client(timeout=120) as http:
        for name, code in EEA_POLLUTANTS.items():
            body = {
                "countries": ["NL"],
                "cities": ["Greater Amsterdam"],
                "pollutants": [f"http://dd.eionet.europa.eu/vocabulary/aq/pollutant/{code}"],
                "dataset": 1,
                "aggregationType": "hour",
            }
            urls = [u for u in http.post(api, json=body).text.splitlines() if u.startswith("http")]
            for url in urls:
                path = out_dir / f"{name}_{url.rsplit('/', 1)[-1]}"
                path.write_bytes(http.get(url).content)
                downloaded += path.stat().st_size
                files += 1
                df = pd.read_parquet(path)
                rows_last_day += int((df["Start"].dt.date == yesterday.date()).sum())
    record(
        {
            "source": "eea_e2a",
            "files": files,
            "downloaded_mb": round(downloaded / 1e6, 2),
            "rows_for_one_day": rows_last_day,
            "seconds": round(time.monotonic() - t0, 1),
        }
    )


def run_knmi() -> None:
    """One day of hourly observations at Schiphol via EDR."""
    base = "https://api.dataplatform.knmi.nl/edr/v1/collections/hourly-in-situ-meteorological-observations"
    headers = {"Authorization": keys.get("KNMI_EDR_API_KEY")}
    out_dir = OUT / "knmi"
    out_dir.mkdir(parents=True, exist_ok=True)
    t0 = time.monotonic()
    with httpx.Client(timeout=120, headers=headers) as http:
        locs = http.get(f"{base}/locations").json()["features"]
        schiphol = next(f for f in locs if "schiphol" in f["properties"].get("name", "").lower())
        day = (datetime.now(UTC) - timedelta(days=1)).date()
        r = http.get(
            f"{base}/locations/{schiphol['id']}",
            params={"datetime": f"{day}T00:00:00Z/{day}T23:59:59Z"},
        )
        r.raise_for_status()
        path = out_dir / f"schiphol_{day}.json"
        path.write_bytes(r.content)
    params = sorted(r.json().get("parameters", {}).keys())
    record(
        {
            "source": "knmi_hourly",
            "station": schiphol["id"],
            "downloaded_mb": round(path.stat().st_size / 1e6, 3),
            "parameters": params,
            "seconds": round(time.monotonic() - t0, 1),
        }
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", choices=["ecmwf", "cams_eu", "cams_global", "era5", "eea", "knmi"])
    parser.add_argument("--resol", default="0p1", choices=["0p1", "0p25"])
    parser.add_argument("--max-step", type=int, default=96)
    parser.add_argument("--mirror", default="aws", choices=list(ECMWF_MIRRORS))
    args = parser.parse_args()

    yesterday = (datetime.now(UTC) - timedelta(days=1)).strftime("%Y-%m-%d")
    if args.source == "ecmwf":
        run_ecmwf(args.resol, args.max_step, args.mirror)
    elif args.source == "cams_eu":
        run_cams_eu(yesterday)
    elif args.source == "cams_global":
        run_cams_global(yesterday)
    elif args.source == "era5":
        run_era5(datetime.now(UTC) - timedelta(days=10))
    elif args.source == "eea":
        run_eea()
    elif args.source == "knmi":
        run_knmi()


if __name__ == "__main__":
    main()
