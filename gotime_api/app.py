"""The GOTime API (FastAPI, kept thin): routes, auth, rate limits, request ids, errors, caching headers.

It only reads the in-memory snapshot; it never reads Parquet, downloads data or runs the expensive part
of an engine (docs/api-design.md). Logic lives in guidance.py, locations.py and freshness.py.
"""

import logging
import re
import threading
import time
import uuid
from collections.abc import Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from .auth import KeyRecord, KeyStore
from .guidance import coverage, etag, handle, max_age_s
from .logs import ACCESS, setup_logging
from .models import parse_request
from .problems import Problem, problem_response
from .ratelimit import RateLimiter
from .settings import Settings, load_settings
from .snapshots import Loaded, SnapshotStore

REQUEST_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
log = logging.getLogger(__name__)


def create_app(
    settings: Settings | None = None,
    clock: Callable[[], datetime] | None = None,
    stores: dict[str, SnapshotStore] | None = None,
    keys: KeyStore | None = None,
    reload_in_background: bool = True,
) -> FastAPI:
    settings = settings or load_settings()
    clock = clock or (lambda: datetime.now(UTC))
    keys = keys or KeyStore(settings.keys_file, settings.keys_reload_s)
    limiter = RateLimiter()
    if stores is None:
        stores = {mode: SnapshotStore(mode) for mode in settings.modes}
        for store in stores.values():
            store.reload()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        """Check for a new snapshot every few seconds in a background thread (off the request path)."""
        stop = threading.Event()

        def loop():
            while not stop.wait(settings.snapshot_reload_s):
                for store in stores.values():
                    store.reload()

        if reload_in_background:
            threading.Thread(target=loop, name="snapshot-reload", daemon=True).start()
        yield
        stop.set()

    app = FastAPI(
        title="GOTime API",
        version="0.1.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.state.settings, app.state.stores, app.state.keys = settings, stores, keys

    @app.middleware("http")
    async def request_frame(request: Request, call_next):
        """Request id, unhandled errors as problem+json, and one JSON log line per request."""
        given = request.headers.get("x-request-id", "")
        rid = given if REQUEST_ID.match(given) else uuid.uuid4().hex
        request.state.request_id, request.state.log = rid, {}
        t0 = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            log.exception("unhandled error", extra={"fields": {"request_id": rid}})
            response = problem_response(Problem(500, "internal-error"), rid)
            request.state.log["code"] = "internal-error"
        response.headers["X-Request-ID"] = rid
        fields = {
            "request_id": rid,
            "method": request.method,
            "route": request.url.path,
            "status": response.status_code,
            "latency_ms": round((time.perf_counter() - t0) * 1000, 2),
            **request.state.log,
        }
        ACCESS.info("request", extra={"fields": fields})
        return response

    @app.exception_handler(Problem)
    async def on_problem(request: Request, p: Problem):
        request.state.log["code"] = p.code
        if p.status >= 500 and p.__cause__ is not None:
            log.error(
                "%s", p.code, exc_info=p.__cause__, extra={"fields": {"request_id": request.state.request_id}}
            )
        return problem_response(p, request.state.request_id)

    @app.exception_handler(StarletteHTTPException)
    async def on_http_error(request: Request, e: StarletteHTTPException):
        code = {404: "not-found", 405: "method-not-allowed"}.get(e.status_code, "invalid-request")
        return await on_problem(request, Problem(e.status_code, code))

    def authorise(request: Request) -> tuple[KeyRecord, Loaded]:
        """Key -> rate limit -> the snapshot for the key's mode."""
        key = keys.verify(request.headers.get("authorization"))
        request.state.log.update(key_id=key.key_id, mode=key.mode)
        limiter.check(
            key.key_id, key.rate_per_minute or settings.rate_per_minute, key.rate_burst or settings.rate_burst
        )
        store = stores.get(key.mode)
        loaded = store.current if store else None
        if loaded is None:
            raise Problem(503, "snapshot-unavailable", headers={"Retry-After": "60"})
        return key, loaded

    async def read_body(request: Request) -> bytes:
        """The body, refusing anything over max_body_bytes without reading it all."""
        limit = settings.max_body_bytes
        if int(request.headers.get("content-length") or 0) > limit:
            raise Problem(413, "payload-too-large")
        body = b""
        async for chunk in request.stream():
            body += chunk
            if len(body) > limit:
                raise Problem(413, "payload-too-large")
        return body

    @app.get("/v1/health")
    def health():
        return {"status": "ok"}

    @app.get("/v1/ready")
    def ready():
        now = clock()
        ages = []
        for mode in settings.modes:
            loaded = stores[mode].current if mode in stores else None
            if loaded is None:
                raise Problem(503, "snapshot-unavailable", headers={"Retry-After": "60"})
            age = (now - loaded.created_utc).total_seconds()
            if age > settings.ready_max_snapshot_age_h * 3600:
                raise Problem(
                    503,
                    "snapshot-unavailable",
                    detail="The newest snapshot is too old.",
                    headers={"Retry-After": "300"},
                )
            ages.append((age, loaded.id))
        age, sid = max(ages)
        return {"status": "ready", "snapshot_id": sid, "snapshot_age_s": int(max(age, 0))}

    @app.get("/v1/coverage")
    def get_coverage(request: Request):
        _, loaded = authorise(request)
        tag = f'W/"{loaded.id}"'
        if request.headers.get("if-none-match") == tag:
            return Response(status_code=304, headers={"ETag": tag})
        return JSONResponse(coverage(loaded, settings, clock()), headers={"ETag": tag})

    @app.post("/v1/guidance")
    async def post_guidance(request: Request):
        _, loaded = authorise(request)
        req = parse_request(await read_body(request))
        now = clock()
        body, fields = handle(req, loaded, settings, now, request.state.request_id)
        request.state.log.update(fields)
        headers = {"ETag": etag(loaded, req), "Cache-Control": f"private, max-age={max_age_s(loaded, now)}"}
        if request.headers.get("if-none-match") == headers["ETag"]:
            return Response(status_code=304, headers=headers)
        return JSONResponse(body, headers=headers)

    return app


def serve_app() -> FastAPI:
    """Factory for uvicorn (`gotime-api serve`): JSON logs in every worker process, then the app."""
    setup_logging()
    return create_app()
