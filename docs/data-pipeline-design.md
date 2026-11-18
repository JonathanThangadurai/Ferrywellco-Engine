# Data pipeline design (V1)

Goal: one hourly table per location that the health algorithm (right side of `docs/architecture/gotime-data-architecture-v1.drawio`)
reads through one interface. It should stay simple enough that anyone on the team can explain every step.

## Stages

```
1 raw download  ->  2 crop  ->  3 point values per location  ->  4 stored hourly table
   (per source)      (city box,     (nearest grid cell,              (Parquet, canonical schema,
                      our vars)      unit conversion, UTC)             read with DuckDB)
```

1. **Raw download**: one module per source. Only our variables are requested. Where the provider can crop on the
   server (ADS/CDS `area`), we send the city box. ECMWF fields are global, so we fetch only our fields by byte range
   from the `.index` file and go straight to stage 2.
2. **Crop**: cut each grid to the city box from `config/locations.yaml` as soon as it is opened, one field at a time.
   Only the cropped NetCDF is written to `raw/`; global arrays are never kept.
3. **Point values**: take the nearest grid cell to the city point (this method is written into the `source` metadata,
   see the table below). Convert units (K→°C, kg m⁻³→µg m⁻³, mixing ratio→µg m⁻³, accumulated J m⁻²→mean W m⁻²), and
   set the time to UTC, start of the hour. Observations are kept per station.
4. **Stored hourly table**: append to Parquet, partitioned by `source/date`. Raw values are never overwritten. A
   new forecast cycle adds rows with a new `issue_time_utc`. Corrections (later) go in a separate column or table,
   never over the raw value.

## Canonical table

| column | type | meaning |
|---|---|---|
| location_id | str | `amsterdam`, `chennai` |
| valid_time_utc | timestamp (UTC) | start of the hour the value is for (instant values: the instant) |
| variable | str | name with the unit in it: `t2m_c`, `td2m_c`, `rh2m_pct`, `wind10m_ms`, `winddir10m_deg`, `ssrd_wm2`, `pm25_ugm3`, `pm10_ugm3`, `no2_ugm3`, `o3_ugm3`, `pollen_<type>_gm3` (grass, birch, alder, mugwort, olive, ragweed) |
| value | float, nullable | null when missing, never 0 as a stand-in |
| source | str | `ecmwf_ifs`, `cams_eu`, `cams_global`, `eea_e2a`, `eea_e1a`, `knmi_hourly`, `era5` |
| kind | str | `forecast`, `observation`, `reanalysis` |
| issue_time_utc | timestamp (UTC) | forecast base time; for observations, the time the provider published the value (EEA `ResultTime`) |
| lead_h | int, nullable | valid minus issue in hours; null for observations |
| status | str | `ok`, `missing`, `interpolated` |
| site_id | str | station id for observations (e.g. `eea:NL/SPO-NL00002_00008_101`, `knmi:06240`); for grid values the chosen cell `grid:<source>:<location>` (stage C: links each value to its cell in the sites table) |
| method | str, nullable | how a value was made where that matters (unit conversion with standard or measured T/p, KNMI 1.5 m, 3-hour mean spread) (added in stage C) |

**Sites table** (`sites`, one row per station or grid point, written when a source first sees it):

| column | type | meaning |
|---|---|---|
| site_id | str | same id as in the hourly table |
| name | str | station name from the provider |
| lat, lon | float | station position |
| station_type | str | `background`, `traffic`, `industrial`, `weather`, or null when unknown |
| source | str | provider source name (`eea_e2a`, `knmi_hourly`, ...) |

Bias fitting selects pairs through this table (e.g. only `background` stations in the Amsterdam box).

## Time handling

- Everything is stored in UTC. Local time is computed on read with `zoneinfo` using the IANA zone **per instant**, so
  the 25-hour day (2026-10-25, Europe/Amsterdam) and the 23-hour day (2027-03-28) come out right. A "local day" is
  therefore 23, 24 or 25 rows long, never a fixed 24.
- Each source's convention (start of hour, end of hour, or instant) is in `data-sources.md`; the converter for that
  source is the only place that shifts times.

