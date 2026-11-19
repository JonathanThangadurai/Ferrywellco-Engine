"""API tests. Every response goes through the OpenAPI contract check (tests/api_support.py)."""

import io
import json
import logging
import os
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from gotime_api.app import create_app
from gotime_api.auth import KeyStore, create_key, revoke_key
from gotime_api.logs import JsonFormatter
from gotime_api.models import GuidanceRequest, parse_request
from gotime_api.problems import Problem
from gotime_api.settings import load_settings
from gotime_engine import SandboxEngine
from gotime_engine.precompute import run
from gotime_engine.protocol import ACTIVITY_TYPES
from tests.api_support import Contract, ContractClient, write_forecast

CONTRACT = Contract()


def _snapshot_dir(tmp_path_factory, issue: str, now: datetime) -> str:
    """A storage root with one synthetic cycle and its snapshot (built once per test session)."""
    root = tmp_path_factory.mktemp("serving")
    old = os.environ.get("GOTIME_STORAGE_ROOT")
    os.environ["GOTIME_STORAGE_ROOT"] = str(root)
    try:
        write_forecast(issue)
        run(SandboxEngine(), now)
    finally:
        if old is None:
            del os.environ["GOTIME_STORAGE_ROOT"]
        else:
            os.environ["GOTIME_STORAGE_ROOT"] = old
    return str(root)


ISSUE, NOW = "2026-11-19 00:00", datetime(2026, 11, 19, 10, tzinfo=UTC)


@pytest.fixture(scope="session")
def november(tmp_path_factory):
    return _snapshot_dir(tmp_path_factory, ISSUE, NOW)


class Api:
    """Test client, a valid key and a clock the test can move."""

    def __init__(self, root, tmp_path, monkeypatch, now, **settings_changes):
        monkeypatch.setenv("GOTIME_STORAGE_ROOT", root)
        monkeypatch.setenv("GOTIME_API_KEYS_FILE", str(tmp_path / "keys" / "api_keys.json"))
        self.settings = replace(load_settings(), keys_reload_s=0, **settings_changes)
        self.key_id, self.key = create_key(self.settings.keys_file, "test-partner", "sandbox")
        self.now = now
        self.app = create_app(self.settings, clock=lambda: self.now, reload_in_background=False)
        self.client = ContractClient(self.app, CONTRACT)
        self.headers = {"Authorization": f"Bearer {self.key}"}

    def guidance(self, headers=None, **changes):
        body = {
            "location": {"lat": 52.37, "lon": 4.90},
            "date": "2026-11-19",
            "earliest_start": "07:00",
            "latest_start": "19:00",
            "activity_type": "run",
            "duration_min": 45,
        } | changes
        return self.client.post(
            "/v1/guidance", json=body, headers=self.headers if headers is None else headers
        )


@pytest.fixture
def api(november, tmp_path, monkeypatch):
    return Api(november, tmp_path, monkeypatch, NOW)


def test_guidance_ok_and_sandbox_flagged(api):
    r = api.guidance(hay_fever="yes")
    assert r.status_code == 200
    body = r.json()
    assert body["mode"] == "sandbox"
    assert body["location"] == {
        "id": "ecmwf0p1_n52.40_e4.90",
        "alias": "amsterdam",
        "timezone": "Europe/Amsterdam",
        "distance_km": 3.3,
    }
    assert body["reasons"] and all(x["code"].startswith("sandbox_") for x in body["reasons"])
    assert body["data"]["status"] == "ok"
    assert {s["source"] for s in body["data"]["sources"]} == {"ecmwf_ifs", "cams_eu"}
    assert body["data"]["attribution"]
    assert r.headers["etag"].startswith('W/"20261119T00Z-')
    assert r.headers["cache-control"].startswith("private, max-age=")


@pytest.mark.parametrize("activity", ACTIVITY_TYPES)
@pytest.mark.parametrize("day", ["2026-11-19", "2026-11-20", "2026-11-21"])
def test_sandbox_mode_always_flagged(api, activity, day):
    for loc in ({"lat": 52.37, "lon": 4.9}, {"lat": 13.08, "lon": 80.27}):
        body = api.guidance(location=loc, date=day, activity_type=activity, heat_adapted="no").json()
        assert body["mode"] == "sandbox"
        engine_reasons = [x for x in body["reasons"] if x["source"] != "data_layer"]
        assert engine_reasons and all(x["code"].startswith("sandbox_") for x in engine_reasons)


