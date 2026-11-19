# GOTime API design (stage D1)

> **Status: DRAFT for review.** The request and response fields are a draft to agree with the partner
> (`openapi.yaml`). The engine's output fields are a draft to agree with the health engine (`engine-interface.md`). This
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
request. Both steps belong to the health engine (`engine-interface.md`).

## 2. Serving snapshot

- **Content:** one file per snapshot, `serving/<mode>/<snapshot_id>.json.gz` (`gotime_engine/snapshot.py`):
  snapshot_id, mode, engine name and version, created time, expected time of the next one, the locations
  (id, alias, cell, radius, time zone, dates, and per source the issue time, forecast end and whether it is
  required), and the assessments keyed `location_id|local_date|profile_id`. Per-location results only:
  nothing about requests or people. Today: 2 locations × 4 dates × 36 profiles = 288 assessments, ~11 KB.
- **snapshot_id** = `<newest issue time>-<first 8 characters of a hash over the cycles used, the dates and the
  engine version>`, e.g. `20261118T00Z-f9228fd3`. The same inputs give the same id, so a rerun does nothing.
- **Atomic swap:**
  1. precompute writes the file under a temporary name and renames it, then reads it back.
  2. Only then does it replace `serving/<mode>/CURRENT` (a one-line file with the id), again via a temporary
     file and a rename (atomic on POSIX; on S3 a single-object PUT is atomic).
- **Reload:** the API checks `CURRENT` every 30 s. When the id changes, it loads and validates the whole new
  snapshot next to the old one, then swaps one Python reference. A request in flight keeps the snapshot it
  started with. If the new snapshot fails to load, the API keeps the old one and logs an error.
- **Retention:** the last 4 snapshots per mode are kept for rollback; rolling back means writing an older id
  to `CURRENT`.
- **Modes:** `sandbox` (SandboxEngine, clearly synthetic) and `live` (the health engine, once it exists). Each
  has its own snapshot. Each API key has one mode, and the response always states its `mode`. A live key gets
  503 `snapshot-unavailable` until a live engine exists.

## 3. Locations and snapping

- **Key:** a location is keyed by its weather grid cell, the ECMWF 0.1° nearest land cell (stage C point
  rule). The id format is `ecmwf0p1_<n|s><lat>_<e|w><lon>`, e.g. `ecmwf0p1_n52.40_e4.90` and
  `ecmwf0p1_n13.10_e80.20`.
- **Aliases:** cities are aliases (`amsterdam`, `chennai`). Air quality cells per location come from the
  sites table (CAMS EU, or CAMS global with land cell by default).
- **Configuration:** `radius_km` per location in `config/locations.yaml` (decided after D2: Amsterdam 15 km,
  Chennai 20 km; default 15) and `tz`.
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
- Chennai's 20 km still leaves out its southern edge (Tambaram is about 22 km away); more cells would fix that.

## 4. Data freshness

Age = request time − forecast `issue_time`, never the download time. It is computed per request from the
manifest, because the age grows while the snapshot is served. Thresholds are in `config/serving.yaml`.
**Starting values (decided after D2); review after two weeks of logs:**

| source | normal age (cycle + delay) | ok | degraded | unavailable |
|---|---|---|---|---|
| ecmwf_ifs (weather) | 8.5–15.5 h (4 runs/day) | ≤ 16 h | > 16 h | > 30 h |
| cams_eu (air, pollen) | ~10–34 h (1 run/day) | ≤ 36 h | > 36 h | > 60 h |
| cams_global (air) | ~12–24 h (2 runs/day) | ≤ 24 h | > 24 h | > 48 h |

- The overall `data.status` is the worst status among the **required** sources (weather plus air quality).
- Pollen (decided after D2): when the location has no usable pollen forecast (Chennai always; Amsterdam when
  CAMS Europe pollen is missing or unavailable), the response gets the reason `pollen_not_assessed` (source
  `data_layer`). The status becomes `degraded` only if the request has `hay_fever: yes`; otherwise it stays
  `ok`.
- `unavailable`: no guidance is given and the API returns **503 `data-unavailable`** with `Retry-After`.
  `degraded` still returns 200, with the status stated.
- Horizon: a window must fit the whole activity inside the forecast. The latest start is cut back to
  forecast end − duration; if even the earliest start does not fit (or the date is not in the snapshot), the
  answer is **422 `beyond-forecast-horizon`**. `GET /v1/coverage` lists `forecast_until` per location.

## 5. Scaling path

1. **Now:** one VPS (4 GB), `docker-compose.yml`. Caddy terminates HTTPS and proxies to the API container (2
   uvicorn workers). The scheduler container runs ingest and precompute per forecast cycle; storage is local
   disk (`docs/operations.md`).
2. **More traffic:** run more API copies (processes or containers) behind Caddy. They are stateless and each
   loads the snapshot. Rate limits are per copy at first (limit ÷ copies); later they move to a shared counter
   (e.g. Redis) or Caddy.
3. **More cells:** add locations to the config. Precompute runs per location, so it can run in parallel, and
   each location is independent. Snapshot size grows linearly: 2 locations × 4 dates × 36 profiles today. At
   thousands of cells, split the snapshot per region and load only the regions a copy serves.
4. **More machines:** set the storage root to `s3://...` (fsspec is already in place). Then ingest, precompute
   and API copies can run on different machines.

## 6. Performance targets and measurements

- **Target:** p95 < 100 ms at the server for `POST /v1/guidance`, measured from the in-memory snapshot
  (lookup + `choose`, which works over at most 25 hourly values).
- **Budgets:** `choose` < 5 ms; snapshot reload < 5 s, off the request path; precompute < 2 min per cycle.
- **Measured** on 2026-11-19 (MacBook M1 Pro, 8 cores), using `scripts/load_test.py` against `gotime-api serve`.
  The snapshot came from real ECMWF and CAMS data (18 Nov 00 UTC) for both cities. Requests mixed locations,
  dates, activities, durations and time ranges, and were all valid (200).

  | measurement | p50 | p95 | p99 | throughput |
  |---|---|---|---|---|
  | server side (`latency_ms` in the log), 5000 requests | 2.5 ms | 4.7 ms | 7.9 ms | |
  | client side, 1 client, 2000 requests | 1.6 ms | 1.8 ms | 2.3 ms | 630 req/s |
  | client side, 20 concurrent clients, 5000 requests | 20 ms | 190 ms | 420 ms | 430–460 req/s |

  With 20 clients the client-side tail is the load generator's own limit: it is one Python process on the
  same machine and tops out at about 450 requests/s. Four API workers gave the same numbers, and the server
  side stays under 5 ms at p95. A load generator on a separate machine is needed to find the server's real
  ceiling. Precompute took about 1 s for 288 assessments (3 s including start-up).

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

## 10. Decided after D2

1. Freshness starting values as in section 4, to review after two weeks of logs.
2. Missing pollen: `degraded` only with `hay_fever: yes`, otherwise `ok` plus reason `pollen_not_assessed`.
3. Radius per location: Amsterdam 15 km, Chennai 20 km.
4. One mode per API key (sandbox or live), enforced at authentication.

Open questions: health engine (`engine-interface.md`) and partner integration (`partner-guide.md`).
