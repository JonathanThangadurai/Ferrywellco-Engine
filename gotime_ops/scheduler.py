"""The scheduler process: fetch and ingest each forecast cycle when it is published, check data quality,
then precompute the serving snapshot. Also hourly observations, daily retention and backup.

One loop, one job at a time (sources one after another keeps peak memory near the ECMWF decode, ~570 MB).
State (which cycles are done) lives in <storage root>/ops/scheduler_state.json, so a restart neither
repeats nor skips work.

Heartbeats, only on success; on failure the /fail ping carries a short reason:
    HC_PING_INGEST      after a forecast cycle is ingested and passes the quality check
    HC_PING_PRECOMPUTE  after the snapshot is (re)built
"""

import json
import logging
import time
from datetime import UTC, datetime

import pandas as pd

from gotime_data.cli import ingest
from gotime_data.config import filesystem
from gotime_data.sources import SOURCES
from gotime_data.store import read_cycles

from . import heartbeat, quality, schedule

log = logging.getLogger(__name__)
TICK_S = 30


def _state_path() -> tuple:
    fs, root = filesystem()
    return fs, f"{root}/ops/scheduler_state.json"


def load_state() -> dict:
    fs, path = _state_path()
    return json.loads(fs.cat_file(path)) if fs.exists(path) else {"cycles": {}, "slots": {}}


def save_state(state: dict) -> None:
    fs, path = _state_path()
    fs.makedirs(path.rsplit("/", 1)[0], exist_ok=True)
    tmp = f"{path}.tmp"
    fs.pipe_file(tmp, json.dumps(state, indent=1).encode())
    fs.mv(tmp, path)


def cycle_complete(source: str, run: datetime) -> bool:
    """The run is in the cycles table, and complete for every location and variable recorded for it."""
    c = read_cycles()
    c = c[(c["source"] == source) & (pd.to_datetime(c["issue_time_utc"], utc=True) == pd.Timestamp(run))]
    return not c.empty and bool(c["complete"].astype(bool).all())


def precompute() -> None:
    from gotime_engine import SandboxEngine
    from gotime_engine.precompute import run

    try:
        sid = run(SandboxEngine())
        log.info("precompute done", extra={"fields": {"snapshot_id": sid or "unchanged"}})
        heartbeat.ping(heartbeat.PRECOMPUTE)
    except Exception as e:
        log.exception("precompute failed")
        heartbeat.ping(heartbeat.PRECOMPUTE, ok=False, reason=f"precompute failed: {type(e).__name__}")


def try_cycle(job: schedule.CycleJob, now: datetime, state: dict) -> None:
    """Fetch + ingest; when the expected cycle is complete: quality check, heartbeat, precompute."""
    entry = state["cycles"].setdefault(job.key, {"status": "trying"})
    entry["last_try"] = now.isoformat()
    try:
        SOURCES[job.forecast.name].fetch()
        ingest(job.forecast.name)
    except Exception as e:
        log.warning("fetch/ingest of %s failed, will retry: %s", job.key, type(e).__name__)
        entry["last_error"] = type(e).__name__
        return
    if not cycle_complete(job.forecast.source, job.run):
        log.info("cycle %s not complete yet; retry in %s", job.key, schedule.RETRY_EVERY)
        return
    entry["status"] = "ok"
    result = quality.run_checks(job.forecast.source, job.run, now)
    if result["status"] == "ok":
        heartbeat.ping(heartbeat.INGEST)
    else:
        reason = f"{job.key} quality: " + "; ".join(result["problems"])
        log.error(reason)
        heartbeat.ping(heartbeat.INGEST, ok=False, reason=reason)
    precompute()  # also when quality failed: the snapshot then marks the source degraded


def fail_cycle(job: schedule.CycleJob, state: dict) -> None:
    entry = state["cycles"].setdefault(job.key, {})
    entry["status"] = "failed"
    reason = (
        f"{job.key} not complete by {job.deadline:%H:%M} UTC ({entry.get('last_error', 'not published')})"
    )
    log.error(reason)
    heartbeat.ping(heartbeat.INGEST, ok=False, reason=reason)


def run_slot_jobs(now: datetime, state: dict) -> None:
    """Hourly observations and the daily clean and backup, once per slot."""
    slots = state["slots"]
    hour = schedule.observation_slot(now)
    if hour and slots.get("observations") != hour:
        slots["observations"] = hour
        for name in schedule.OBSERVATIONS:
            try:
                SOURCES[name].fetch()
                ingest(name)
            except Exception as e:  # observations are for verification; the nightly source check alerts
                log.warning("observations %s failed: %s", name, type(e).__name__)
    for job in schedule.DAILY:
        day = schedule.daily_slot(job, now)
        if day and slots.get(job) != day:
            slots[job] = day
            try:
                if job == "clean":
                    from gotime_data.store import clean

                    clean()
                else:
                    from .backup import backup

                    backup()
            except Exception as e:
                log.error("%s failed: %s", job, type(e).__name__)


def prune_state(state: dict, keep: int = 50) -> None:
    for key in sorted(state["cycles"])[:-keep]:
        del state["cycles"][key]


def tick(now: datetime | None = None) -> None:
    now = now or datetime.now(UTC)
    state = load_state()
    for action, job in schedule.due_cycle_jobs(now, state["cycles"]):
        if action == "try":
            try_cycle(job, now, state)
        else:
            fail_cycle(job, state)
        save_state(state)
    run_slot_jobs(now, state)
    prune_state(state)
    save_state(state)


def run_forever() -> None:
    log.info("scheduler started")
    while True:
        try:
            tick()
        except Exception:
            log.exception("scheduler tick failed")
        time.sleep(TICK_S)
