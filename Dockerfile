# GOTime: one image for the API, the scheduler (ingest + precompute) and the monitor.
# Slim Python base, dependencies from uv.lock, runs as a non-root user (uid 10001).
FROM python:3.12-slim-bookworm

COPY --from=ghcr.io/astral-sh/uv:0.9.26 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    GOTIME_STORAGE_ROOT=/data \
    GOTIME_API_KEYS_FILE=/run/gotime/keys/api_keys.json

WORKDIR /app

# Dependencies first (cached while only code changes). ecCodes comes as a wheel (eccodeslib).
COPY pyproject.toml uv.lock README.md LICENSE ./
RUN uv sync --frozen --no-dev --no-install-project

COPY gotime_data ./gotime_data
COPY gotime_api ./gotime_api
COPY gotime_engine ./gotime_engine
COPY gotime_ops ./gotime_ops
COPY config ./config
COPY climatology ./climatology
RUN uv sync --frozen --no-dev && rm /usr/local/bin/uv

RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin gotime \
    && mkdir -p /data /run/gotime/keys \
    && chown gotime:gotime /data
USER gotime

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD ["python", "-c", "import urllib.request,sys; urllib.request.urlopen('http://127.0.0.1:8000/v1/health', timeout=3)"]
CMD ["gotime-api", "serve", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