def test_window_fits_duration_and_options(api):
    for minutes in (10, 45, 120, 240):
        w = api.guidance(duration_min=minutes, earliest_start="06:00", latest_start="18:00").json()["window"]
        assert w["status"] == "found"
        start, end = datetime.fromisoformat(w["start"]), datetime.fromisoformat(w["end"])
        last = datetime.fromisoformat(w["latest_start"])
        # start..end is the suitable period; latest_start = end - duration.
        assert start <= last and last + timedelta(minutes=minutes) <= end
        assert last + timedelta(minutes=minutes) == end
        assert "06:00" <= w["start"][11:16] <= w["latest_start"][11:16] <= "18:00"
        assert w["start"].endswith("+01:00")


def test_chennai_local_times(api):
    body = api.guidance(
        location={"lat": 13.08, "lon": 80.27}, earliest_start="06:00", latest_start="06:00"
    ).json()
    assert body["location"]["alias"] == "chennai" and body["location"]["distance_km"] == 7.9
    assert body["window"]["start"] == "2026-11-19T06:00:00+05:30"


def test_snapping_radius_and_rounding(api):
    assert (
        api.guidance(location={"lat": 52.30, "lon": 4.80}).json()["location"]["alias"] == "amsterdam"
    )  # ~13 km
    south = api.guidance(location={"lat": 12.95, "lon": 80.20})  # 16.7 km: inside Chennai's 20 km
    assert south.json()["location"]["alias"] == "chennai"
    tambaram = api.guidance(location={"lat": 12.92, "lon": 80.12})  # 22 km
    assert tambaram.json()["code"] == "not-covered"
    r = api.guidance(location={"lat": 52.09, "lon": 5.12})  # Utrecht, ~35 km
    assert r.status_code == 422 and r.json()["code"] == "not-covered"
    r = api.guidance(location={"lat": 48.86, "lon": 2.35})  # Paris
    assert r.json()["code"] == "not-covered"
    # 52.3712 and 52.3698 both round to 52.37: the same request, the same ETag.
    a = api.guidance(location={"lat": 52.3712, "lon": 4.9001}).headers["etag"]
    b = api.guidance(location={"lat": 52.3698, "lon": 4.8998}).headers["etag"]
    assert a == b


def test_dates_and_horizon(api):
    assert api.guidance(date="2026-11-18").json()["code"] == "date-out-of-range"  # yesterday
    assert api.guidance(date="2026-11-23").json()["code"] == "date-out-of-range"  # today + 4
    # The synthetic forecast (97 h from 2026-11-19 00 UTC) covers all of 2026-11-22 local (today + 3).
    assert api.guidance(date="2026-11-22").status_code == 200
    api.now = NOW + timedelta(hours=14)  # local 2026-11-20 01:00: today + 3 = 2026-11-23, not in the snapshot
    r = api.guidance(date="2026-11-23")
    assert r.status_code == 422 and r.json()["code"] == "beyond-forecast-horizon"


def test_daylight_saving_days(tmp_path_factory, tmp_path, monkeypatch):
    autumn = _snapshot_dir(tmp_path_factory, "2026-10-24 00:00", datetime(2026, 10, 24, 6, tzinfo=UTC))
    api = Api(autumn, tmp_path, monkeypatch, datetime(2026, 10, 24, 6, tzinfo=UTC))
    w = api.guidance(date="2026-10-25", earliest_start="02:30", latest_start="02:30", duration_min=60).json()
    assert w["window"]["start"] == "2026-10-25T02:30:00+02:00"  # first 02:30 (summer time)
    assert w["window"]["end"] == "2026-10-25T02:30:00+01:00"  # 60 min later the clock shows 02:30 again

    spring = _snapshot_dir(tmp_path_factory, "2027-03-27 00:00", datetime(2027, 3, 27, 6, tzinfo=UTC))
    api = Api(spring, tmp_path / "b", monkeypatch, datetime(2027, 3, 27, 6, tzinfo=UTC))
    w = api.guidance(date="2027-03-28", earliest_start="02:30", latest_start="02:30", duration_min=30).json()
    assert w["window"]["start"] == "2027-03-28T03:00:00+02:00"  # 02:30 does not exist that day
    assert w["window"]["end"] == "2027-03-28T03:30:00+02:00"


