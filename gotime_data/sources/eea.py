"""EEA E2a (up-to-date, unverified) hourly air quality observations, Greater Amsterdam.

Start/End have no time zone and are UTC+1 all year for NL (verified against Luchtmeetnet), and Start
is the start of the hour, so valid_time_utc = Start - 1 h. Each file is the whole year to date for
one sampling point; we keep the last LOOKBACK_DAYS. Validity <= 0 means invalid -> null + missing.
"""

import logging
from datetime import timedelta
from pathlib import Path

import httpx
import pandas as pd

from .. import convert
from ..schema import SITE_COLUMNS, Batch
from ._common import batch_dir, now_utc, read_manifest, rows, utc, write_manifest

log = logging.getLogger(__name__)

NAME = "eea_e2a"
API = "https://eeadmz1-downloads-api-appservice.azurewebsites.net/ParquetFile/urls"
COUNTRY, CITY, LOCATION = "NL", "Greater Amsterdam", "amsterdam"
POLLUTANTS = {6001: "pm25_ugm3", 5: "pm10_ugm3", 8: "no2_ugm3", 7: "o3_ugm3"}
LOOKBACK_DAYS = 3


def fetch(now=None) -> Path:
    fetched = pd.Timestamp(now or now_utc())
    out = batch_dir(NAME, f"{fetched:%Y%m%d%H%M}")
    with httpx.Client(timeout=120) as http:
        for code in POLLUTANTS:
            body = {
                "countries": [COUNTRY],
                "cities": [CITY],
                "dataset": 1,
                "aggregationType": "hour",
                "pollutants": [f"http://dd.eionet.europa.eu/vocabulary/aq/pollutant/{code}"],
            }
            r = http.post(API, json=body)
            r.raise_for_status()
            for url in (u for u in r.text.splitlines() if u.startswith("http")):
                (out / url.rsplit("/", 1)[-1]).write_bytes(http.get(url).raise_for_status().content)
    write_manifest(out, {"source": NAME, "fetched_time_utc": fetched})
    return out


def file_to_rows(df: pd.DataFrame, since: pd.Timestamp) -> pd.DataFrame:
    """Rows for one sampling-point file from `since` to its last hour, gaps filled as missing."""
    var = POLLUTANTS.get(int(df["Pollutant"].iloc[0]))
    if var is None or (df["Unit"] != "ug.m-3").any():
        log.warning(
            "EEA: skipping %s (pollutant %s, unit %s)",
            df["Samplingpoint"].iloc[0],
            df["Pollutant"].iloc[0],
            df["Unit"].unique(),
        )
        return pd.DataFrame()
    valid = convert.eea_to_utc(df["Start"], COUNTRY)
    issue = convert.eea_to_utc(df["ResultTime"], COUNTRY)  # assumed same clock as Start
    value = pd.to_numeric(df["Value"], errors="coerce").where(df["Validity"] > 0)
    keep = valid >= since
    frame = pd.DataFrame(
        {"value": value[keep].to_numpy(), "issue": issue[keep].to_numpy()},
        index=pd.DatetimeIndex(valid[keep]),
    )
    frame = frame[~frame.index.duplicated(keep="last")]
    if frame.empty:
        return pd.DataFrame()
    full = pd.date_range(since, frame.index.max(), freq="h")
    frame = frame.reindex(full)
    frame["issue"] = frame["issue"].fillna(frame["issue"].max())  # gap rows: issue = file's newest time
    site = "eea:" + df["Samplingpoint"].iloc[0]
    return rows(LOCATION, var, frame["value"], "ok", NAME, "observation", frame["issue"], site)


def to_points(raw: Path) -> Batch:
    fetched = utc(read_manifest(raw)["fetched_time_utc"])
    since = (fetched - timedelta(days=LOOKBACK_DAYS)).floor("h")
    frames, sites = [], []
    for path in sorted(raw.glob("*.parquet")):
        df = pd.read_parquet(path)
        if df.empty:
            continue
        part = file_to_rows(df, since)
        if not part.empty:
            frames.append(part)
            # Station type and position need the EEA station metadata (open item); null until then.
            sites.append([part["site_id"].iloc[0], df["Samplingpoint"].iloc[0], None, None, None, NAME])
    hourly = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    return Batch(hourly, pd.DataFrame(sites, columns=SITE_COLUMNS))
