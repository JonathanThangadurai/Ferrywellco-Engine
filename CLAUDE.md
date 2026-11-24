# CLAUDE.md: gotime-data

GOTime turns weather and air quality forecasts into wellness guidance for partner apps (B2B API). This repo is the
**data layer** (left side of `docs/architecture/gotime-data-architecture-v1.drawio`): data in, storage, bias correction, verification, data
interface. The health engine reads data through the interface defined here.

## Rules (always)

- V1 is as simple as possible. Every part must be explainable to the team. No clever abstractions; small files;
  docstrings say what a function does and why.
- Wellness, not health: never write "safe", "clean" or "healthy" in anything user-facing.
- Started from scratch. Never read, copy or imitate code from the old `gotime-engine` repo. Only `climatology/` (data) was copied.
- Python only. Work in stages and **STOP for review after each stage**.
- Git: commit as `Jonathan Thangadurai <315550690+JonathanThangadurai@users.noreply.github.com>` (set in repo config).
  No co-author lines, no tool attribution. **Never push to main.** Since D3: work on a feature branch, push the
  branch, open a PR with `gh pr create`, wait for green CI, report the link; merging is done by hand, not by the agent.
- Keys: read only from the environment; if unset, parse `~/.config/gotime/keys.env` (`gotime_data/keys.py`).
  ERA5: `cdsapi.Client(url=CDS_URL, key=COPERNICUS_KEY)`; CAMS: `cdsapi.Client(url=ADS_URL, key=COPERNICUS_KEY)`. Never print, log, echo, cat or
  commit a key; report presence only ("KNMI_API_KEY set: yes/no"). On 401/403, stop and report whether it is the key or a
  dataset licence that still needs accepting.
- Memory: never load a full global or European file into memory; crop to city boxes and our variables as early as possible.
- No names of people, roles or partner companies in code, docs, commit messages, PR titles/bodies or issues.
  Refer to components instead ("health engine", "data pipeline", "wind/route service", "partner", "partner app").
- Missing values are null plus `status = missing`, never 0. Data age counts from the forecast issue time.
- Store UTC; convert with the IANA zone per instant. Keep raw values; compute averages on read. Pollen per type, never summed.

## Stack

Python 3.12, uv, ruff, pytest. Downloads: own ECMWF byte-range fetcher (httpx; AWS first, ECMWF fallback), cdsapi, httpx. Grids: xarray + cfgrib/eccodes,
h5netcdf. Raw files ~2 days; processed Parquet queried with DuckDB (~10 days live + archive). One storage root
(`GOTIME_STORAGE_ROOT`, local now, `s3://` later via fsspec). API: FastAPI + Pydantic. Ops: one scheduler process and
Docker Compose (api, scheduler, monitor, Caddy).
Target server: Hetzner VPS, 4 GB RAM, 40 GB disk.

## Known facts (details and verification in docs/data-sources.md)

- Luchtmeetnet (fallback only): `timestamp_measured` = end of hour (verified 2026-11-17); requests are cut to ~7 days; 429 comes with no Retry-After.
- KNMI: the old daily endpoint is gone after 2026; use the Data Platform / EDR with personal keys (the anonymous key gets 429).
- EEA E2a: `Start`/`End` are UTC+1 all year with no time zone attached (verified against Luchtmeetnet, winter and summer). City name is "Greater Amsterdam".
- ECMWF open data: steps are 3-hourly (hourly rows are interpolated); `ssrd` is accumulated; 0.1° available since 2026-10-20.
  Decided: 0.1°, 00/06/12/18 UTC, 0–96 h. Published 00/12 at base + 9 h 34 min, 06/18 at + 8 h 27 min (0.25°: + 7 h 34).
  Own byte-range fetcher; AWS first, data.ecmwf.int fallback, every fallback logged. Serve newest complete cycle.
