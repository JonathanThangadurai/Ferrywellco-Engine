"""Golden responses: the fixed e2e snapshot + a fixed clock must give exactly the stored JSON.

Any change to a response must be intentional: update the golden files in the same commit with
    UPDATE_GOLDEN=1 uv run pytest tests/test_golden.py
and review the diff. request_id is left out (it is random); everything else is compared.
"""

import json
import os
from datetime import datetime
from pathlib import Path

import pytest

from gotime_api.app import create_app
from gotime_api.auth import create_key
from gotime_api.settings import load_settings
from gotime_engine import SandboxEngine
from gotime_engine import snapshot as snapshot_files
from tests.api_support import Contract, ContractClient
from tests.e2e.make_snapshot import DEGRADED_NOW, NOW, OUT, UNAVAILABLE_NOW

GOLDEN = Path(__file__).parent / "golden"
UPDATE = os.environ.get("UPDATE_GOLDEN") == "1"
BASE = {
    "location": {"lat": 52.37, "lon": 4.90},
    "date": "2026-11-19",
    "earliest_start": "07:00",
    "latest_start": "19:00",
    "activity_type": "run",
    "duration_min": 45,
}
CASES = {
    "normal_amsterdam": (NOW, BASE | {"hay_fever": "yes"}),
    "chennai": (NOW, BASE | {"location": {"lat": 13.08, "lon": 80.27}, "activity_type": "walk"}),
    "not_covered": (NOW, BASE | {"location": {"lat": 48.86, "lon": 2.35}}),
    "degraded": (DEGRADED_NOW, BASE | {"date": "2026-11-20"}),
    "unavailable": (UNAVAILABLE_NOW, BASE | {"date": "2026-11-21"}),
    "bad_input": (NOW, BASE | {"duration_min": 5}),
    "no_window": (NOW, BASE | {"earliest_start": "23:30", "latest_start": "23:30", "duration_min": 60}),
}


@pytest.fixture
def client_at(tmp_path, monkeypatch):
    monkeypatch.setenv("GOTIME_STORAGE_ROOT", str(OUT))
    monkeypatch.setenv("GOTIME_API_KEYS_FILE", str(tmp_path / "keys.json"))
    settings = load_settings()
    _, key = create_key(settings.keys_file, "golden", "sandbox")

    def make(now: datetime) -> tuple[ContractClient, dict]:
        app = create_app(settings, clock=lambda: now, reload_in_background=False)
        return ContractClient(app, Contract()), {"Authorization": f"Bearer {key}"}

    return make


def _compare(name: str, status: int, body: dict) -> None:
    body = {k: v for k, v in body.items() if k != "request_id"}
    got = {"status": status, "body": body}
    path = GOLDEN / f"{name}.json"
    if UPDATE:
        GOLDEN.mkdir(exist_ok=True)
        path.write_text(json.dumps(got, indent=2, sort_keys=True) + "\n")
    assert path.exists(), f"no golden file for {name}; run with UPDATE_GOLDEN=1"
    assert got == json.loads(path.read_text()), f"{name} differs from {path.name}"


def test_e2e_snapshot_matches_engine_version(monkeypatch):
    monkeypatch.setenv("GOTIME_STORAGE_ROOT", str(OUT))
    snap = snapshot_files.load("sandbox", snapshot_files.current_id("sandbox"))
    assert snap["engine"]["version"] == SandboxEngine.version, (
        "rebuild: uv run python -m tests.e2e.make_snapshot"
    )


@pytest.mark.parametrize("name", sorted(CASES))
def test_golden_guidance(client_at, name):
    now, body = CASES[name]
    client, headers = client_at(now)
    r = client.post("/v1/guidance", json=body, headers=headers)
    _compare(name, r.status_code, r.json())


def test_golden_coverage(client_at):
    client, headers = client_at(NOW)
    r = client.get("/v1/coverage", headers=headers)
    _compare("coverage", r.status_code, r.json())


def test_golden_missing_key(client_at):
    client, _ = client_at(NOW)
    r = client.post("/v1/guidance", json=BASE)
    _compare("missing_key", r.status_code, r.json())
