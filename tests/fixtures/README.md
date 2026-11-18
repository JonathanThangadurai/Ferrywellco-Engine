# Test fixtures

Small saved inputs so the tests run without network. All real data here is open data reused under
CC BY 4.0; it has been cropped and cut down (modified). Full attribution per source is in
`docs/data-sources.md`.

| folder | origin | licence / attribution |
|---|---|---|
| `ecmwf_ifs/2026111600/` | ECMWF IFS open data 0.1°, 16 Nov 2026 00z, steps 0–12 h, cropped to the city boxes; step 0 includes the land-sea mask (17 Nov 00z, static field) | CC BY 4.0, "Contains ECMWF open data" |
| `static/ecmwf_lsm_*.nc` | ECMWF IFS open data 0.1° land-sea mask, cropped | CC BY 4.0, ECMWF |
| `static/cams_global_lsm_chennai.nc` | CAMS global land-sea mask, Chennai box (cross-check) | CC BY 4.0, Copernicus Atmosphere Monitoring Service |
| `cams_eu/2026111600/` | CAMS European air quality forecast (ensemble), 16 Nov 2026 00z, 3 × 3 cells around Amsterdam | CC BY 4.0, "Generated using Copernicus Atmosphere Monitoring Service information" |
| `cams_global/2026111600/` | CAMS global composition forecast, 16 Nov 2026 00z, Chennai box | CC BY 4.0, Copernicus Atmosphere Monitoring Service |
| `eea_e2a/202611171000/` | EEA E2a, one NO2 sampling point in Greater Amsterdam, 14–17 Nov 2026 | CC BY 4.0, "Source: European Environment Agency (EEA)" |
| `knmi_hourly/202611170600/` | KNMI hourly observations, Schiphol, 16 Nov 2026 (EDR CoverageJSON) | CC BY 4.0, "Source: KNMI" |
| `openaq/202611171445/` | **SYNTHETIC.** Made-up values in the OpenAQ v3 format. Real OpenAQ data is never committed to this public repo. | n/a |
