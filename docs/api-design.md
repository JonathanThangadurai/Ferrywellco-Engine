# GOTime API design (stage D1)

> **Status: DRAFT for Jon's review.** The request and response fields are a draft to agree with AEIVA
> (`openapi.yaml`). The engine's output fields are a draft to agree with Tilman (`engine-interface.md`). This
> document builds only the place where the guidance science plugs in; it does not design that science.

## 1. Principle and processes

**Compute once per forecast cycle, serve ready answers.** There are three separate processes. They talk only
through storage (`GOTIME_STORAGE_ROOT`), never directly.

```
 cron          ingest (exists)        precompute (new)                 API (new, stateless)
 ──────▶  sources → raw → hourly  ──▶  hourly → engine.assess() ──▶   snapshot in memory
                 table (Parquet)       → serving snapshot (files)       → engine.choose() → JSON
```

1. **ingest** (stage C) writes the hourly canonical table and the cycles table.
2. **precompute** runs after each ingest. It checks whether the set of newest complete cycles has changed
   (`latest_complete_cycles`). If it has not, it does nothing. If it has, then for each location, each local
   date (today to today + 3) and each engine profile, it reads `get_hourly(...)` and calls `engine.assess(...)`.
   It writes all results as one new snapshot. This is where the expensive work happens, once per cycle.
3. **API** loads the current snapshot into memory. For each request it does only cheap work: validate, snap
   the point, look up the assessment, call `engine.choose(...)` (picks the window from ready hourly results),
   and add data status. It never reads Parquet, never downloads anything and never runs the expensive part.

Why the engine has two steps: request options such as earliest/latest start and duration are continuous, so
answers cannot be precomputed for every combination. The engine assesses hours per discrete *profile*
(activity type × heat_adapted × hay_fever) once per cycle. Then a fast, pure `choose` picks the window per
request. Both steps are Tilman's code (`engine-interface.md`).

## 2. Serving snapshot

- **Content:** `serving/<mode>/<snapshot_id>/` holds:
  - `manifest.json`: snapshot_id, mode, engine name and version, created time, and the cycles used per source
    (issue and publish time).
  - `locations.json`: the resolved locations, with the per-source cell for each.
  - `assessments.json.gz`: keyed by `(location_id, local_date, profile_id)`.
  - `checksums.json`.
- **snapshot_id** = `<newest issue time>-<first 8 characters of a hash over the cycles used and the engine
  version>`, e.g. `20261119T06Z-3fa9c2d1`. The same inputs give the same id, so a rerun is harmless.
- **Atomic swap:**
  1. precompute writes the folder under a temporary name, verifies the checksums and renames it.
  2. Only then does it replace `serving/<mode>/CURRENT` (a one-line file with the id). It writes a temporary
     file and calls `os.replace`, which is atomic on POSIX. On S3, a single-object PUT is atomic.
- **Reload:** the API checks `CURRENT` every 30 s. When the id changes, it loads and validates the whole new
  snapshot next to the old one, then swaps one Python reference. A request in flight keeps the snapshot it
  started with. If the new snapshot fails to load, the API keeps the old one and logs an error.
- **Retention:** the last 4 snapshots per mode are kept for rollback; rolling back means writing an older id
  to `CURRENT`.
