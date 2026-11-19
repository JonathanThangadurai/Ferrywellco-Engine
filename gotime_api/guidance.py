"""POST /v1/guidance without the web framework: from a validated request to the response body.

Steps: snap the (already rounded) point -> check data freshness -> check the date -> turn local start
times into instants, cut at the forecast end -> engine.choose() on the stored assessment -> response.
"""

import hashlib
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from gotime_engine import WindowOptions
from gotime_engine.snapshot import assessment_key

from .freshness import data_status, forecast_until
from .locations import allowed_dates, local_instant, snap
from .models import GuidanceRequest
from .problems import Problem
from .settings import Settings
from .snapshots import Loaded


def etag(loaded: Loaded, req: GuidanceRequest) -> str:
    """Weak ETag: same snapshot + same (rounded) request = same guidance."""
    digest = hashlib.sha256(json.dumps(req.model_dump(mode="json"), sort_keys=True).encode()).hexdigest()
    return f'W/"{loaded.id}-{digest[:16]}"'


def max_age_s(loaded: Loaded, now_utc: datetime) -> int:
    """Seconds until the next snapshot is expected, between 60 and 900."""
    return int(min(max((loaded.expected_next_utc - now_utc).total_seconds(), 60), 900))


def _local(t: datetime, tz: ZoneInfo) -> str:
    return t.astimezone(tz).isoformat()


def handle(
    req: GuidanceRequest, loaded: Loaded, settings: Settings, now_utc: datetime, request_id: str
) -> tuple[dict, dict]:
    """(response body, fields for the request log). Raises Problem for every error case."""
    loc, distance = snap(req.location.lat, req.location.lon, loaded.locations)
    log_fields = {"location_id": loc["id"], "distance_km": distance, "snapshot_id": loaded.id}

    status, sources = data_status(loc, now_utc, settings)
    if status == "unavailable":
        raise Problem(503, "data-unavailable", headers={"Retry-After": "600"})

    if req.date not in allowed_dates(loc["timezone"], now_utc, settings.max_days_ahead):
        raise Problem(
            422, "date-out-of-range", detail=f"date must be today to today + {settings.max_days_ahead}"
        )
    key = assessment_key(loc["id"], req.date.isoformat(), req.profile_id)
    assessment = loaded.snap["assessments"].get(key)
    if assessment is None:
        raise Problem(422, "beyond-forecast-horizon", detail="This date is not in the current forecast.")

    duration = timedelta(minutes=req.duration_min)
    earliest = local_instant(req.date, req.earliest_start, loc["timezone"])
    latest = local_instant(req.date, req.latest_start, loc["timezone"])
    horizon = forecast_until(loc)
    if earliest + duration > horizon:
        raise Problem(422, "beyond-forecast-horizon", detail="The activity would end after the forecast.")
    latest = min(latest, horizon - duration)  # a window must fit the whole activity inside the forecast

    try:
        result = loaded.engine.choose(assessment, WindowOptions(earliest, latest, req.duration_min))
    except Exception as e:
        raise Problem(500, "engine-error") from e

    tz = ZoneInfo(loc["timezone"])
    window = {"status": result.window_status}
    if result.window_status == "found":
        window.update(
            start=_local(result.window_start, tz),
            end=_local(result.window_end, tz),
            latest_start=_local(result.latest_start, tz),
        )
    hint = {"status": "given", "code": result.intensity_hint} if result.intensity_hint else {"status": "none"}
    reasons = []
    for r in result.reasons:
        item = {"code": r.code, "params": r.params, "source": r.source}
        if r.text:
            item["text"] = r.text
        reasons.append(item)
    used = {s["source"] for s in loc["sources"]}
    body = {
        "mode": loaded.snap["mode"],
        "request_id": request_id,
        "snapshot_id": loaded.id,
        "location": {
            "id": loc["id"],
            "alias": loc["alias"],
            "timezone": loc["timezone"],
            "distance_km": distance,
        },
        "date": req.date.isoformat(),
        "window": window,
        "intensity_hint": hint,
        "indoor_alternative": {"status": result.indoor_alternative},
        "reasons": reasons,
        "data": {
            "status": status,
            "sources": sources,
            "attribution": sorted({settings.attribution[s] for s in used if s in settings.attribution}),
        },
    }
    return body, log_fields


def coverage(loaded: Loaded, settings: Settings, now_utc: datetime) -> dict:
    """GET /v1/coverage: the locations of the snapshot, with the dates that can be asked for now."""
    out = []
    for loc in loaded.locations.values():
        status, _ = data_status(loc, now_utc, settings)
        allowed = {d.isoformat() for d in allowed_dates(loc["timezone"], now_utc, settings.max_days_ahead)}
        out.append(
            {
                "id": loc["id"],
                "aliases": [loc["alias"]],
                "name": loc["name"],
                "lat": loc["lat"],
                "lon": loc["lon"],
                "radius_km": loc["radius_km"],
                "timezone": loc["timezone"],
                "dates_available": [d for d in loc["dates"] if d in allowed],
                "forecast_until": (forecast_until(loc) or now_utc).isoformat().replace("+00:00", "Z"),
                "data_status": status,
            }
        )
    return {"snapshot_id": loaded.id, "locations": out}
