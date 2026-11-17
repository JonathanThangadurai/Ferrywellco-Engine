# Ferrywellco Engine: GOTime data layer

This repository is `Ferrywellco-Engine` on GitHub; the Python package inside it is `gotime_data`.

The GOTime data layer turns weather and air quality forecasts and observations into one hourly table per location,
which the wellness algorithm reads through a small interface. GOTime gives wellness guidance (when and how to be active
outside); it is not a health service.

Status: **Stage B (access check and size test) done**. The pipeline package comes in stage C.

## Scripts

```
uv run python scripts/check_access.py          # one small request per source: OK / FAILED and why
uv run python scripts/size_test.py ecmwf --resol 0p1 --mirror ecmwf
uv run python scripts/size_test.py cams_eu|cams_global|era5|eea|knmi
```

## Docs

- `docs/data-sources.md`: every source, verified live: access, licence, units, time conventions, pitfalls.
- `docs/data-pipeline-design.md`: stages, canonical table, retention, size and memory estimates.
- `docs/open-questions.md`: questions for the algorithm side.
- `docs/size-test.md`: measured sizes, times and memory; storage recommendation.
- `docs/architecture/gotime-data-architecture-v1.drawio`: architecture diagram (open with [diagrams.net](https://app.diagrams.net/)).
- `docs/data-interface.md`: how the algorithm reads data (draft, pending Tilman's review).

## Setup

```
uv sync
uv run pytest
uv run ruff check .
```

## Environment variables (names only; never commit values)

| name | used for |
|---|---|
| `KNMI_API_KEY` | KNMI Open Data API |
| `KNMI_EDR_API_KEY` | KNMI EDR API (hourly observations) |
| `COPERNICUS_KEY` | ADS and CDS key (CAMS, ERA5) |
| `ADS_URL` | ADS API URL (CAMS) |
| `CDS_URL` | CDS API URL (ERA5) |
| `OPENAQ_API_KEY` | optional, OpenAQ (Chennai observations, not used yet) |
| `GOTIME_STORAGE_ROOT` | storage root, local path or `s3://...` (default `./data`) |

ERA5 and CAMS both use `COPERNICUS_KEY`; `~/.cdsapirc` holds the same CDS key for other tools. If the variables are not set, scripts read `~/.config/gotime/keys.env`.

## Licences to accept (once, logged in on the website)

- ADS: `cams-europe-air-quality-forecasts`, `cams-global-atmospheric-composition-forecasts`
- CDS: `reanalysis-era5-single-levels`

## Attribution

Contains ECMWF open data (CC BY 4.0). Generated using Copernicus Atmosphere Monitoring Service and Copernicus Climate
Change Service information (CC BY 4.0). Air quality observations: European Environment Agency. Weather observations:
KNMI (CC BY 4.0). All data has been modified (cropped, converted, interpolated). Details per source are in `docs/data-sources.md`.
