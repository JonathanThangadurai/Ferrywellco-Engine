"""Retention: raw files 2 days, live table 10 days, then moved to the archive (kept)."""

import logging
import shutil
from datetime import UTC, datetime, timedelta

import pandas as pd

from ..config import LIVE_RETENTION_DAYS, RAW_RETENTION_DAYS, filesystem, raw_dir
from .write import write_hourly

log = logging.getLogger(__name__)


def clean_raw(now: datetime | None = None) -> int:
    """Delete raw batch folders older than RAW_RETENTION_DAYS (by modification time). Returns count."""
    now = now or datetime.now(UTC)
    cutoff = (now - timedelta(days=RAW_RETENTION_DAYS)).timestamp()
    removed = 0
    for batch in sorted(raw_dir().glob("*/*")):
        if batch.is_dir() and batch.stat().st_mtime < cutoff:
            if not (batch / ".ingested").exists():
                log.warning("deleting raw batch %s that was never ingested", batch)
            shutil.rmtree(batch)
            removed += 1
    return removed


def archive_live(now: datetime | None = None) -> int:
    """Move live date partitions older than LIVE_RETENTION_DAYS to archive/. Returns partitions moved."""
    now = now or datetime.now(UTC)
    cutoff = (now - timedelta(days=LIVE_RETENTION_DAYS)).strftime("%Y-%m-%d")
    fs, root = filesystem()
    moved = 0
    for part_dir in sorted(fs.glob(f"{root}/live/hourly/source=*/date=*")):
        day = part_dir.rsplit("date=", 1)[-1]
        if day >= cutoff:
            continue
        for path in fs.glob(f"{part_dir}/*.parquet"):
            write_hourly(pd.read_parquet(fs.open(path, "rb")), layer="archive")  # merges if late rows exist
        fs.rm(part_dir, recursive=True)
        moved += 1
    return moved


def clean(now: datetime | None = None) -> dict[str, int]:
    """Run both retention steps."""
    return {"raw_batches_deleted": clean_raw(now), "live_partitions_archived": archive_live(now)}
