"""Operations: schedule, heartbeats, data quality, scheduler steps, monitor, backup, metrics, test clock."""

import json
import logging
from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from gotime_api import app as app_module
from gotime_api.app import create_app
from gotime_api.auth import create_key
from gotime_api.metrics import Metrics
from gotime_api.settings import load_settings
from gotime_engine import SandboxEngine
from gotime_engine import snapshot as snapshot_files
from gotime_engine.precompute import run as precompute_run
from gotime_ops import backup, heartbeat, monitor, quality, schedule, scheduler
from tests.api_support import write_forecast


def utc(s: str) -> datetime:
    return pd.Timestamp(s, tz="UTC").to_pydatetime()


# ---------------------------------------------------------------- schedule


def test_ecmwf_jobs_follow_measured_publish_times():
    ecmwf = schedule.FORECASTS[0]
    job = schedule.newest_job(ecmwf, utc("2026-11-19 09:50"))
    assert job.run == utc("2026-11-19 00:00")  # published 09:34, start 09:44
    assert job.start == utc("2026-11-19 09:44") and job.deadline == utc("2026-11-19 12:44")
    assert schedule.newest_job(ecmwf, utc("2026-11-19 09:40")).run == utc("2026-11-18 18:00")
    assert schedule.newest_job(ecmwf, utc("2026-11-19 14:40")).start == utc("2026-11-19 14:37")  # 06z: +8:27
    assert schedule.newest_job(ecmwf, utc("2026-11-19 01:00")).run == utc("2026-11-18 12:00")


def test_due_jobs_retry_then_fail():
    now = utc("2026-11-19 10:00")
    jobs = dict((j.forecast.source, (a, j)) for a, j in schedule.due_cycle_jobs(now, {}))
    assert jobs["ecmwf_ifs"][0] == "try" and jobs["cams_eu"][0] == "try"
    key = jobs["ecmwf_ifs"][1].key
    state = {key: {"status": "trying", "last_try": now.isoformat()}}
    later = [j.key for _, j in schedule.due_cycle_jobs(now + timedelta(minutes=5), state)]
    assert key not in later  # retry only every 15 minutes
    assert key in [j.key for _, j in schedule.due_cycle_jobs(now + timedelta(minutes=15), state)]
    actions = dict((j.key, a) for a, j in schedule.due_cycle_jobs(utc("2026-11-19 12:50"), state))
    assert actions[key] == "fail"
    assert key not in [j.key for _, j in schedule.due_cycle_jobs(now, {key: {"status": "ok"}})]


def test_slots():
    assert schedule.observation_slot(utc("2026-11-19 10:20")) is None
    assert schedule.observation_slot(utc("2026-11-19 10:26")) == "2026-11-19T10"
    assert schedule.daily_slot("clean", utc("2026-11-19 03:00")) is None
    assert schedule.daily_slot("clean", utc("2026-11-19 03:20")) == "2026-11-19"


# ---------------------------------------------------------------- heartbeat


class FakeHttp:
    calls: list = []

    def __init__(self, **kw):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get(self, url):
        FakeHttp.calls.append(("GET", url, None))

    def post(self, url, content=b""):
        FakeHttp.calls.append(("POST", url, content))


@pytest.fixture
def pings(monkeypatch):
    monkeypatch.setattr(heartbeat, "load_keys", lambda: None)
    monkeypatch.setattr(heartbeat.httpx, "Client", FakeHttp)
    FakeHttp.calls = []
    return FakeHttp.calls


def test_heartbeat_skips_when_unset(pings, monkeypatch, caplog):
    monkeypatch.delenv(heartbeat.INGEST, raising=False)
    with caplog.at_level(logging.WARNING):
        assert heartbeat.ping(heartbeat.INGEST) is False
    assert pings == [] and "not set" in caplog.text


def test_heartbeat_success_and_fail(pings, monkeypatch, caplog):
    secret_url = "https://hc.example/ping/0000-secret-uuid"
    monkeypatch.setenv(heartbeat.INGEST, secret_url)
    with caplog.at_level(logging.DEBUG):
        assert heartbeat.ping(heartbeat.INGEST)
        assert heartbeat.ping(heartbeat.INGEST, ok=False, reason="ecmwf 06z late")
    assert pings[0] == ("GET", secret_url, None)
    assert pings[1] == ("POST", secret_url + "/fail", b"ecmwf 06z late")
    assert "secret-uuid" not in caplog.text


