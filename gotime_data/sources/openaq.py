"""OpenAQ v3: CPCB/TNPCB station observations around Chennai. INTERNAL USE ONLY.

Used to verify and later bias-correct the Chennai forecast. OpenAQ data never appears in output for
partners (the read interface leaves it out unless include_internal=True), and it is never committed
to this public repo (test fixtures are synthetic). Licence/terms: docs/data-sources.md section 10.

- Hours are local Indian hours, so periods start at :30 UTC (IST = UTC+5:30); valid_time_utc = period start.
- NO2 (and O3 when it comes in ppb) is converted to ug/m3 with the station's measured temperature and
  pressure where available, else 25 C / 1013.25 hPa; the `method` column says which.
- A station can list the same pollutant twice: keep the non-empty series; if both have values, keep the
  one with more hours and log the other.
"""

import json
import logging
import os
import time
from datetime import timedelta
from pathlib import Path

import httpx
import pandas as pd

from .. import convert, keys
from ..schema import SITE_COLUMNS, Batch
from ._common import batch_dir, now_utc, read_manifest, rows, write_manifest

log = logging.getLogger(__name__)

NAME = "openaq"
API = "https://api.openaq.org/v3"
LOCATION, CENTRE, RADIUS_M = "chennai", "13.08,80.27", 25000
LOOKBACK_DAYS = 2
POLLUTANTS = {"pm25": "pm25_ugm3", "pm10": "pm10_ugm3", "no2": "no2_ugm3", "o3": "o3_ugm3"}
MET = {"temperature", "pressure"}
GASES = {"no2", "o3"}


def fetch(now=None) -> Path:
    """Save, per active station, the location record and the hourly series of each wanted sensor."""
    fetched = pd.Timestamp(now or now_utc())
    out = batch_dir(NAME, f"{fetched:%Y%m%d%H%M}")
    keys.load_keys()
    start = fetched - timedelta(days=LOOKBACK_DAYS)
    with httpx.Client(timeout=60, headers={"X-API-Key": os.environ["OPENAQ_API_KEY"]}) as http:
        locs = http.get(f"{API}/locations", params={"coordinates": CENTRE, "radius": RADIUS_M, "limit": 100})
        locs.raise_for_status()
        for loc in locs.json()["results"]:
            last = (loc.get("datetimeLast") or {}).get("utc") or ""
            if last < f"{start:%Y-%m-%d}":
                continue  # station no longer reporting
            sensors = []
            for s in loc["sensors"]:
                if s["parameter"]["name"] in set(POLLUTANTS) | MET:
                    r = http.get(
                        f"{API}/sensors/{s['id']}/hours",
                        params={
                            "datetime_from": start.isoformat(),
                            "datetime_to": fetched.isoformat(),
                            "limit": 1000,
                        },
                    )
                    r.raise_for_status()
                    sensors.append({"sensor": s, "results": r.json()["results"]})
                    time.sleep(1.1)  # free key: 60 requests per minute
            (out / f"{loc['id']}.json").write_text(json.dumps({"location": loc, "sensors": sensors}))
    write_manifest(out, {"source": NAME, "fetched_time_utc": fetched})
    return out


def sensor_series(entry: dict) -> pd.Series:
    """Hourly values of one sensor, indexed by period start (UTC)."""
    res = entry["results"]
    idx = pd.to_datetime([r["period"]["datetimeFrom"]["utc"] for r in res], utc=True)
    return pd.Series([r["value"] for r in res], index=idx, dtype="float64").sort_index()


def pick_sensor(entries: list[dict], station: str, parameter: str) -> dict | None:
    """One sensor per parameter: the non-empty one; if several have values, the one with most hours."""
    with_data = sorted((e for e in entries if e["results"]), key=lambda e: len(e["results"]), reverse=True)
    if not with_data:
        return None
    for dropped in with_data[1:]:
        log.warning(
            "OpenAQ %s: %s listed twice; keeping sensor %s (%d h), dropping %s (%d h)",
            station,
            parameter,
            with_data[0]["sensor"]["id"],
            len(with_data[0]["results"]),
            dropped["sensor"]["id"],
            len(dropped["results"]),
        )
    return with_data[0]


def station_to_rows(station: dict) -> tuple[pd.DataFrame, list]:
    loc = station["location"]
    site_id, name = f"openaq:{loc['id']}", loc["name"]
    by_param: dict[str, list] = {}
    for e in station["sensors"]:
        by_param.setdefault(e["sensor"]["parameter"]["name"], []).append(e)
    chosen = {p: pick_sensor(es, name, p) for p, es in by_param.items()}
    met = {p: sensor_series(chosen[p]) for p in MET if chosen.get(p)}
    frames = []
    for param, var in POLLUTANTS.items():
        entry = chosen.get(param)
        if entry is None:
            continue
        values = sensor_series(entry)
        values = values.reindex(pd.date_range(values.index.min(), values.index.max(), freq="h"))
        unit = entry["sensor"]["parameter"]["units"]
        method = None
        if unit == "ppb" and param in GASES:
            values, method = convert.ppb_to_ugm3(values, param, met.get("temperature"), met.get("pressure"))
        elif unit not in ("µg/m³", "ug/m3"):
            log.warning("OpenAQ %s: %s in %s, skipped", name, param, unit)
            continue
        issue = pd.Series(values.index + pd.Timedelta(hours=1), index=values.index)  # end of the hour
        frames.append(rows(LOCATION, var, values, "ok", NAME, "observation", issue, site_id, method))
    coords = loc.get("coordinates") or {}
    site = [site_id, name, coords.get("latitude"), coords.get("longitude"), None, NAME]
    return (pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()), site


def to_points(raw: Path) -> Batch:
    read_manifest(raw)
    frames, sites = [], []
    for path in sorted(raw.glob("*.json")):
        if path.name == "manifest.json":
            continue
        df, site = station_to_rows(json.loads(path.read_text()))
        if not df.empty:
            frames.append(df)
            sites.append(site)
    hourly = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    return Batch(hourly, pd.DataFrame(sites, columns=SITE_COLUMNS))
