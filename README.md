# Ferrywellco Engine: GOTime data layer

This repository is `Ferrywellco-Engine` on GitHub; the Python package inside it is `gotime_data`.

The GOTime data layer turns weather and air quality forecasts and observations into one hourly table per location,
which the wellness algorithm reads through a small interface. GOTime gives wellness guidance (when and how to be active
outside); it is not a health service.

Status: stages A to D2 done (data layer, GOTime API with a sandbox engine). **Stage D3 (deployment, scheduler,
monitoring, CI) on branch `d3-ops`, awaiting review.**

## What each stage does

1. **fetch**: download the newest data per source, only our variables, cropped to the city boxes right away
   (`raw/<source>/<batch>/`, kept 2 days). ECMWF: own byte-range downloader, AWS first, ECMWF server as fallback.
2. **ingest**: turn raw files into the canonical hourly table: nearest land grid cell, unit conversion, UTC,
   3-hourly steps filled to hourly (`interpolated`), missing hours as null + `missing`. Validated, then written as
   Parquet (`live/`, 10 days; then `archive/`, kept). Chosen grid cells and stations go to the sites table, forecast
   cycles (issue, publish, complete) to the cycles table.
3. **read**: `gotime_data.read.get_hourly(...)` plus 24-hour and maximum 8-hour means, computed on read
   (`docs/data-interface.md`, draft pending health-engine review).
4. **clean**: retention (raw 2 days, live 10 days, then archive).

## How to run

```
uv sync
uv run gotime-data check                  # one small request per source: OK / FAILED and why (no keys printed)
uv run gotime-data fetch ecmwf            # sources: ecmwf cams_eu cams_global eea knmi era5 openaq
uv run gotime-data ingest ecmwf           # every raw batch not ingested yet
uv run gotime-data read amsterdam --hours 48 --variables t2m_c pm25_ugm3
uv run gotime-data clean
uv run pytest                             # no network; small saved fixtures in tests/fixtures/
uv run ruff check .
```

### API (stage D)

```
uv run gotime-api precompute              # after ingest: new serving snapshot if the forecast cycles changed
uv run gotime-api keys create --partner partner-app --mode sandbox   # key written once to a 600 file, never printed
uv run gotime-api keys list | revoke <key_id>
uv run gotime-api serve --port 8000       # POST /v1/guidance, GET /v1/coverage, /v1/health, /v1/ready
uv run python scripts/load_test.py --key-file <file>   # p50/p95 latency and requests/s
```

Three separate processes share only storage: ingest, then precompute (`gotime_engine/`: the protocol where the
health engine plugs in, and a clearly synthetic `SandboxEngine`), then the API (`gotime_api/`). The API serves the
snapshot from memory. API keys live hashed in `GOTIME_API_KEYS_FILE` (default `~/.config/gotime/api_keys.json`,
outside the repo). Settings are in `config/serving.yaml`.

### Operations (stage D3)

```
uv run gotime-ops scheduler              # forecast schedule: fetch, ingest, quality check, precompute; heartbeats
uv run gotime-ops monitor --every 900    # synthetic check of the API; HC_PING_MONITOR
uv run gotime-ops source-check ecmwf     # live access and format check of one source
uv run gotime-ops backup | restore       # archive/, meta/, climatology/ <-> GOTIME_BACKUP_TARGET
docker compose up -d                     # api, scheduler, monitor, caddy (docs/operations.md)
```

Stage B measurement scripts: `scripts/check_access.py`, `scripts/size_test.py` (see `docs/size-test.md`).

ADS/CDS (CAMS, ERA5) allow only a few queued requests per dataset; the pipeline sends one request at a time.
OpenAQ (`openaq`) is for internal verification only: it is left out of `get_hourly` unless
`include_internal=True`, never shown to partners, and its data is never committed here.

## Docs

- `docs/data-sources.md`: every source, verified live: access, licence, units, time conventions, pitfalls.
- `docs/data-pipeline-design.md`: stages, canonical table, retention, size and memory estimates.
- `docs/size-test.md`: measured sizes, times and memory; storage recommendation.
- `docs/data-interface.md`: how the algorithm reads data (draft, pending health-engine review).
- `docs/api-design.md`: GOTime API design, stage D1 (draft for review).
- `docs/engine-interface.md`: where the guidance engine plugs in (draft, to agree with the health engine).
- `docs/openapi.yaml`: API 0.1.0 draft (to agree with the partner).
- `docs/partner-guide.md`: how the partner app uses the sandbox; `docs/postman/`: Postman collection and environment template.
- `docs/operations.md`: deploying on the server, keys, alerts, backups.
- `docs/testing.md`: what runs where and when.
- `docs/architecture/gotime-data-architecture-v1.drawio`: architecture diagram (open with [diagrams.net](https://app.diagrams.net/)).

## Environment variables (names only; never commit values)

| name | used for |
|---|---|
| `KNMI_API_KEY` | KNMI Open Data API |
| `KNMI_EDR_API_KEY` | KNMI EDR API (hourly observations) |
| `COPERNICUS_KEY` | ADS and CDS key (CAMS, ERA5) |
| `ADS_URL` | ADS API URL (CAMS) |
| `CDS_URL` | CDS API URL (ERA5) |
| `OPENAQ_API_KEY` | OpenAQ (Chennai observations, internal use only) |
| `GOTIME_STORAGE_ROOT` | storage root, local path or `s3://...` (default `./data`) |
| `GOTIME_API_KEYS_FILE` | hashed partner API keys (default `~/.config/gotime/api_keys.json`) |
| `GOTIME_SERVING_CONFIG` | API settings file (default `config/serving.yaml`) |
| `HC_PING_INGEST`, `HC_PING_PRECOMPUTE`, `HC_PING_MONITOR` | heartbeat ping URLs (treated as secrets; unset = skipped) |
| `GOTIME_MONITOR_KEY` | sandbox API key used by the synthetic monitor |
| `GOTIME_BACKUP_TARGET` | backup folder or `s3://...` |

ERA5 and CAMS both use `COPERNICUS_KEY`; `~/.cdsapirc` holds the same CDS key for other tools. If the variables are not set, scripts read `~/.config/gotime/keys.env`.

## Licences to accept (once, logged in on the website)

- ADS: `cams-europe-air-quality-forecasts`, `cams-global-atmospheric-composition-forecasts`
- CDS: `reanalysis-era5-single-levels`

## Attribution

Contains ECMWF open data (CC BY 4.0). Generated using Copernicus Atmosphere Monitoring Service and Copernicus Climate
Change Service information (CC BY 4.0). Air quality observations: European Environment Agency. Weather observations:
KNMI (CC BY 4.0). All data has been modified (cropped, converted, interpolated). Details per source are in `docs/data-sources.md`.
Test fixtures: see `tests/fixtures/README.md` (the OpenAQ fixture is synthetic). OpenAQ data (internal only):
CPCB / TNPCB via OpenAQ.
