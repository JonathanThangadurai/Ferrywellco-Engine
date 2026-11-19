"""Request model for POST /v1/guidance (docs/openapi.yaml; draft, to agree with the partner).

Strict: unknown fields are rejected, so no personal fields can come in. Coordinates are rounded to 2
decimals (~1 km) inside validation, so nothing after this point ever sees the raw point.
"""

import datetime as dt
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from gotime_engine.protocol import ACTIVITY_TYPES

from .problems import Problem

HHMM = r"^([01][0-9]|2[0-3]):[0-5][0-9]$"
YesNoUnknown = Literal["yes", "no", "unknown"]
ActivityType = Literal[tuple(ACTIVITY_TYPES)]  # type: ignore[valid-type]


class Point(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    lat: float = Field(ge=-90, le=90, allow_inf_nan=False)
    lon: float = Field(ge=-180, le=180, allow_inf_nan=False)


class GuidanceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    location: Point
    date: dt.date
    earliest_start: str = Field(pattern=HHMM)
    latest_start: str = Field(pattern=HHMM)
    activity_type: ActivityType
    duration_min: int = Field(ge=10, le=240)
    heat_adapted: YesNoUnknown = "unknown"
    hay_fever: YesNoUnknown = "unknown"

    @model_validator(mode="after")
    def round_point_and_check_times(self):
        self.location.lat = round(self.location.lat, 2)
        self.location.lon = round(self.location.lon, 2)
        if self.latest_start < self.earliest_start:  # HH:MM strings compare like times
            raise ValueError("latest_start must not be before earliest_start")
        return self

    @property
    def profile_id(self) -> str:
        return f"{self.activity_type}.{self.heat_adapted}.{self.hay_fever}"


def parse_request(body: bytes) -> GuidanceRequest:
    """Validate a JSON body. Errors name the field only, never the value that was sent."""
    try:
        return GuidanceRequest.model_validate_json(body)
    except ValidationError as e:
        errors = [
            {"field": ".".join(str(p) for p in err["loc"]) or "body", "reason": err["msg"]}
            for err in e.errors()
        ]
        status = 400 if any(err["type"] == "json_invalid" for err in e.errors()) else 422
        raise Problem(status, "invalid-request", errors=errors) from None
