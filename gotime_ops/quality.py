"""Data quality checks after each forecast ingest: plausible ranges, completeness, missing cycles.

The result per source is written to meta/quality/<source>.json. The precompute job copies it into the
snapshot, and the API reports that source as at least `degraded` while its check fails. The scheduler
sends the ingest heartbeat's /fail ping with the reason.
"""

import json
from datetime import datetime, timedelta

import pandas as pd

from gotime_data.config import filesystem
from gotime_data.store import read_cycles, read_hourly

from .schedule import FORECASTS, MARGIN

# Plausible physical ranges (wide on purpose: they catch unit or decoding errors, not weather extremes).
RANGES = {
    "t2m_c": (-60, 60),
    "td2m_c": (-80, 40),
    "rh2m_pct": (0, 100.5),
    "wind10m_ms": (0, 80),
    "ssrd_wm2": (0, 1500),
    "precip_mm": (0, 300),
    "pm25_ugm3": (0, 2000),
    "pm10_ugm3": (0, 5000),
    "no2_ugm3": (0, 2000),
    "o3_ugm3": (0, 1000),
}
POLLEN_RANGE = (0, 100_000)
MAX_MISSING_SHARE = 0.10  # a cycle with more missing hours than this fails


def check_values(df: pd.DataFrame) -> list[str]:
    """Out-of-range values and variables that are empty, per variable."""
    problems = []
    for var, part in df.groupby("variable"):
        lo, hi = POLLEN_RANGE if var.startswith("pollen_") else RANGES.get(var, (-1e9, 1e9))
        values = part["value"].dropna()
        if values.empty:
            problems.append(f"{var}: no values")
            continue
        bad = int(((values < lo) | (values > hi)).sum())
        if bad:
            problems.append(f"{var}: {bad} values outside {lo}..{hi}")
        share = part["value"].isna().mean()
        if share > MAX_MISSING_SHARE:
            problems.append(f"{var}: {share:.0%} of hours missing")
    return problems


def check_cycle(source: str, run: datetime) -> list[str]:
    """Ranges and completeness of one ingested cycle, for every location it covers."""
    cycles = read_cycles()
    c = cycles[(cycles["source"] == source) & (pd.to_datetime(cycles["issue_time_utc"], utc=True) == run)]
    if c.empty:
        return [f"cycle {run:%Y-%m-%d %H} UTC not ingested"]
    problems = [
        f"{row.location_id} {row.variable}: cycle incomplete ({row.n_missing}/{row.n_expected} hours missing)"
        for row in c.itertuples()
        if not bool(row.complete)
    ]
    for loc in sorted(set(c["location_id"])):
        df = read_hourly(loc, run, run + timedelta(days=6), None, ["forecast"])
        df = df[(df["source"] == source) & (df["issue_time_utc"] == run)]
        problems += [f"{loc} {p}" for p in check_values(df)]
    return problems


def missing_cycles(source: str, now: datetime, days: int = 1) -> list[str]:
    """Runs from the last `days` whose retry deadline has passed but are not complete in the cycles table.

    Runs before the first complete cycle of the source are not counted: a fresh install starts with the
    newest cycle, so the ones before it were never expected (otherwise every first start would alarm).
    """
    fc = next(f for f in FORECASTS if f.source == source)
    cycles = read_cycles()
    complete = cycles[(cycles["source"] == source) & cycles["complete"].astype(bool)]
    done = set(pd.to_datetime(complete["issue_time_utc"], utc=True))
    if not done:
        return []  # nothing stored yet; check_cycle reports the cycle that is not ingested
    first = min(done)
    day0 = now.replace(hour=0, minute=0, second=0, microsecond=0)
    missing = []
    for d in range(days + 1):
        for hour, delay in fc.delay.items():
            run = day0 - timedelta(days=d) + timedelta(hours=hour)
            deadline = run + delay + MARGIN + fc.window
            expected = run >= now - timedelta(days=days) and run >= first and deadline <= now
            if expected and pd.Timestamp(run) not in done:
                missing.append(f"cycle {run:%Y-%m-%d %H} UTC missing")
    return sorted(missing)


def run_checks(source: str, run: datetime, now: datetime) -> dict:
    """All checks for a source after an ingest; writes and returns the result."""
    problems = check_cycle(source, run) + missing_cycles(source, now)
    result = {
        "source": source,
        "run_utc": run.isoformat(),
        "checked_utc": now.isoformat(),
        "status": "failed" if problems else "ok",
        "problems": problems,
    }
    fs, root = filesystem()
    fs.makedirs(f"{root}/meta/quality", exist_ok=True)
    fs.pipe_file(f"{root}/meta/quality/{source}.json", json.dumps(result, indent=1).encode())
    return result


def latest(source: str) -> dict | None:
    """The newest quality result for a source, or None."""
    fs, root = filesystem()
    path = f"{root}/meta/quality/{source}.json"
    return json.loads(fs.cat_file(path)) if fs.exists(path) else None