- Point: nearest land cell (lsm >= 0.5). Chennai: ECMWF 13.1, 80.2; CAMS global 13.2, 80.0 (land share from ECMWF lsm).
  CAMS global Chennai coastal cell 13.2, 80.4 also stored (`grid:cams_global:chennai:coastal`, read only on request).
  site_id on grid rows = `grid:<source>:<location>` (kept). KNMI: hourly collection; 10-minute data is a later option.
- OpenAQ: INTERNAL ONLY (never in partner output; never commit its data; fixtures synthetic). NO2 in ppb -> ug/m3
  with measured T/p else 25 C/1013.25 hPa, recorded in `method`. Duplicate sensors: keep non-empty / most hours.
- Public GitHub repo `JonathanThangadurai/Ferrywellco-Engine` (origin). Package stays `gotime_data`.
- CAMS global: NO2/O3 only at `model_level` 137 (separate request; silently dropped otherwise), kg/kg → µg/m³ via sp and t2m.
- KNMI EDR: time stamp = end of hour for hourly sums/means; `RH` is precipitation (humidity is `U`); T at 1.5 m; Q in J/cm².
- ERA5 NetCDF from the CDS comes as a zip of two files (instant, accum) even with `download_format: unarchived`.
- CDS/ADS: one request at a time per dataset; abandoned jobs stay queued and block new ones ("Maximum number queued requests").
- Canonical table has `site_id` plus a `sites` table (site_id, name, lat, lon, station_type, source).
- CAMS pollen is unvalidated/experimental according to CAMS.
- 2026 bias (Amsterdam, summer, vs background stations): CAMS NO2 ×1.8, PM2.5 ×1.35, varying by hour; model T vs
  Schiphol −1.4 °C at 10:00, +0.9 °C at 21:00. Bias correction (later) is per hour of day; store pairs; re-measure on ADS CAMS.
- Algorithm needs: 24-hour means (PM2.5, PM10, NO2), maximum 8-hour mean (O3), hourly values.
- Licences: all V1 sources are CC BY 4.0 (EEA wording to confirm). ADS/CDS dataset licences must be accepted on the website.
- `climatology/`: frozen KNMI daily TG 2006 to 2026-11-10, cannot be downloaded again. Read-only.
- Test days: 25-hour day 2026-10-25 and 23-hour day 2027-03-28 (Europe/Amsterdam).

## Stage status

- [x] Stage A: facts and design (skeleton, data-sources, pipeline design, open questions). Approved 2026-11-17.
- [x] Stage B: access check and size test. Approved 2026-11-17. Stages A+B pushed to origin/main on 2026-11-17.
- [x] Stage C: `gotime_data/` package (sources, store, schema, read, cli), tests, README. Reviewed 2026-11-18; pushed.
  `docs/data-interface.md` stays "draft, pending health-engine review" until confirmed.
- [x] Stage D1: API design (`docs/api-design.md`, `docs/engine-interface.md`, `docs/openapi.yaml`).
  Guidance science belongs to the health engine; the API only provides the plug-in point and a SandboxEngine.
- [x] Stage D2: `gotime_api/`, `gotime_engine/`, contract tests, load test. Approved and pushed to main 2026-11-19.
  Decisions: freshness ECMWF 16/30 h, CAMS EU 36/60 h, CAMS global 24/48 h (review after two weeks of logs);
  pollen_not_assessed (degraded only with hay_fever=yes); radius Amsterdam 15 km, Chennai 20 km; one mode per key.
- [x] Stage D3: Docker/compose/Caddy, `gotime_ops/` (scheduler, heartbeats, quality, monitor, backup, source
  checks), CI + nightly/weekly source checks, golden/property/e2e tests, docs. Merged (PR #1).
- GitHub Actions secrets: KNMI_API_KEY, KNMI_EDR_API_KEY, COPERNICUS_KEY, OPENAQ_API_KEY; variables: ADS_URL, CDS_URL.
  Heartbeats: HC_PING_INGEST/PRECOMPUTE/MONITOR (env; never log or commit).
