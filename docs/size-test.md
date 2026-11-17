# Size test (Stage B)

Measured 2026-11-17 on a MacBook (home connection), one forecast cycle or one day per source, cropped to the
city boxes in `config/locations.yaml`, our variables only. Script: `scripts/size_test.py`; raw numbers in
`data/size-test/results.jsonl` (not committed). Peak memory = peak resident memory of the Python process for that
source (includes ~120 MB for Python + xarray + pandas imports).

## Measured per source

| Source | What was fetched | Downloaded | Kept (cropped) | Time | Peak RAM |
|---|---|---|---|---|---|
| ECMWF IFS 0.1° | 16 Nov 00z, steps 0–96 h every 3 h (33 steps), 5 fields | 392 MB | 3.2 MB (both cities) | 95 s | 566 MB |
| ECMWF IFS 0.25° | same | 84 MB | 3.0 MB (both cities) | 31 s | 226 MB |
| CAMS Europe | 16 Nov 00z, 0–96 h hourly, 10 variables (4 pollutants + 6 pollen), Amsterdam box | 0.47 MB | 0.47 MB | 116 s (mostly queue) | 129 MB |
| CAMS global, surface | 16 Nov 00z, 0–120 h hourly, PM2.5, PM10, surface pressure, 2 m T, Chennai box | 0.07 MB | 0.07 MB | 36 s (queue) | 129 MB |
| CAMS global, level 137 | same run, NO2 and O3 at the lowest model level | 0.06 MB | 0.06 MB | 40 s (queue) | 129 MB |
| EEA E2a | Greater Amsterdam, PM2.5/PM10/NO2/O3, 26 sampling-point files (each = year to date) | 7.3 MB | 7.3 MB | 5.5 s | 123 MB |
| KNMI hourly (EDR) | Schiphol, 1 day, all 23 parameters | 0.02 MB | 0.02 MB | 0.2 s | 123 MB |
| ERA5 | see "ERA5" below | | | | |

Notes on the measurements:

- **ECMWF**: only our 5 fields are downloaded (HTTP byte ranges from the `.index` file), but each field is global, so
  most of the download is thrown away after cropping. Peak RAM at 0.1° comes from decoding the global fields of one
  step (3600 × 1801 points × 5 fields). One step is processed at a time, so it does not grow with the number of steps.
  The cropped grid is 10 × 12 cells (Amsterdam) / 10 × 11 (Chennai) at 0.1°, and 4 × 5 at 0.25°.
- **ECMWF publish times** (Last-Modified on data.ecmwf.int): 0.25° at base + 7 h 34 min, 0.1° at base + **9 h 34 min**
  (00z run ready ~09:34 UTC, 12z ~21:34 UTC). With 0.1°, the newest forecast is always ≥ 9.5 h old.
- **ECMWF on AWS**: the AWS mirror answered `503 SlowDown` to every request from this machine for several minutes,
  and the `ecmwf-opendata` client retried silently, so it looked like a hang. The measurement used `data.ecmwf.int`
  directly. The downloader now has a bounded retry, and the mirror is a setting (`aws` default, `ecmwf` fallback).
- **CAMS times** are mostly ADS queue time, not transfer: the files are under 0.5 MB because the ADS crops on its side.
- **CAMS global grid**: at 0.4°, the 1° Chennai box gives only 3 × 3 cells (lat 12.8–13.6, lon 80.0–80.8). The
  nearest cell to the city point (13.08, 80.27) is 13.2, 80.4, which is partly sea. Point method to revisit in stage C.
- **EEA**: files are whole-year-to-date per sampling point, so they grow through the year (~8.3 MB per full fetch by
  December). Only the newest copy needs to be kept; 588 rows were for the last full day.

## ERA5

Not measured yet. The access check proved the key and licence work (the job was **accepted**, no 401/403), but on
2026-11-17 the jobs stayed queued for over an hour during announced CDS/ADS maintenance (08:00–17:00 CET, data older
than 4–5 days, i.e. all of ERA5). The size-test request was then **rejected** with "Maximum number queued requests
for this dataset has been reached", because the earlier check jobs were still in the queue. To redo after the queue
clears: `uv run python scripts/size_test.py era5`. Expected size (0.25°, 5 variables, 5 × 5 cells, 24 h): well under
0.1 MB per city per day, so a 20-year climatology per city is under 1 GB; it is a one-off download.

## Projection for the decided setup

Setup: ECMWF 0.1°, 00 + 12 UTC, 0–96 h; CAMS Europe daily 00z; CAMS global 00 + 12 UTC; EEA hourly; KNMI hourly.

| Item | Per day | 2 days raw | 10 days processed | 1 year archive |
|---|---|---|---|---|
| ECMWF 0.1° cropped NetCDF | 6.3 MB | 13 MB | | |
| CAMS Europe + global | 0.7 MB | 1.4 MB | | |
| EEA (newest copy only) | ~8 MB | ~8 MB | | |
| KNMI | 0.02 MB | 0.04 MB | | |
| Canonical table (Parquet, zstd) | ~5 100 rows, ~0.02–0.05 MB | | ~0.5 MB | ~3.5–15 MB |
| **Total on disk** | | **~23 MB** | **~0.5 MB** | **< 20 MB** |
| Download traffic | ~0.8 GB (ECMWF) + ~0.2 GB (EEA hourly refetch) | | | ~370 GB/year |

Canonical table size was measured, not guessed: a synthetic year with the measured row counts per source
(1.85 million rows, 11 columns incl. `site_id`) is **3.4 MB** as zstd Parquet with random values (real values
compress better). A DuckDB query for 2 days of one location on that file takes 0.03 s. Even with one Parquet file per
source per day (file overhead), a year stays well below 100 MB.

## Fit on the server (Hetzner, 4 GB RAM, 40 GB disk)

- **Disk**: under 0.1 GB for everything above, against 40 GB. Even 10 cities and 5 years stay under 5 GB.
- **RAM**: the largest step is ECMWF 0.1° at ~570 MB peak. Run sources one after another (not in parallel) and peak
  stays under 1 GB, leaving room for the API.
- **Traffic**: ~1 GB/day is mostly ECMWF fields we throw away. Hetzner VPS plans include 20 TB/month, so this is fine.
  0.25° would cut it to ~0.2 GB/day.
- **Time**: one full ingest round is under 5 minutes of work plus the ADS/CDS queues.

## Recommendation

**Local disk only for now.** All live data, 2 days of raw files and a year of archive fit in well under 1 GB on
the 40 GB disk, so object storage would add cost and moving parts without solving a problem. Keep
`GOTIME_STORAGE_ROOT` and fsspec paths so moving to `s3://` later is a setting change. Revisit when (a) we add many
cities, (b) a second server or service needs the same archive, or (c) we want backups off the VPS (a nightly copy of
the archive folder to a Hetzner Storage Box or S3 bucket would cover backups without changing the pipeline).
