"""Write a Batch (hourly rows, sites, cycles) to storage, after validating it.

Hourly rows are merged into one Parquet file per source and date: existing rows are kept, new rows
are added, and only exact duplicates (the same row ingested twice) are dropped. A changed value comes
with a different issue time, so it is added next to the old one; raw values are never overwritten.
"""

import logging
import uuid

import pandas as pd

from ..config import filesystem
from ..schema import CYCLE_COLUMNS, SITE_COLUMNS, Batch, normalise, validate

log = logging.getLogger(__name__)
KEY = ["location_id", "valid_time_utc", "variable", "source", "issue_time_utc", "site_id"]


def _write_parquet(fs, path: str, df: pd.DataFrame) -> None:
    """Write via a temporary file and rename, so a crash never leaves half a file."""
    fs.makedirs(path.rsplit("/", 1)[0], exist_ok=True)
    tmp = f"{path}.{uuid.uuid4().hex}.tmp"
    with fs.open(tmp, "wb") as f:
        df.to_parquet(f, index=False, compression="zstd")
    fs.mv(tmp, path)


def partition_date(df: pd.DataFrame) -> pd.Series:
    """UTC date used for the folder: issue date for forecasts, valid date otherwise."""
    t = df["issue_time_utc"].where(df["kind"] == "forecast", df["valid_time_utc"])
    return t.dt.strftime("%Y-%m-%d")


def write_hourly(df: pd.DataFrame, layer: str = "live") -> int:
    """Merge rows into <layer>/hourly/source=/date=/part.parquet. Returns the number of new rows."""
    fs, root = filesystem()
    df = normalise(df)
    added = 0
    for (source, day), part in df.groupby([df["source"], partition_date(df)]):
        path = f"{root}/{layer}/hourly/source={source}/date={day}/part.parquet"
        old = normalise(pd.read_parquet(fs.open(path, "rb"))) if fs.exists(path) else None
        merged = part if old is None else pd.concat([old, part], ignore_index=True)
        merged = merged.drop_duplicates(KEY, keep="first")
        added += len(merged) - (0 if old is None else len(old))
        _write_parquet(fs, path, merged.sort_values(KEY[:3]))
    return added


def write_sites(sites: pd.DataFrame) -> None:
    """Upsert stations and chosen grid cells into meta/sites/<source>.parquet (newest row per site wins)."""
    if sites.empty:
        return
    fs, root = filesystem()
    for source, part in sites.groupby("source"):
        path = f"{root}/meta/sites/{source}.parquet"
        if fs.exists(path):
            part = pd.concat([pd.read_parquet(fs.open(path, "rb")), part], ignore_index=True)
        part = part[SITE_COLUMNS].drop_duplicates("site_id", keep="last")
        _write_parquet(fs, path, part)


def write_cycles(cycles: pd.DataFrame, batch_id: str) -> None:
    """Record which forecast cycles were ingested, when they were published and whether they are complete."""
    if cycles.empty:
        return
    fs, root = filesystem()
    for source, part in cycles.groupby("source"):
        _write_parquet(fs, f"{root}/meta/cycles/{source}/{batch_id}.parquet", part[CYCLE_COLUMNS])


def write_batch(batch: Batch, batch_id: str) -> int:
    """Validate, then write hourly rows, sites and cycles. Raises on invalid data (nothing is written)."""
    hourly = normalise(batch.hourly)
    problems = validate(hourly)
    if problems:
        raise ValueError("batch failed validation: " + "; ".join(problems))
    added = write_hourly(hourly)
    write_sites(batch.sites)
    write_cycles(batch.cycles, batch_id)
    log.info("batch %s: %d rows, %d new", batch_id, len(hourly), added)
    return added
