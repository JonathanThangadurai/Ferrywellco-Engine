"""Small helpers shared by the source modules: raw batch folders, manifests, building rows, ADS/CDS."""

import json
import logging
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from .. import keys
from ..config import raw_dir
from ..schema import CYCLE_COLUMNS

log = logging.getLogger(__name__)


def batch_dir(source: str, batch_id: str) -> Path:
    """raw/<source>/<batch_id>/, created if needed. batch_id is the issue time (forecasts) or fetch time."""
    path = raw_dir() / source / batch_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_manifest(path: Path, manifest: dict) -> None:
    """manifest.json says what a raw batch holds (issue/publish/fetch times, missing steps, fallbacks)."""
    (path / "manifest.json").write_text(json.dumps(manifest, indent=1, default=str))


def read_manifest(path: Path) -> dict:
    return json.loads((path / "manifest.json").read_text())


def utc(text) -> pd.Timestamp:
    """Parse a time to a UTC timestamp (naive input is taken as UTC)."""
    t = pd.Timestamp(text)
    return t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")


def now_utc() -> datetime:
    return datetime.now(UTC)


def rows(
    location_id: str,
    variable: str,
    values: pd.Series,
    status: pd.Series | str,
    source: str,
    kind: str,
    issue_time,
    site_id: str | None = None,
    method: str | None = None,
) -> pd.DataFrame:
    """Canonical rows for one series (index = valid time UTC). NaN values get status 'missing'."""
    valid = pd.DatetimeIndex(values.index)
    valid = valid.tz_localize("UTC") if valid.tz is None else valid.tz_convert("UTC")
    status = pd.Series(status, index=values.index) if isinstance(status, str) else status
    status = status.where(values.notna(), "missing")
    issue = pd.Series(issue_time, index=values.index) if not isinstance(issue_time, pd.Series) else issue_time
    issue = pd.to_datetime(issue, utc=True)
    lead = ((valid - pd.DatetimeIndex(issue)).total_seconds() / 3600).round() if kind == "forecast" else None
    return pd.DataFrame(
        {
            "location_id": location_id,
            "valid_time_utc": valid,
            "variable": variable,
            "value": values.to_numpy(dtype="float64"),
            "source": source,
            "kind": kind,
            "issue_time_utc": issue.to_numpy(),
            "lead_h": np.asarray(lead) if lead is not None else None,
            "status": status.to_numpy(),
            "site_id": site_id,
            "method": method,
        }
    )


def cycle_rows(hourly: pd.DataFrame, manifest: dict) -> pd.DataFrame:
    """One cycle row per location and variable: how many hours were expected and how many are missing."""
    if hourly.empty:
        return pd.DataFrame(columns=CYCLE_COLUMNS)
    g = hourly.groupby(["source", "location_id", "variable", "issue_time_utc"])
    out = g["status"].agg(n_expected="size", n_missing=lambda s: int((s == "missing").sum())).reset_index()
    out["publish_time_utc"] = pd.to_datetime(manifest.get("publish_time_utc"), utc=True)
    out["fetched_time_utc"] = pd.to_datetime(manifest.get("fetched_time_utc"), utc=True)
    missing_steps = manifest.get("missing_steps") or []
    out["complete"] = (out["n_missing"] == 0) & (len(missing_steps) == 0)
    return out[CYCLE_COLUMNS]


def ads_area(bbox: list[float]) -> list[float]:
    """config bbox [lat_min, lat_max, lon_min, lon_max] -> ADS/CDS area [N, W, S, E]."""
    lat_min, lat_max, lon_min, lon_max = bbox
    return [lat_max, lon_min, lat_min, lon_max]


def ads_retrieve(url_name: str, dataset: str, request: dict, target: Path) -> None:
    """One ADS/CDS request (ADS_URL for CAMS, CDS_URL for ERA5), waiting until the file is there.

    The ADS/CDS allow only a few queued requests per dataset; callers send one request at a time.
    """
    import cdsapi

    client = cdsapi.Client(url=keys.get(url_name), key=keys.get("COPERNICUS_KEY"), quiet=True, progress=False)
    log.info("ADS/CDS request %s -> %s", dataset, target.name)
    client.retrieve(dataset, request, str(target))
