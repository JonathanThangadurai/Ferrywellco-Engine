# CLAUDE.md: gotime-data

GOTime turns weather and air quality forecasts into wellness guidance for partner apps (B2B API). This repo is the
**data layer** (left side of `docs/architecture.drawio`): data in, storage, bias correction, verification, data
interface. The health engine reads data through the interface defined here.

## Rules (always)

- V1 is as simple as possible. Every part must be explainable to the team. No clever abstractions; small files;
  docstrings say what a function does and why.
- Wellness, not health: never write "safe", "clean" or "healthy" in anything user-facing.
- Started from scratch. Never read, copy or imitate code from the old `gotime-engine` repo. Only `climatology/` (data) was copied.
- Python only. Work in stages and **STOP for review after each stage**.
- Git: commit as `Jonathan Thangadurai <315550690+JonathanThangadurai@users.noreply.github.com>` (set in repo config).
  No co-author lines, no tool attribution. **Never push without explicit approval; check the GitHub remote first.**
- Keys: read only from the environment; if unset, parse `~/.config/gotime/keys.env` (`gotime_data/keys.py`).
  ERA5: `cdsapi.Client(url=CDS_URL, key=COPERNICUS_KEY)`; CAMS: `cdsapi.Client(url=ADS_URL, key=COPERNICUS_KEY)`. Never print, log, echo, cat or
  commit a key; report presence only ("KNMI_API_KEY set: yes/no"). On 401/403, stop and report whether it is the key or a
  dataset licence that still needs accepting.
- Memory: never load a full global or European file into memory; crop to city boxes and our variables as early as possible.
- Missing values are null plus `status = missing`, never 0. Data age counts from the forecast issue time.
- Store UTC; convert with the IANA zone per instant. Keep raw values; compute averages on read. Pollen per type, never summed.

## Stack

Python 3.12, uv, ruff, pytest. Downloads: ecmwf-opendata (AWS mirror), cdsapi, httpx. Grids: xarray + cfgrib/eccodes,
h5netcdf. Raw files ~2 days; processed Parquet queried with DuckDB (~10 days live + archive). One storage root
(`GOTIME_STORAGE_ROOT`, local now, `s3://` later via fsspec). Later: cron in Docker, FastAPI + Pydantic.
Target server: Hetzner VPS, 4 GB RAM, 40 GB disk.

## Known facts (details and verification in docs/data-sources.md)

- Luchtmeetnet (fallback only): `timestamp_measured` = end of hour (verified 2026-11-17); requests are cut to ~7 days; 429 comes with no Retry-After.
- KNMI: the old daily endpoint is gone after 2026; use the Data Platform / EDR with personal keys (the anonymous key gets 429).
- EEA E2a: `Start`/`End` are UTC+1 all year with no time zone attached (verified against Luchtmeetnet, winter and summer). City name is "Greater Amsterdam".
- ECMWF open data: steps are 3-hourly (hourly rows are interpolated); `ssrd` is accumulated; 0.1° available since 2026-10-20.
  Decided: 0.1°, 00 + 12 UTC, 0–96 h. Published at base + 9 h 34 min (0.25°: + 7 h 34 min). Own byte-range fetcher
  (AWS throttled with 503 SlowDown; ecmwf-opendata retried silently). Mirror is a setting (aws / ecmwf).
- CAMS global: NO2/O3 only at `model_level` 137 (separate request; silently dropped otherwise), kg/kg → µg/m³ via sp and t2m.
- KNMI EDR: time stamp = end of hour for hourly sums/means; `RH` is precipitation (humidity is `U`); T at 1.5 m; Q in J/cm².
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
- [x] Stage B: access check (`scripts/check_access.py`) and size test (`docs/size-test.md`). Awaiting review.
  Open: ERA5 size (CDS queue), OpenAQ needs a key.
- [ ] Stage C: `gotime_data/` package, read interface, tests, README.
