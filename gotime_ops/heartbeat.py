"""Heartbeat pings (e.g. healthchecks.io), so a job that fails or stops running raises an alert.

The URLs come from environment variables (HC_PING_INGEST, HC_PING_PRECOMPUTE, HC_PING_MONITOR). They act as
secrets: never logged, printed or committed. If a variable is unset the ping is skipped with a warning; a
ping problem never fails the job itself.

Success: GET <url>. Failure: POST <url>/fail with a short reason as the body.
"""

import logging
import os

import httpx

from gotime_data.keys import load_keys

INGEST, PRECOMPUTE, MONITOR = "HC_PING_INGEST", "HC_PING_PRECOMPUTE", "HC_PING_MONITOR"
log = logging.getLogger(__name__)


def _url(name: str) -> str | None:
    load_keys()  # also reads ~/.config/gotime/keys.env when the variable is not in the environment
    return os.environ.get(name) or None


def ping(name: str, ok: bool = True, reason: str = "") -> bool:
    """Send one ping. Returns True when it was sent; never raises."""
    url = _url(name)
    if url is None:
        log.warning("%s is not set; heartbeat skipped", name)
        return False
    try:
        with httpx.Client(timeout=10) as http:
            if ok:
                http.get(url)
            else:
                http.post(f"{url.rstrip('/')}/fail", content=reason[:500].encode())
    except httpx.HTTPError as e:
        log.warning("%s ping failed (%s); continuing", name, type(e).__name__)  # the URL is never logged
        return False
    return True