def test_stale_data_is_degraded_then_unavailable(api):
    api.now = datetime(2026, 11, 19, 20, tzinfo=UTC)  # ECMWF 20 h old (ok up to 18)
    body = api.guidance(date="2026-11-20").json()
    assert body["data"]["status"] == "degraded"
    ecmwf = next(s for s in body["data"]["sources"] if s["source"] == "ecmwf_ifs")
    assert ecmwf["status"] == "degraded" and ecmwf["forecast_age_h"] == 20.0
    api.now = datetime(2026, 11, 20, 13, tzinfo=UTC)  # 37 h old (degraded up to 36)
    r = api.guidance(date="2026-11-21")
    assert r.status_code == 503 and r.json()["code"] == "data-unavailable"
    assert (
        api.client.get("/v1/coverage", headers=api.headers).json()["locations"][0]["data_status"]
        == "unavailable"
    )


def test_ready_fails_when_snapshot_is_too_old(api):
    r = api.client.get("/v1/ready")
    assert r.status_code == 200 and r.json()["snapshot_id"].startswith("20261119T00Z-")
    api.now = NOW + timedelta(hours=19)
    r = api.client.get("/v1/ready")
    assert r.status_code == 503 and r.json()["code"] == "snapshot-unavailable"
    assert api.client.get("/v1/health").status_code == 200


def test_no_snapshot(storage, tmp_path, monkeypatch):
    api = Api(str(storage), tmp_path, monkeypatch, NOW)
    assert api.client.get("/v1/ready").status_code == 503
    r = api.guidance()
    assert r.status_code == 503 and r.json()["code"] == "snapshot-unavailable"


def test_auth(api):
    assert api.guidance(headers={}).status_code == 401
    r = api.guidance(headers={"Authorization": "Bearer nonsense"})
    assert r.status_code == 401 and r.headers["www-authenticate"] == "Bearer"
    wrong = api.key[:-4] + ("aaaa" if not api.key.endswith("aaaa") else "bbbb")
    assert api.guidance(headers={"Authorization": f"Bearer {wrong}"}).status_code == 401
    assert api.guidance(headers={"Authorization": api.key}).status_code == 401  # no "Bearer "
    assert api.guidance().status_code == 200
    assert revoke_key(api.settings.keys_file, api.key_id)
    r = api.guidance()
    assert r.status_code == 401 and r.json()["code"] == "unauthorized"
    assert api.client.get("/v1/coverage", headers=api.headers).status_code == 401


def test_one_mode_per_key(api):
    with pytest.raises(ValueError):
        create_key(api.settings.keys_file, "x", "admin")
    _, live = create_key(api.settings.keys_file, "x", "live")
    r = api.guidance(headers={"Authorization": f"Bearer {live}"})  # no live snapshot is served yet
    assert r.status_code == 503 and r.json()["code"] == "snapshot-unavailable"
    data = json.loads(api.settings.keys_file.read_text())
    for k in data["keys"]:
        k["mode"] = "admin"  # a hand-edited file must not open anything
    api.settings.keys_file.write_text(json.dumps(data))
    os.utime(api.settings.keys_file, (1, 1))
    assert api.guidance().status_code == 401


def test_pollen_not_assessed(api):
    chennai = {"lat": 13.08, "lon": 80.27}  # no pollen forecast for Chennai
    body = api.guidance(location=chennai, hay_fever="no").json()
    assert body["data"]["status"] == "ok"
    assert body["reasons"][0]["code"] == "pollen_not_assessed"
    body = api.guidance(location=chennai, hay_fever="yes").json()
    assert body["data"]["status"] == "degraded"
    assert body["reasons"][0]["code"] == "pollen_not_assessed"
    body = api.guidance(hay_fever="yes").json()  # Amsterdam has pollen
    assert body["data"]["status"] == "ok"
    assert all(x["code"] != "pollen_not_assessed" for x in body["reasons"])