def test_heartbeat_never_raises(monkeypatch):
    monkeypatch.setattr(heartbeat, "load_keys", lambda: None)
    monkeypatch.setenv(heartbeat.MONITOR, "http://127.0.0.1:9/unreachable")
    assert heartbeat.ping(heartbeat.MONITOR) is False


# ---------------------------------------------------------------- data quality


def test_value_ranges():
    df = pd.DataFrame(
        {"variable": ["t2m_c"] * 3 + ["pm25_ugm3"] * 2, "value": [10.0, 75.0, 12.0, None, None]}
    )
    problems = quality.check_values(df)
    assert any("t2m_c: 1 values outside" in p for p in problems)
    assert any("pm25_ugm3: no values" in p for p in problems)
    assert quality.check_values(pd.DataFrame({"variable": ["t2m_c"], "value": [5.0]})) == []


def test_quality_checks_on_stored_cycle(storage):
    write_forecast("2026-11-19 00:00")
    now = utc("2026-11-19 10:00")
    result = quality.run_checks("ecmwf_ifs", utc("2026-11-19 00:00"), now)
    assert result["status"] == "failed"  # yesterday's cycles were never ingested
    assert all("missing" in p for p in result["problems"])
    assert quality.latest("ecmwf_ifs")["status"] == "failed"
    assert quality.check_cycle("ecmwf_ifs", utc("2026-11-19 00:00")) == []
    assert quality.check_cycle("ecmwf_ifs", utc("2026-11-19 06:00")) == [
        "cycle 2026-11-19 06 UTC not ingested"
    ]


def test_failed_quality_makes_source_degraded(storage, tmp_path, monkeypatch):
    write_forecast("2026-11-19 00:00", locations=("amsterdam",))
    (storage / "meta" / "quality").mkdir(parents=True)
    (storage / "meta" / "quality" / "ecmwf_ifs.json").write_text(json.dumps({"status": "failed"}))
    now = utc("2026-11-19 10:00")
    precompute_run(SandboxEngine(), now)
    monkeypatch.setenv("GOTIME_API_KEYS_FILE", str(tmp_path / "k.json"))
    settings = load_settings()
    _, key = create_key(settings.keys_file, "t", "sandbox")
    client = TestClient(create_app(settings, clock=lambda: now, reload_in_background=False))
    body = client.post(
        "/v1/guidance",
        headers={"Authorization": f"Bearer {key}"},
        json={
            "location": {"lat": 52.37, "lon": 4.9},
            "date": "2026-11-19",
            "earliest_start": "08:00",
            "latest_start": "12:00",
            "activity_type": "walk",
            "duration_min": 30,
        },
    ).json()
    assert body["data"]["status"] == "degraded"
    assert next(s for s in body["data"]["sources"] if s["source"] == "ecmwf_ifs")["status"] == "degraded"


# ---------------------------------------------------------------- scheduler


def test_scheduler_ingests_checks_and_precomputes(storage, monkeypatch, pings):
    monkeypatch.setenv(heartbeat.INGEST, "https://hc.example/ingest")
    monkeypatch.setenv(heartbeat.PRECOMPUTE, "https://hc.example/precompute")
    fetched = []

    class FakeSource:
        def __init__(self, name):
            self.name = name

        def fetch(self):
            fetched.append(self.name)
            if self.name == "ecmwf":
                write_forecast("2026-11-19 00:00", locations=("amsterdam",))

    monkeypatch.setattr(scheduler, "SOURCES", {n: FakeSource(n) for n in ("ecmwf", "cams_eu", "cams_global")})
    monkeypatch.setattr(scheduler, "ingest", lambda name: 0)
    monkeypatch.setattr(scheduler, "run_slot_jobs", lambda now, state: None)

    scheduler.tick(utc("2026-11-19 09:50"))
    state = scheduler.load_state()
    assert state["cycles"]["ecmwf_ifs|2026-11-19T00"]["status"] == "ok"
    assert snapshot_files.current_id("sandbox") is not None
    urls = [(m, u) for m, u, _ in pings]
    assert ("GET", "https://hc.example/precompute") in urls
    # Quality failed (yesterday's cycles are missing in this fresh store): ingest /fail with the reason.
    fail = [c for c in pings if c[1] == "https://hc.example/ingest/fail"]
    assert fail and b"missing" in fail[0][2]

    # CAMS global 00z is due from 12:10 and never arrives: after its 6 h window, one ingest /fail ping.
    pings.clear()
    for t in ("2026-11-19 12:10", "2026-11-19 18:15", "2026-11-19 18:16"):
        scheduler.tick(utc(t))
    fails = [c for c in pings if c[1].endswith("/fail") and b"cams_global|2026-11-19T00" in c[2]]
    assert len(fails) == 1


