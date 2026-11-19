"""Data freshness per request: age = now - forecast issue time, never the download time.

Per source: ok up to `ok` hours, degraded up to `degraded` hours, else unavailable (config/serving.yaml;
starting values, to review after two weeks of logs).
A source whose data quality check failed (meta/quality/) is at least degraded.
Overall: unavailable when a required source is unavailable (the API then answers 503 data-unavailable),
otherwise the worst status of the required sources.
Pollen (decided after D2): when no pollen data is available for the location, the response carries the
reason `pollen_not_assessed`; the status becomes degraded only if the request says hay_fever = yes.
The age is computed per request because it grows while a snapshot is served.
"""

from datetime import datetime

from .settings import Settings

ORDER = {"ok": 0, "degraded": 1, "unavailable": 2}


def source_status(age_h: float, thresholds: dict[str, float]) -> str:
    if age_h <= thresholds["ok"]:
        return "ok"
    if age_h <= thresholds["degraded"]:
        return "degraded"
    return "unavailable"


def data_status(location: dict, now_utc: datetime, settings: Settings) -> tuple[str, list[dict]]:
    """(overall status, one entry per source) for one location of the snapshot."""
    entries = []
    for s in location["sources"]:
        issue = datetime.fromisoformat(s["issue_time_utc"])
        age_h = max((now_utc - issue).total_seconds() / 3600, 0.0)
        status = source_status(age_h, settings.thresholds(s["source"]))
        if s.get("quality") == "failed":  # data quality check failed after ingest: at least degraded
            status = max(status, "degraded", key=ORDER.get)
        entries.append(
            {
                "source": s["source"],
                "required": s["required"],
                "status": status,
                "issue_time": issue.isoformat().replace("+00:00", "Z"),
                "forecast_age_h": round(age_h, 1),
            }
        )
    required = [e["status"] for e in entries if e["required"]]
    if not required or "unavailable" in required:
        return "unavailable", entries
    return max(required, key=ORDER.get), entries


def pollen_available(location: dict, entries: list[dict]) -> bool:
    """True when a source with pollen variables is present for this location and not unavailable."""
    status = {e["source"]: e["status"] for e in entries}
    return any(
        any(v.startswith("pollen_") for v in s["variables"]) and status.get(s["source"]) != "unavailable"
        for s in location["sources"]
    )


def forecast_until(location: dict) -> datetime | None:
    """Where the forecast ends for the required sources (the earliest end among them); None without any."""
    ends = [datetime.fromisoformat(s["forecast_until_utc"]) for s in location["sources"] if s["required"]]
    return min(ends, default=None)