def test_keys_file_holds_hashes_only(api):
    text = api.settings.keys_file.read_text()
    assert api.key.split("_", 2)[2] not in text
    assert oct(api.settings.keys_file.stat().st_mode)[-3:] == "600"
    assert KeyStore(api.settings.keys_file).verify(f"Bearer {api.key}").partner == "test-partner"


def test_rate_limit(november, tmp_path, monkeypatch):
    api = Api(november, tmp_path, monkeypatch, NOW, rate_burst=3, rate_per_minute=60)
    codes = [api.guidance().status_code for _ in range(4)]
    assert codes == [200, 200, 200, 429]
    r = api.guidance()
    assert r.json()["code"] == "rate-limited" and int(r.headers["retry-after"]) >= 1
    other_id, other = create_key(api.settings.keys_file, "other", "sandbox")
    assert api.guidance(headers={"Authorization": f"Bearer {other}"}).status_code == 200  # per key


def test_invalid_requests(api):
    r = api.guidance(duration_min=5)
    assert r.status_code == 422 and r.json()["errors"][0]["field"] == "duration_min"
    assert api.guidance(earliest_start="7:00").status_code == 422
    assert api.guidance(earliest_start="10:00", latest_start="09:00").status_code == 422
    assert api.guidance(activity_type="swim").status_code == 422
    assert api.guidance(location={"lat": 95.0, "lon": 4.9}).status_code == 422
    assert api.guidance(location={"lat": "52.37", "lon": 4.9}).status_code == 422  # strict: no strings
    r = api.guidance(name="Jane Example", email="jane@example.com")  # personal fields are refused
    assert r.status_code == 422 and "Jane" not in r.text and "example.com" not in r.text
    r = api.client.post("/v1/guidance", content=b"{not json", headers=api.headers)
    assert r.status_code == 400 and r.json()["code"] == "invalid-request"
    big = json.dumps({"x": "a" * 5000}).encode()
    r = api.client.post("/v1/guidance", content=big, headers=api.headers)
    assert r.status_code == 413 and r.json()["code"] == "payload-too-large"
    assert api.client.get("/v1/nothing").json()["code"] == "not-found"
    assert api.client.get("/v1/guidance").json()["code"] == "method-not-allowed"


def test_etag_gives_304(api):
    r = api.guidance()
    again = api.guidance(headers={**api.headers, "If-None-Match": r.headers["etag"]})
    assert again.status_code == 304 and again.headers["etag"] == r.headers["etag"]
    cov = api.client.get("/v1/coverage", headers=api.headers)
    assert (
        api.client.get(
            "/v1/coverage", headers={**api.headers, "If-None-Match": cov.headers["etag"]}
        ).status_code
        == 304
    )


def test_coverage(api):
    body = api.client.get("/v1/coverage", headers=api.headers).json()
    assert {loc["aliases"][0] for loc in body["locations"]} == {"amsterdam", "chennai"}
    ams = next(loc for loc in body["locations"] if loc["aliases"] == ["amsterdam"])
    assert ams["dates_available"] == ["2026-11-19", "2026-11-20", "2026-11-21", "2026-11-22"]
    assert ams["radius_km"] == 15.0 and ams["data_status"] == "ok"


def test_request_id(api):
    assert (
        api.guidance(headers={**api.headers, "X-Request-ID": "abc-123"}).headers["x-request-id"] == "abc-123"
    )
    assert (
        api.guidance(headers={**api.headers, "X-Request-ID": "bad id!"}).headers["x-request-id"] != "bad id!"
    )


