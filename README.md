# gotime-data

The GOTime data layer turns weather and air quality forecasts and observations into one hourly table per location,
which the wellness algorithm reads through a small interface. GOTime gives wellness guidance (when and how to be active
outside); it is not a health service.

Status: **Stage A (facts and design)**. There is no pipeline code yet.

## Docs

- `docs/data-sources.md`: every source, verified live: access, licence, units, time conventions, pitfalls.
- `docs/data-pipeline-design.md`: stages, canonical table, retention, size and memory estimates.
- `docs/open-questions.md`: questions for the algorithm side.
- `docs/architecture.drawio`: architecture diagram (added by Jon).

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
| `COPERNICUS_KEY` | ADS key (CAMS) |
| `ADS_URL` | ADS API URL |
| `GOTIME_STORAGE_ROOT` | storage root, local path or `s3://...` (default `./data`) |

ERA5 uses `~/.cdsapirc`. If the variables are not set, scripts read `~/.config/gotime/keys.env`.

## Licences to accept (once, logged in on the website)

- ADS: `cams-europe-air-quality-forecasts`, `cams-global-atmospheric-composition-forecasts`
- CDS: `reanalysis-era5-single-levels`

## Attribution

Contains ECMWF open data (CC BY 4.0). Generated using Copernicus Atmosphere Monitoring Service and Copernicus Climate
Change Service information (CC BY 4.0). Air quality observations: European Environment Agency. Weather observations:
KNMI (CC BY 4.0). All data has been modified (cropped, converted, interpolated). Details per source are in `docs/data-sources.md`.
