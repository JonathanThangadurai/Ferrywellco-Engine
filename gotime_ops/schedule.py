"""When to fetch each forecast cycle, from the measured publish delays (docs/data-sources.md).

ECMWF 0.1 deg   00/12 UTC runs published at base + 9 h 34 min, 06/18 UTC at base + 8 h 27 min (measured)
CAMS Europe     00 UTC run, all steps 0-96 h at about 08:30 UTC (documented)
CAMS global     00/12 UTC runs; delay not measured yet: asked for 12 h after the run (as the fetcher
                does). The cycles table records the real publish time, so this can be tightened later.

A job starts MARGIN after the expected publish time and is retried every RETRY_EVERY until its deadline
(start + window). Only the newest run per source counts: once a newer run is due, an older one that never
arrived is reported by the data quality check (missing cycle), not retried.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

H, M = timedelta(hours=1), timedelta(minutes=1)
MARGIN = 10 * M
RETRY_EVERY = 15 * M


@dataclass(frozen=True)
class Forecast:
    name: str  # source module name for gotime-data fetch/ingest
    source: str  # source id in the table
    delay: dict[int, timedelta]  # run hour (UTC) -> publish delay
    window: timedelta  # how long to keep retrying after the start


FORECASTS = [
    Forecast(
        "ecmwf",
        "ecmwf_ifs",
        {0: 9 * H + 34 * M, 6: 8 * H + 27 * M, 12: 9 * H + 34 * M, 18: 8 * H + 27 * M},
        3 * H,
    ),
    Forecast("cams_eu", "cams_eu", {0: 8 * H + 30 * M}, 3 * H),
    Forecast("cams_global", "cams_global", {0: 12 * H, 12: 12 * H}, 6 * H),
]
OBSERVATIONS = ["eea", "knmi", "openaq"]  # hourly at OBSERVATION_MINUTE; for verification only
OBSERVATION_MINUTE = 25
DAILY = {"clean": (3, 15), "backup": (3, 45)}  # job -> (hour, minute) UTC


@dataclass(frozen=True)
class CycleJob:
    forecast: Forecast
    run: datetime
    start: datetime
    deadline: datetime

    @property
    def key(self) -> str:
        return f"{self.forecast.source}|{self.run:%Y-%m-%dT%H}"


def newest_job(fc: Forecast, now: datetime) -> CycleJob:
    """The newest run of this forecast whose start time has passed."""
    best = None
    day0 = now.replace(hour=0, minute=0, second=0, microsecond=0)
    for days in range(0, 3):
        for hour, delay in fc.delay.items():
            run = day0 - timedelta(days=days) + timedelta(hours=hour)
            start = run + delay + MARGIN
            if start <= now and (best is None or run > best.run):
                best = CycleJob(fc, run, start, start + fc.window)
    return best


def due_cycle_jobs(now: datetime, state: dict) -> list[tuple[str, CycleJob]]:
    """('try' | 'fail', job) for every forecast whose newest cycle needs work now.

    state[key] = {"status": "ok" | "failed" | "trying", "last_try": iso time}
    """
    out = []
    for fc in FORECASTS:
        job = newest_job(fc, now)
        entry = state.get(job.key, {})
        if entry.get("status") in ("ok", "failed"):
            continue
        if now > job.deadline:
            out.append(("fail", job))
            continue
        last = entry.get("last_try")
        if last is None or now - datetime.fromisoformat(last) >= RETRY_EVERY:
            out.append(("try", job))
    return out


def observation_slot(now: datetime) -> str | None:
    """The hour key when observations are due (from minute OBSERVATION_MINUTE of each hour)."""
    return f"{now:%Y-%m-%dT%H}" if now.minute >= OBSERVATION_MINUTE else None


def daily_slot(job: str, now: datetime) -> str | None:
    """The date key when a daily job is due (after its time of day)."""
    hour, minute = DAILY[job]
    return f"{now:%Y-%m-%d}" if (now.hour, now.minute) >= (hour, minute) else None