def test_engine_error_is_500(api, monkeypatch):
    engine = api.app.state.stores["sandbox"].current.engine

    def broken(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(engine, "choose", broken)
    r = api.guidance()
    assert r.status_code == 500 and r.json()["code"] == "engine-error"


def test_new_snapshot_is_picked_up(storage, tmp_path, monkeypatch):
    write_forecast("2026-11-19 00:00", locations=("amsterdam",))
    first = run(SandboxEngine(), NOW)
    api = Api(str(storage), tmp_path, monkeypatch, NOW)
    write_forecast("2026-11-19 06:00", locations=("amsterdam",))
    second = run(SandboxEngine(), NOW + timedelta(hours=9))
    assert api.guidance().json()["snapshot_id"] == first  # until reload
    assert api.app.state.stores["sandbox"].reload()
    assert api.guidance().json()["snapshot_id"] == second


def test_no_coordinates_in_logs(api):
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.addHandler(handler)
    old_level = root.level
    root.setLevel(logging.INFO)
    secret_points = [(52.3791, 4.9003), (48.8566, 2.3522), (13.0827, 80.2707), (95.4321, 4.9)]
    try:
        for lat, lon in secret_points:
            api.guidance(location={"lat": lat, "lon": lon})
        api.guidance(location={"lat": 52.3791, "lon": 4.9003}, duration_min=999)
        engine = api.app.state.stores["sandbox"].current.engine
        engine.choose = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
        api.guidance(location={"lat": 52.3791, "lon": 4.9003})
        del engine.choose
    finally:
        root.removeHandler(handler)
        root.setLevel(old_level)
    lines = [json.loads(x) for x in stream.getvalue().splitlines()]
    requests = [x for x in lines if x["msg"] == "request"]
    assert len(requests) == 6 and all("request_id" in x for x in requests)
    forbidden = {
        "52.3791",
        "52.38",
        "4.9003",
        "48.8566",
        "48.86",
        "2.3522",
        "2.35",
        "13.0827",
        "13.08",
        "80.2707",
        "80.27",
        "95.4321",
        "95.43",
    }
    for line in lines:
        assert not {"lat", "lon", "location", "body"} & set(line)
        for key, value in line.items():
            if key in ("latency_ms", "ts"):
                continue
            assert not any(f in str(value) for f in forbidden), (key, value)
        assert api.key.split("_", 2)[2] not in json.dumps(line)


def test_spec_matches_the_request_model():
    schema = CONTRACT.spec["components"]["schemas"]["GuidanceRequest"]
    for example in schema["examples"]:
        req = parse_request(json.dumps(example).encode())
        assert isinstance(req, GuidanceRequest)
    assert schema["properties"]["activity_type"]["enum"] == ACTIVITY_TYPES
    assert set(schema["properties"]) == set(GuidanceRequest.model_fields)
    assert set(schema["required"]) == {n for n, f in GuidanceRequest.model_fields.items() if f.is_required()}
    codes = set(CONTRACT.spec["components"]["schemas"]["Problem"]["properties"]["code"]["enum"])
    from gotime_api.problems import TITLES

    assert codes == set(TITLES)
    with pytest.raises(Problem):
        parse_request(b"[]")


def test_every_response_was_contract_checked():
    """Runs last in this file: the contract check saw responses from the tests above."""
    assert CONTRACT.checked > 50


def test_inconsistent_engine_window_is_an_engine_error(api, monkeypatch):
    """An engine result whose latest_start + duration != end is refused (500 engine-error), never served."""
    from gotime_engine.sandbox import SandboxEngine

    real = SandboxEngine.choose

    def broken(self, assessment, options):
        r = real(self, assessment, options)
        if r.window_status == "found":
            r.latest_start = r.window_end  # the old meaning could put latest_start after the activity's end
        return r

    monkeypatch.setattr(SandboxEngine, "choose", broken)
    r = api.guidance()
    assert r.status_code == 500 and r.json()["code"] == "engine-error"


def test_local_docs_only_when_added():
    """Swagger UI and the spec are served only by add_local_docs (GOTIME_ENV=local), never by default."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from gotime_api.app import add_local_docs

    plain = TestClient(FastAPI(docs_url=None, redoc_url=None, openapi_url=None))
    assert plain.get("/docs").status_code == 404
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    add_local_docs(app)
    local = TestClient(app)
    assert "swagger-ui" in local.get("/docs").text
    assert local.get("/openapi.yaml").text.startswith("openapi: 3.1.0")
