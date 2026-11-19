"""Synthetic monitoring: call the API like a partner would, every 15 minutes, and ping HC_PING_MONITOR.

Checks: /v1/ready is 200; /v1/coverage lists a location with a date; one sandbox /v1/guidance answers 200 in
mode sandbox. Uses a dedicated monitor key (GOTIME_MONITOR_KEY, a sandbox key; never logged). Also logs the
data age per source as a metrics line.
"""

import logging
import os
import time

import httpx

from .heartbeat import MONITOR, ping

log = logging.getLogger(__name__)


def check_once(base_url: str, key: str) -> tuple[bool, str, dict]:
    """(ok, reason, metrics). Never raises."""
    headers = {"Authorization": f"Bearer {key}"}
    try:
        with httpx.Client(base_url=base_url, timeout=10) as http:
            r = http.get("/v1/ready")
            if r.status_code != 200:
                return False, f"/v1/ready {r.status_code} {r.json().get('code', '')}", {}
            cov = http.get("/v1/coverage", headers=headers)
            if cov.status_code != 200:
                return False, f"/v1/coverage {cov.status_code} {cov.json().get('code', '')}", {}
            loc = cov.json()["locations"][0]
            body = {
                "location": {"lat": loc["lat"], "lon": loc["lon"]},
                "date": loc["dates_available"][0],
                "earliest_start": "00:00",
                "latest_start": "22:00",
                "activity_type": "walk",
                "duration_min": 30,
            }
            t0 = time.perf_counter()
            g = http.post("/v1/guidance", json=body, headers=headers)
            latency_ms = round((time.perf_counter() - t0) * 1000, 1)
            if g.status_code != 200:
                return False, f"/v1/guidance {g.status_code} {g.json().get('code', '')}", {}
            data = g.json()
            if data["mode"] != "sandbox":
                return False, "monitor key is not a sandbox key", {}
            metrics = {
                "guidance_latency_ms": latency_ms,
                "data_status": data["data"]["status"],
                "data_age_h": {s["source"]: s["forecast_age_h"] for s in data["data"]["sources"]},
                "snapshot_id": data["snapshot_id"],
            }
            return True, "", metrics
    except (httpx.HTTPError, ValueError, KeyError, IndexError) as e:
        return False, f"monitor request failed: {type(e).__name__}", {}


def run(base_url: str | None = None, every_s: float = 0) -> int:
    """One check (every_s = 0) or a loop. Pings HC_PING_MONITOR on success, /fail with a reason otherwise."""
    base_url = base_url or os.environ.get("GOTIME_MONITOR_URL", "http://api:8000")
    while True:
        key = os.environ.get("GOTIME_MONITOR_KEY")
        if not key:
            ok, reason, metrics = False, "GOTIME_MONITOR_KEY is not set", {}
        else:
            ok, reason, metrics = check_once(base_url, key)
        if ok:
            log.info("monitor ok", extra={"fields": {"metrics": metrics}})
        else:
            log.error("monitor failed: %s", reason)
        ping(MONITOR, ok, reason)
        if not every_s:
            return 0 if ok else 1
        time.sleep(every_s)
