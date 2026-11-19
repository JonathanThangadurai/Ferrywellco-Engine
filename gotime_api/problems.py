"""Errors as RFC 9457 problem details (application/problem+json) with a stable `code`.

Error responses never echo input values (no coordinates, no keys); `errors` lists field paths only.
"""

from fastapi.responses import JSONResponse

TITLES = {
    "invalid-request": "The request is not valid",
    "not-covered": "This place is not covered",
    "date-out-of-range": "The date is out of range",
    "beyond-forecast-horizon": "The window lies beyond the forecast",
    "unauthorized": "Missing or invalid API key",
    "not-found": "Not found",
    "method-not-allowed": "Method not allowed",
    "payload-too-large": "The request body is too large",
    "rate-limited": "Too many requests",
    "engine-error": "The guidance engine failed",
    "internal-error": "Internal error",
    "snapshot-unavailable": "No guidance snapshot is loaded",
    "data-unavailable": "Forecast data is too old",
}


class Problem(Exception):
    """Raise anywhere in request handling; turned into a problem+json response."""

    def __init__(
        self,
        status: int,
        code: str,
        detail: str | None = None,
        headers: dict[str, str] | None = None,
        errors: list[dict] | None = None,
    ):
        if code not in TITLES:
            raise ValueError(f"unknown problem code {code}")
        super().__init__(code)
        self.status, self.code, self.detail = status, code, detail
        self.headers, self.errors = headers or {}, errors


def problem_response(p: Problem, request_id: str) -> JSONResponse:
    body = {
        "type": f"urn:gotime:problem:{p.code}",
        "title": TITLES[p.code],
        "status": p.status,
        "code": p.code,
        "request_id": request_id,
    }
    if p.detail:
        body["detail"] = p.detail
    if p.errors:
        body["errors"] = p.errors
    return JSONResponse(
        body,
        status_code=p.status,
        headers={**p.headers, "X-Request-ID": request_id},
        media_type="application/problem+json",
    )
