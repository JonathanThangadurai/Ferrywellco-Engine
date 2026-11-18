"""KNMI hourly in-situ observations via the EDR API (weather observations, Schiphol).

Field traps (tested in tests/test_knmi.py):
- RH is precipitation in mm, NOT relative humidity. Humidity is U (%).
- Q is global radiation in J/cm2 summed over the hour -> W/m2 = Q x 10 000 / 3600.
- T and TD are measured at 1.5 m (models give 2 m); stored as t2m_c/td2m_c with method "KNMI 1.5 m".
- Time stamps are whole UTC hours. Hourly sums/means (Q, RH, FH) cover the hour ENDING at the stamp,
  so valid_time_utc = stamp - 1 h. T, TD, U are instant values at the stamp.
"""

import json
import logging
from datetime import timedelta
from pathlib import Path

import httpx
import pandas as pd

from .. import convert, keys
from ..schema import SITE_COLUMNS, Batch
from ._common import batch_dir, now_utc, read_manifest, rows, write_manifest

log = logging.getLogger(__name__)

NAME = "knmi_hourly"
BASE = "https://api.dataplatform.knmi.nl/edr/v1/collections/hourly-in-situ-meteorological-observations"
STATIONS = {"amsterdam": ("0-20000-0-06240", "knmi:06240", "Schiphol")}
LOOKBACK_DAYS = 2

# KNMI parameter -> (our variable, time rule, conversion, method note)
FIELDS = {
    "T": ("t2m_c", "instant", None, "KNMI 1.5 m"),
    "TD": ("td2m_c", "instant", None, "KNMI 1.5 m"),
    "U": ("rh2m_pct", "instant", None, "KNMI 1.5 m"),
    "FH": ("wind10m_ms", "hour_ending", None, None),
    "Q": ("ssrd_wm2", "hour_ending", convert.jcm2_per_hour_to_wm2, "J/cm2 per hour -> W/m2"),
    "RH": ("precip_mm", "hour_ending", lambda x: x.clip(lower=0), "negative (<0.05 mm) -> 0"),
}


def fetch(now=None) -> Path:
    fetched = pd.Timestamp(now or now_utc())
    out = batch_dir(NAME, f"{fetched:%Y%m%d%H%M}")
    start = (fetched - timedelta(days=LOOKBACK_DAYS)).floor("h")
    with httpx.Client(timeout=120, headers={"Authorization": keys.get("KNMI_EDR_API_KEY")}) as http:
        for loc_id, (station, _, _) in STATIONS.items():
            r = http.get(
                f"{BASE}/locations/{station}",
                params={"datetime": f"{start:%Y-%m-%dT%H:%M:%SZ}/{fetched:%Y-%m-%dT%H:%M:%SZ}"},
            )
            r.raise_for_status()
            (out / f"{loc_id}.json").write_bytes(r.content)
    write_manifest(out, {"source": NAME, "fetched_time_utc": fetched})
    return out


def coverage_to_rows(cov_json: dict, loc_id: str) -> tuple[pd.DataFrame, list]:
    """CoverageJSON (one station) -> canonical rows and its sites row."""
    _, site_id, name = STATIONS[loc_id]
    cov = cov_json["coverages"][0]
    axes = cov["domain"]["axes"]
    stamps = pd.to_datetime(axes["t"]["values"], utc=True)
    frames = []
    for param, (var, rule, conv, method) in FIELDS.items():
        if param not in cov["ranges"]:
            continue
        values = pd.Series(cov["ranges"][param]["values"], dtype="float64")
        if conv is not None:
            values = conv(values)
        valid = stamps - pd.Timedelta(hours=1) if rule == "hour_ending" else stamps
        values.index = valid
        # issue time for observations: the end of the observed hour (when it could first be known)
        frames.append(
            rows(
                loc_id,
                var,
                values,
                "ok",
                NAME,
                "observation",
                pd.Series(stamps, index=valid),
                site_id,
                method,
            )
        )
    site = [site_id, name, axes["y"]["values"][0], axes["x"]["values"][0], "weather", NAME]
    return pd.concat(frames, ignore_index=True), site


def to_points(raw: Path) -> Batch:
    read_manifest(raw)
    frames, sites = [], []
    for loc_id in STATIONS:
        path = raw / f"{loc_id}.json"
        if path.exists():
            df, site = coverage_to_rows(json.loads(path.read_text()), loc_id)
            frames.append(df)
            sites.append(site)
    hourly = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    return Batch(hourly, pd.DataFrame(sites, columns=SITE_COLUMNS))