# ---------------------------------------------------------------- monitor


def test_monitor_pings_on_success_and_failure(tmp_path, monkeypatch, pings):
    from tests.e2e.make_snapshot import NOW, OUT

    monkeypatch.setenv("GOTIME_STORAGE_ROOT", str(OUT))
    monkeypatch.setenv("GOTIME_API_KEYS_FILE", str(tmp_path / "k.json"))
    monkeypatch.setenv(heartbeat.MONITOR, "https://hc.example/monitor")
    settings = load_settings()
    _, key = create_key(settings.keys_file, "monitor", "sandbox")
    api = create_app(settings, clock=lambda: NOW, reload_in_background=False)
    # monitor and heartbeat share httpx: API calls (with base_url) go to the app, pings to FakeHttp.
    monkeypatch.setattr(
        monitor.httpx, "Client", lambda **kw: TestClient(api) if "base_url" in kw else FakeHttp()
    )
    monkeypatch.setenv("GOTIME_MONITOR_KEY", key)
    assert monitor.run("http://api") == 0
    assert pings[-1][:2] == ("GET", "https://hc.example/monitor")
    monkeypatch.setenv("GOTIME_MONITOR_KEY", "gt_00000000_wrong")
    assert monitor.run("http://api") == 1
    assert pings[-1][1] == "https://hc.example/monitor/fail" and b"401" in pings[-1][2]


# ---------------------------------------------------------------- backup


def test_backup_and_restore(storage, tmp_path, monkeypatch):
    (storage / "archive" / "hourly").mkdir(parents=True)
    (storage / "archive" / "hourly" / "part.parquet").write_bytes(b"x" * 10)
    (storage / "meta").mkdir()
    (storage / "meta" / "s.parquet").write_bytes(b"y")
    target = tmp_path / "backup"
    monkeypatch.setenv("GOTIME_BACKUP_TARGET", str(target))
    first = backup.backup()
    assert first["archive"] == 1 and first["meta"] == 1 and first["climatology"] > 0
    assert backup.backup()["archive"] == 0  # incremental: nothing changed
    (storage / "archive" / "hourly" / "part.parquet").unlink()
    backup.restore()
    assert (storage / "archive" / "hourly" / "part.parquet").read_bytes() == b"x" * 10


# ---------------------------------------------------------------- API metrics and test clock


def test_metrics_flush():
    m = Metrics()
    for status, ms in ((200, 2.0), (200, 4.0), (422, 1.0), (503, 3.0)):
        m.record(status, ms)
    out = m.flush({}, datetime.now(UTC))
    assert (
        out["requests"] == 4 and out["by_status"] == {"2xx": 2, "4xx": 1, "5xx": 1} and out["errors_5xx"] == 1
    )
    assert m.flush({}, datetime.now(UTC))["requests"] == 0


def test_test_clock_only_in_test_env(monkeypatch, tmp_path):
    monkeypatch.setenv("GOTIME_STORAGE_ROOT", str(tmp_path))
    monkeypatch.setenv("GOTIME_API_KEYS_FILE", str(tmp_path / "k.json"))
    monkeypatch.setenv("GOTIME_TEST_NOW", "2026-11-19T10:00:00+00:00")
    monkeypatch.delenv("GOTIME_ENV", raising=False)
    with pytest.raises(RuntimeError):
        app_module.serve_app()
    monkeypatch.setenv("GOTIME_ENV", "test")
    assert app_module.serve_app() is not None
