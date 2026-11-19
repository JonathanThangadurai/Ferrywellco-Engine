# Testing: what runs where and when

| what | where | when | keys needed | when it fails |
|---|---|---|---|---|
| ruff (lint + format) | GitHub Actions `CI` | every push and pull request | none | CI red |
| pytest: unit tests (sources on saved fixtures, store, read, schema) | `CI` | every push and PR | none | CI red |
| pytest: API tests, every response checked against `docs/openapi.yaml` (contract) | `CI` | every push and PR | none | CI red |
| pytest: golden responses (`tests/golden/`, fixed snapshot + fixed clock) | `CI` | every push and PR | none | CI red; a change must update the golden file in the same commit |
| pytest: property-based tests (hypothesis) | `CI` | every push and PR | none | CI red with a minimal failing example |
| OpenAPI document valid | `CI` | every push and PR | none | CI red |
| pip-audit (known vulnerabilities in pinned dependencies) | `CI` | every push and PR | none | CI red |
| Docker build + end-to-end: 3 API containers on the fixed snapshot, Postman collection with Newman | `CI` | every push and PR | throwaway keys made in the run | CI red; container logs in the run |
| live access and format check per source (EEA, KNMI, ECMWF, CAMS Europe, CAMS global, OpenAQ) | GitHub Actions `Source checks (nightly)` | 02:30 UTC daily, or by hand | repository secrets and variables | GitHub issue "Source check failing: X" (label `source-check`), closed automatically when it passes |
| ERA5 live check (slow CDS queue, up to 5 h) | `Source check ERA5 (weekly)` | Sunday 03:00 UTC | `COPERNICUS_KEY`, `CDS_URL` | same issue mechanism |
| data quality after each forecast ingest (ranges, completeness, missing cycles) | server, scheduler | after every cycle | none | ingest heartbeat `/fail` with the reason; source shown `degraded` in the API |
| cycle not published or downloaded in time | server, scheduler | at each cycle's deadline | none | ingest heartbeat `/fail` |
| synthetic monitoring: `/v1/ready` + one sandbox `/v1/guidance` | server, monitor | every 15 min | monitor key (`GOTIME_MONITOR_KEY`) | monitor heartbeat `/fail`, or no ping at all |

The per-push CI needs no real key. Everything in it runs offline on saved, cut-down fixtures and synthetic
data. The OpenAQ fixtures are synthetic too: OpenAQ data is never committed.

## What each test group guards

- **Contract** (`tests/api_support.py`): every response in the API tests must:
  - have a status code listed for that path in `docs/openapi.yaml`;
  - have the listed content type and a body that validates against the schema;
  - carry `X-Request-ID`, plus `Retry-After` where listed.

  The request model and the spec must list the same fields, and the problem codes must match.
- **Golden** (`tests/test_golden.py`): normal Amsterdam, Chennai, not covered, degraded, unavailable,
  missing key, bad input, no window, coverage. To change one on purpose:
  ```
  UPDATE_GOLDEN=1 uv run pytest tests/test_golden.py
  ```
  then review the diff and commit it with the code change. If the SandboxEngine version changes, first
  rebuild the fixed snapshot with `uv run python -m tests.e2e.make_snapshot`.
- **Property-based** (`tests/test_properties.py`):
  - A found window lasts exactly the duration, starts within [earliest, latest], stays within the local
    day's hours and touches only hours with data. This is checked on both daylight-saving days
    (2026-10-25, 2027-03-28) and in UTC+5:30.
  - Snapping never returns a location beyond its radius.
- **End-to-end** (CI job `docker-e2e`): the real image, started three times on `tests/fixtures/e2e` with
  fixed clocks:
  - fresh at 2026-11-19 10:00 UTC;
  - degraded at 20:00 the same day (ECMWF 20 h old);
  - unavailable at 2026-11-20 10:00 (34 h old).

  Newman runs all 12 requests of the Postman collection: normal Amsterdam, Chennai, not covered, degraded,
  unavailable, missing key, revoked key, bad input, no window fits, coverage, health and ready.
  `GOTIME_TEST_NOW` only works together with `GOTIME_ENV=test`.

## Running locally

```
uv run ruff check . && uv run ruff format --check .
uv run pytest                                   # all offline
uv run gotime-ops source-check knmi             # one live check (needs the keys)
```

Newman locally (Node needed): start the API three times with `GOTIME_ENV=test`,
`GOTIME_STORAGE_ROOT=tests/fixtures/e2e` and the three `GOTIME_TEST_NOW` values on ports 8001–8003. Then:

```
npx newman@6.2.1 run docs/postman/gotime.postman_collection.json -e tests/e2e/ci.postman_environment.json \
  --env-var apiKey=... --env-var revokedKey=...
```

Alert meanings and first steps: `docs/operations.md`.
