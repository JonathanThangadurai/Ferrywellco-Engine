"""Precompute job: run the engine once per forecast cycle and write one serving snapshot.

For each location and each of the next 4 local dates (today to today + 3), it reads the hourly table
(newest complete cycle per source and variable) and calls engine.assess() for every profile. If the
cycles, dates and engine version are the same as in the CURRENT snapshot, it does nothing.
"""

import hashlib
import json
import logging
from datetime import UTC, date, datetime, timedelta

import pandas as pd

from gotime_data.config import filesystem, locations
from gotime_data.read import get_hourly
from gotime_data.store import read_sites

from . import snapshot
from .protocol import GuidanceEngine, LocationInfo, local_day_hours

DAYS = 4
DEFAULT_RADIUS_KM = 15.0
WEATHER_SOURCE = "ecmwf_ifs"  # locations are keyed by this source's chosen grid cell
NEXT_CYCLE_AFTER = timedelta(hours=6 + 10)  # next ECMWF run (6 h) + publication and ingest (~10 h)
log = logging.getLogger(__name__)


def location_id(lat: float, lon: float) -> str:
    """Id of a location = its ECMWF 0.1 deg cell, e.g. ecmwf0p1_n52.40_e4.90."""
    ns, ew = ("n" if lat >= 0 else "s"), ("e" if lon >= 0 else "w")
    return f"ecmwf0p1_{ns}{abs(lat):.2f}_{ew}{abs(lon):.2f}"


def resolve_locations() -> list[tuple[LocationInfo, dict]]:
    """Each configured city with the weather cell chosen at ingest (sites table). Skips cities without one."""
    sites = read_sites()
    out = []
    for alias, cfg in locations().items():
        row = sites[sites["site_id"] == f"grid:{WEATHER_SOURCE}:{alias}"]
        if row.empty:
            log.warning("no %s grid cell for %s yet; skipped", WEATHER_SOURCE, alias)
            continue
        lat, lon = round(float(row["lat"].iloc[0]), 2), round(float(row["lon"].iloc[0]), 2)
        out.append((LocationInfo(location_id(lat, lon), alias, cfg["tz"], lat, lon), cfg))
    return out


def local_dates(tz: str, now_utc: datetime, days: int = DAYS) -> list[date]:
    today = pd.Timestamp(now_utc).tz_convert(tz).date()
    return [today + timedelta(days=i) for i in range(days)]


def quality_status(source: str) -> str:
    """'failed' when the newest data quality check of this source failed (meta/quality/), else 'ok'."""
    fs, root = filesystem()
    path = f"{root}/meta/quality/{source}.json"
    return json.loads(fs.cat_file(path)).get("status", "ok") if fs.exists(path) else "ok"


def source_summary(df: pd.DataFrame, required: list[str]) -> list[dict]:
    """Per source: oldest issue time over its variables (for data age), forecast end and quality status."""
    out = []
    for source, part in df[df["value"].notna()].groupby("source"):
        per_var = part.groupby("variable").agg(
            issue=("issue_time_utc", "min"), last=("valid_time_utc", "max")
        )
        variables = sorted(per_var.index)
        out.append(
            {
                "source": source,
                "issue_time_utc": per_var["issue"].min().isoformat(),
                "forecast_until_utc": (per_var["last"].min() + pd.Timedelta(hours=1)).isoformat(),
                "variables": variables,
                "required": any(v in required for v in variables),
                "quality": quality_status(source),
            }
        )
    return out


def plan(engine: GuidanceEngine, now_utc: datetime) -> tuple[str, list[dict]]:
    """Read the inputs and work out the snapshot id, without running the engine."""
    variables = engine.required_variables() + engine.optional_variables()
    plans = []
    for info, cfg in resolve_locations():
        dates = local_dates(info.timezone, now_utc)
        start = local_day_hours(dates[0], info.timezone)[0] - pd.Timedelta(hours=24)
        end = local_day_hours(dates[-1], info.timezone)[-1] + pd.Timedelta(hours=1)
        hourly = get_hourly(info.alias, start, end, variables, kind="forecast", latest_only=True)
        plans.append(
            {
                "info": info,
                "name": cfg.get("name", info.alias),
                "radius_km": float(cfg.get("radius_km", DEFAULT_RADIUS_KM)),
                "dates": dates,
                "hourly": hourly,
                "sources": source_summary(hourly, engine.required_variables()),
            }
        )
    fingerprint = json.dumps(
        [engine.name, engine.version]
        + [[p["info"].id, p["radius_km"], [str(d) for d in p["dates"]], p["sources"]] for p in plans],
        sort_keys=True,
    )
    issues = [s["issue_time_utc"] for p in plans for s in p["sources"]]
    newest = pd.Timestamp(max(issues)) if issues else pd.Timestamp(now_utc)
    sid = f"{newest:%Y%m%dT%H}Z-{hashlib.sha256(fingerprint.encode()).hexdigest()[:8]}"
    return sid, plans


def build(engine: GuidanceEngine, now_utc: datetime, sid: str, plans: list[dict]) -> dict:
    """Run engine.assess for every location, date and profile and assemble the snapshot."""
    assessments, locs = {}, []
    for p in plans:
        info = p["info"]
        for d in p["dates"]:
            hours = local_day_hours(d, info.timezone)
            window = p["hourly"][
                (p["hourly"]["valid_time_utc"] >= hours[0] - pd.Timedelta(hours=24))
                & (p["hourly"]["valid_time_utc"] <= hours[-1])
            ]
            for profile in engine.profiles():
                result = engine.assess(window, info, d, profile, now_utc)
                assessments[snapshot.assessment_key(info.id, d.isoformat(), profile.id)] = result
        locs.append(
            {
                "id": info.id,
                "alias": info.alias,
                "name": p["name"],
                "lat": info.lat,
                "lon": info.lon,
                "radius_km": p["radius_km"],
                "timezone": info.timezone,
                "dates": [d.isoformat() for d in p["dates"]],
                "sources": p["sources"],
            }
        )
    weather = [s["issue_time_utc"] for p in plans for s in p["sources"] if s["source"] == WEATHER_SOURCE]
    base = pd.Timestamp(max(weather)) if weather else pd.Timestamp(now_utc)
    return {
        "format": snapshot.FORMAT,
        "snapshot_id": sid,
        "mode": engine.mode,
        "engine": {"name": engine.name, "version": engine.version},
        "created_utc": pd.Timestamp(now_utc).isoformat(),
        "expected_next_utc": (base + NEXT_CYCLE_AFTER).isoformat(),
        "profiles": [pr.id for pr in engine.profiles()],
        "locations": locs,
        "assessments": assessments,
    }


def run(engine: GuidanceEngine, now_utc: datetime | None = None, force: bool = False) -> str | None:
    """Write a new snapshot when the inputs changed. Returns its id, or None when nothing changed."""
    now_utc = now_utc or datetime.now(UTC)
    sid, plans = plan(engine, now_utc)
    if not plans:
        raise RuntimeError("no location has data yet; run ingest first")
    if not force and snapshot.current_id(engine.mode) == sid:
        log.info("snapshot %s is current; nothing to do", sid)
        return None
    snap = build(engine, now_utc, sid, plans)
    snapshot.write(snap)
    log.info("snapshot %s written: %d assessments", sid, len(snap["assessments"]))
    return sid