## Missing data

- A missing hour gets a row with `value = null` and `status = missing`, so gaps can be seen instead of being silently absent.
- Gaps between 3-hourly ECMWF steps are filled by linear interpolation in time with `status = interpolated`. No other
  source is interpolated in V1.
- Averages (24-hour, maximum 8-hour) are computed on read. The result carries a coverage count (e.g. 18 of 24 hours); the
  threshold for "valid" is an open question for the health engine (see `open-questions.md`).
- Data age is counted from `issue_time_utc`, not from the download time.

## Retention and formats

| layer | format | why | kept |
|---|---|---|---|
| raw | cropped GRIB/NetCDF as downloaded | so stage 3 can be re-run after a bug fix | 2 days |
| live | Parquet, partitioned by source/date | columnar, small, DuckDB queries it directly without a database server | 10 days |
| archive | the same Parquet, moved to `archive/` | forecast and observation pairs for bias fitting and verification | kept (≥ 1 year) |

One setting, `GOTIME_STORAGE_ROOT`, holds the storage root (default `./data`). All paths go through `fsspec`, so changing
the setting to `s3://bucket/gotime` needs no code change.

## Expected size and memory on the server (4 GB RAM / 40 GB disk), to be measured in Stage B

- **Rows**: about 10 000 per day (ECMWF 4 cycles × ~145 h × 6 variables × 2 cities ≈ 7 000; CAMS EU ≈ 1 000; CAMS global
  ≈ 1 000; EEA ≈ 1 000; KNMI ≈ 150). As compressed Parquet that is ~0.3 MB/day, so **about 100 MB per year**.
- **Raw (cropped)**: well under 50 MB per day, so ≤ 100 MB for 2 days.
- **Download volume**: ECMWF 0.1° fields are global (~12 MB per step for our 5 fields). Up to ~3 GB/day of transfer if
  we take all 4 cycles to the end, 0.4 GB/day at 0.25°. That is traffic, not disk.
- **RAM**: one global 0.1° field is 3600 × 1801 values (26 MB as float32, 52 MB as float64). Processing one field at a
  time keeps the peak well under 500 MB. ADS/CDS downloads are already cropped and tiny.
- Disk conclusion (to confirm with measurements): local disk is enough for V1. Object storage only becomes
  useful when there are more cities or the archive is shared with other services.

## ECMWF choice (decided after stages A and B)

0.1°, runs 00, 06, 12 and 18 UTC, steps 0–96 h (3-hourly). AWS mirror first, `data.ecmwf.int` as fallback (logged).
Each cycle's issue and publish time are recorded in `meta/cycles/`; the newest complete cycle is served.

## Point selection (decided after stage B)

Nearest land cell (land-sea mask ≥ 0.5) for ECMWF, CAMS global (land share from the ECMWF 0.1° mask) and ERA5;
plain nearest cell for CAMS Europe over Amsterdam. Chosen cells are in the sites table; details in `data-sources.md`.

## Storage layout (stage C)

```
<GOTIME_STORAGE_ROOT>/raw/<source>/<batch>/        cropped downloads + manifest.json (2 days; local files)
<GOTIME_STORAGE_ROOT>/static/ecmwf_lsm_<loc>.nc     cropped land-sea mask
<GOTIME_STORAGE_ROOT>/live/hourly/source=<s>/date=<d>/part.parquet      10 days
<GOTIME_STORAGE_ROOT>/archive/hourly/source=<s>/date=<d>/part.parquet   kept
<GOTIME_STORAGE_ROOT>/meta/sites/<source>.parquet, meta/cycles/<source>/<batch>.parquet
```

`date` is the issue date for forecasts and the valid date for observations. Writing merges into the partition file
and drops only exact duplicates; a revised observation has a new issue time and is kept next to the old one.
OpenAQ (`openaq`) is an internal-only source: stored, but left out of the read interface by default.

## Not in V1

DWD ICON, street-level localisation, fitting the bias correction (we only store the pairs), CPCB, the API (FastAPI), and
scheduling (cron in Docker).