- **Modes:** `sandbox` (SandboxEngine, clearly synthetic) and `live` (Tilman's engine, once it exists). Each
  has its own snapshot. Each API key has one mode, and the response always states its `mode`. A live key gets
  503 `snapshot-unavailable` until a live engine exists.

## 3. Locations and snapping

- **Key:** a location is keyed by its weather grid cell, the ECMWF 0.1° nearest land cell (stage C point
  rule). The id format is `ecmwf0p1_<n|s><lat>_<e|w><lon>`, e.g. `ecmwf0p1_n52.40_e4.90` and
  `ecmwf0p1_n13.10_e80.20`.
- **Aliases:** cities are aliases (`amsterdam`, `chennai`). Air quality cells per location come from the
  sites table (CAMS EU, or CAMS global with land cell by default).
- **Configuration:** `config/locations.yaml` gains `radius_km` (default 15) and keeps `tz`.
- **Snapping:**
  1. Round lat and lon to 2 decimals (privacy, section 7).
  2. Compute the great-circle distance to the reference point (cell centre) of each supported location.
  3. Take the nearest one within its `radius_km`.
  4. If there is none, return **422 `not-covered`**.
- **Response:** gives `location.id`, `location.alias`, `location.timezone` and `distance_km` (1 decimal).
  Today the city points are 3.3 km (Amsterdam) and 7.9 km (Chennai) from their cells.
- **Time zone:** comes from the location config, never from the client.
  - `date`, `earliest_start` and `latest_start` are local to the location.
  - The response times are RFC 3339 with the local offset for that instant (daylight-saving days have 23 or
    25 hours).
- **Open point:** 15 km around one cell does not cover all of Chennai (e.g. Tambaram is about 21 km away).
  It can be fixed with more cells or a larger radius per location.

## 4. Data freshness

Age = request time − forecast `issue_time`, never the download time. It is computed per request from the
manifest, because the age grows while the snapshot is served. Thresholds are placeholders in config
(`config/serving.yaml`) until we agree them:

| source | normal age (cycle + delay) | ok | degraded | unavailable |
|---|---|---|---|---|
| ecmwf_ifs (weather) | 8.5–15.5 h (4 runs/day) | ≤ 18 h | ≤ 36 h | > 36 h |
| cams_eu (air, pollen) | ~10–34 h (1 run/day) | ≤ 36 h | ≤ 60 h | > 60 h |
| cams_global (air) | ~12–24 h (2 runs/day) | ≤ 30 h | ≤ 54 h | > 54 h |

- The overall `data.status` is the worst status among the **required** sources (weather plus air quality).
  Pollen is optional: if it is unavailable, the response says so in its source entry and drops to `degraded`.
- `unavailable`: no guidance is given and the API returns **503 `data-unavailable`** with `Retry-After`.
  `degraded` still returns 200, with the status stated.
- Horizon: a request whose window (latest start + duration) lies beyond the forecast end of a required source
  gets **422 `beyond-forecast-horizon`**. `GET /v1/coverage` lists `forecast_until` per location.

## 5. Scaling path

1. **Now:** one VPS (4 GB). Caddy terminates HTTPS and proxies to one uvicorn process. Cron runs ingest and
   precompute; storage is local disk.
2. **More traffic:** run more API copies (processes or containers) behind Caddy. They are stateless and each
   loads the snapshot. Rate limits are per copy at first (limit ÷ copies); later they move to a shared counter
   (e.g. Redis) or Caddy.
3. **More cells:** add locations to the config. Precompute runs per location, so it can run in parallel, and
   each location is independent. Snapshot size grows linearly: 2 locations × 4 dates × 24 profiles today. At
   thousands of cells, split the snapshot per region and load only the regions a copy serves.
4. **More machines:** set the storage root to `s3://...` (fsspec is already in place). Then ingest, precompute
   and API copies can run on different machines.

## 6. Performance targets

- p95 < 100 ms at the server for `POST /v1/guidance`, measured from the in-memory snapshot (lookup + `choose`,
  which works over at most 24 hourly values).
- Budgets:
  - `choose` < 5 ms.
  - Snapshot reload < 5 s, off the request path.
  - Precompute < 2 min per cycle for today's locations.
- Coverage, health and ready responses < 20 ms.

## 7. Security and privacy

- **API keys:** `gt_<key_id>_<secret>`, where the secret is 32 random bytes. The server stores only
  SHA-256(secret), which is enough for random secrets, and compares in constant time.
  - Keys sit in a file outside the repo (`~/.config/gotime/api_keys.json`, chmod 600), one entry per partner:
    key_id, partner, hash, modes, rate limit, created, revoked.
  - A CLI creates a key (prints the secret once) and revokes it.
  - Keys are sent as `Authorization: Bearer ...` and are never logged; logs carry the `key_id` only.
- **Rate limits:** a token bucket per key (default 60 per minute, burst 20). Exceeding it returns **429** with
  `Retry-After`. Failed authentication is limited at Caddy.
- **HTTPS only:** Caddy, automatic certificates, HSTS. The API itself listens on localhost only.
- **Input limits:**
  - Body ≤ 4 KB.
  - Strict Pydantic models with `extra = forbid`, so unknown or personal fields are rejected.
  - lat −90…90, lon −180…180.
  - `date` between today and today + 3 at the location.
  - Duration 10–240 min.
  - Enums for every coded field.
  - `earliest_start` ≤ `latest_start`, on the same local date (no midnight crossing in 0.1.0).
- **Privacy:**
  - Lat and lon are rounded to 2 decimals (~1 km) the moment the request is parsed, before snapping, logging or
    anything else.
  - Raw or rounded coordinates are never written to logs, storage or error messages; logs carry `location_id`
    and `distance_km` only.
  - The app does not log client IPs, and the uvicorn access log is off (our own JSON log replaces it).
  - No request bodies are stored. The API accepts no personal fields.
- **Logs:** one JSON line per request with request_id (taken from `X-Request-ID` if valid, else generated, and
  returned in every response), key_id, route, status, problem code, latency_ms, location_id, snapshot_id and
  mode.

## 8. Caching

- `ETag` = `W/"<snapshot_id>-<hash of the normalised request>"`. `If-None-Match` returns 304.
- `Cache-Control: private, max-age=N`, where N is the time until the next expected snapshot, between 60 and
  900 s.
- Coverage uses the snapshot id as its ETag. Shared proxies do not cache POST; this is for partner clients.

## 9. Failure behaviour

| situation | behaviour |
|---|---|
| no snapshot at start-up | `/v1/ready` 503; guidance 503 `snapshot-unavailable`; `/v1/health` 200 (process alive) |
| new snapshot broken or missing files | keep serving the old one; error log |
| precompute or ingest fails | old snapshot keeps serving; freshness moves to `degraded`, then 503 `data-unavailable` |
| storage unreachable | keep the in-memory snapshot; reload retried every 30 s |
| `engine.choose` raises | 500 `engine-error` with request_id; the error is logged without the request body |
| no window fits the options | 200 with `window.status = none` (an answer, not an error) |
| point outside coverage, date or window out of range | 422 with a specific code |
| missing, invalid or revoked key / too many requests | 401 / 429 |

All errors are RFC 9457 `application/problem+json` with a stable `code` and the `request_id`. Error responses
never echo input values.

## 10. Open points for Jon

1. Activity types for 0.1.0 (draft: `walk`, `run`, `cycle`, `outdoor_workout`) to agree with AEIVA and Tilman.
2. Freshness thresholds (section 4) and whether missing pollen should give `degraded` or stay `ok`.
3. Radius per location (Chennai coverage, section 3).
