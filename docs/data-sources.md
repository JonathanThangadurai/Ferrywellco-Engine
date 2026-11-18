# Data sources (V1)

Checked live on 2026-11-17 (Stage A) and with real downloads in Stage B the same day. Each item is marked:
- **[live]**: checked against the actual data or API response today.
- **[docs]**: taken from the provider's current documentation page.
- **[B]**: verified in Stage B with keys and a real download (see `size-test.md`).
- **[open]**: still not verified.

Rules for every source:
- Store times in UTC. Each source has a **time stamp** row below that says whether its time marks the start of the hour, the end of the hour or a single instant. Every source's converter sets `valid_time_utc` to the **start of the hour** it covers. Instantaneous model values are stored at the instant they are valid for.
- Missing values are stored as `value = null` with `status = missing`, never as 0.
- Data age = now minus `issue_time_utc` (the forecast base time), not the download time.

Summary:

| Source | Use | Access | Licence | Key needed |
|---|---|---|---|---|
| ECMWF IFS open data | weather forecast, both cities | `ecmwf-opendata`, AWS mirror | CC BY 4.0 | none |
| CAMS European AQ forecast | AQ + pollen forecast, Amsterdam | `cdsapi` via ADS | CC BY 4.0 | ADS key + accept dataset licence |
| CAMS global composition forecast | AQ forecast, Chennai | `cdsapi` via ADS | CC BY 4.0 | ADS key + accept dataset licence |
| EEA E2a / E1a | AQ observations, Amsterdam | REST + Parquet (`httpx`) | EEA reuse policy (CC BY 4.0) [open] | none |
| KNMI hourly in-situ observations | weather observations, Amsterdam | KNMI EDR API (`httpx`) | CC BY 4.0 | KNMI EDR key |
| ERA5 single levels | climatology, both cities | `cdsapi` via CDS | CC BY 4.0 | CDS key + accept dataset licence |
| KNMI daily TG (frozen copy) | climatology, Amsterdam | file in `climatology/` | CC BY 4.0 (KNMI) | none |
| Luchtmeetnet | fallback only, AQ observations NL | REST (`httpx`) | open data [open] | none |
| CPCB (India) | not used in V1 | no stable API found | n/a | n/a |
| OpenAQ (CPCB/TNPCB) | Chennai observations, internal only | REST v3 (`httpx`) | provider terms + OpenAQ terms | `OPENAQ_API_KEY` |

---

## 1. ECMWF IFS open data (weather forecast)

- **Provider / dataset**: ECMWF, IFS HRES "oper" stream, open data subset. [docs]
- **Access**: one GRIB2 file per step plus a JSON-lines `.index` with the byte offset of each field, so we download only our fields with HTTP range requests. Mirrors: AWS bucket `ecmwf-forecasts` (eu-central-1) and `data.ecmwf.int/forecasts`, same paths `YYYYMMDD/HHz/ifs/{0p25|0p1}/oper/`. 0.1° file names end in `-oper-fc-sfc.grib2`. [live]
- **Download method** [B, C]: our own small range fetcher (httpx), not the `ecmwf-opendata` client (removed from the dependencies). Reason: on 2026-11-17 AWS answered `503 SlowDown` to every request for several minutes, and the client retried silently, so it looked like a hang. Source order (decided after stage B): **AWS first, `data.ecmwf.int` as fallback**. Each request retries 429/503 six times (1 + 2 + … + 32 s); if AWS still fails, the fetcher logs a warning and uses ECMWF for the rest of that cycle. Every fallback is logged and written to the raw batch's `manifest.json` (`mirror_fallbacks`).
- **Licence**: CC BY 4.0 plus the ECMWF Terms of Use; commercial use allowed. [docs]
- **Attribution**: "Contains ECMWF open data, © ECMWF, licensed under CC BY 4.0." State the changes we make (cropping, interpolation, bias correction).
- **Resolution**: 0.25°, and 0.1° since 20 Oct 2026 (the 0.1° surface file is `...-oper-fc-sfc.grib2`). [docs, live: the 0p1 folder exists on AWS]
- **Runs / length**: 00, 06, 12, 18 UTC. 00/12 UTC: 0–144 h every 3 h, then 150–360 h every 6 h. 06/18 UTC: 0–144 h every 3 h. Only about the last 12 runs (2–3 days) are kept online. [docs]
- **Runs used** (decided after stage B): 0.1°, **00, 06, 12 and 18 UTC**, steps 0–96 h every 3 h. The 06/18 UTC runs exist at 0.1° in open data as stream `oper` (not `scda`), with steps 0–144 h every 3 h [live, 17 Nov 2026]. The land-sea mask `lsm` is in the step-0 file and is fetched once per cycle.
- **Delay** [live, Last-Modified on data.ecmwf.int, 16–17 Nov 2026; all steps of a run carry the same time]:

  | run | 0.1° published | 0.25° published |
  |---|---|---|
  | 00 UTC | base + 9 h 34 min (09:34 UTC) | base + 7 h 34 min |
  | 06 UTC | base + 8 h 27 min (14:27 UTC) | |
  | 12 UTC | base + 9 h 34 min (21:34 UTC) | base + 7 h 34 min |
  | 18 UTC | base + 8 h 27 min (02:27 UTC next day) | |

- **Forecast age at 06:00 local time** (age = now − issue time of the newest published cycle):

  | location | 06:00 local = | newest cycle then | age | without 06/18 UTC runs |
  |---|---|---|---|---|
  | Amsterdam, winter (CET) | 05:00 UTC | 18 UTC of the day before (out 02:27) | **11 h** | 17 h (12 UTC) |
  | Amsterdam, summer (CEST) | 04:00 UTC | 18 UTC of the day before | **10 h** | 16 h |
  | Chennai (IST) | 00:30 UTC | 12 UTC of the day before (out 21:34) | **12.5 h** | 12.5 h |

- **Serving**: every ingested cycle is recorded in `meta/cycles/` with issue time, publish time (Last-Modified), fetch time and whether it is complete (all steps present) per location and variable. The read interface serves the newest *complete* cycle per source and variable.
- **Variables**: `2t` (K), `2d` (K), `10u` and `10v` (m s⁻¹), `ssrd` (J m⁻², accumulated since base time). Relative humidity is derived from 2t and 2d. [live: all five fields are in the 0.1° index; global field size 1.4–3.7 MB each, about 12 MB per step for the five]
- **Time stamp**: `2t`, `2d`, `10u` and `10v` are instantaneous at valid time = base + step. `ssrd` is accumulated from base time to the step: mean W m⁻² = (ssrd[step] − ssrd[prev]) / (Δt in seconds), and the result is the mean over the interval that **ends** at the step.
- **Pitfalls**:
  - Steps are 3-hourly, not hourly. Hourly rows between steps are filled by interpolation and get `status = interpolated`. Each GRIB field is global, so a field must be cropped to the city box straight after download (never keep a global array).
  - Converting radiation (de-accumulating `ssrd`) needs the previous step's field, so we keep the previous step's cropped field rather than the global one.
  - Kelvin → °C.
- **Point** [C]: nearest land cell (lsm ≥ 0.5) to the city point, see "Point selection" below.
- **Known facts handled**: CC BY confirmed. The model temperature bias against Schiphol (about −1.4 °C at 10:00, +0.9 °C at 21:00) was measured on an earlier model feed, so it must be re-measured: forecast–observation pairs are stored with `lead_h` and the valid hour so a per-hour-of-day correction can be fitted later.

## Point selection (decided after stage B)

The value for a city is taken from one grid cell: the **nearest land cell** (land-sea mask ≥ 0.5) to the city
point in `config/locations.yaml`. A coastal city otherwise gets a cell that is mostly sea (different temperature,
humidity, wind and pollution). The chosen cell and the method are written to the `sites` table
(`site_id = grid:<source>:<location>`), so it can always be checked. Code: `gotime_data/grid.py`; tests:
`tests/test_grid.py`.

| source | location | land-sea mask | nearest cell (land share) | chosen cell (land share) | distance to city point |
|---|---|---|---|---|---|
| ECMWF 0.1° | Amsterdam (52.37, 4.90) | ECMWF `lsm` | 52.4, 4.9 (0.83) | same | 3 km |
| ECMWF 0.1° | Chennai (13.08, 80.27) | ECMWF `lsm` | 13.1, 80.3 (**0.37**, mostly sea) | **13.1, 80.2** (0.98) | 8 km |
| CAMS global 0.4° | Chennai | land share = mean of the ECMWF 0.1° `lsm` inside each 0.4° cell | 13.2, 80.4 (**0.31**) | **13.2, 80.0** (0.99) | 32 km |
| CAMS Europe 0.1° | Amsterdam | none needed (plain nearest cell) | 52.35, 4.95 | same | 4 km |
| ERA5 0.25° | both | ERA5 `lsm`, requested with the data | | | |

- **CAMS global cross-check** [live, 18 Nov 2026]: the ADS also serves `land_sea_mask` for this dataset. CAMS's own
  mask gives 0.27 for 13.2, 80.4 and 0.94 for 13.2, 80.0, so it picks the same cell as the ECMWF land share
  (test `test_chennai_cams_own_land_sea_mask_picks_the_same_cell`). We keep the ECMWF-derived share so no extra
  ADS request is needed per run.
- **Chennai CAMS caveat**: at 0.4° the nearest land cell is 32 km west of the centre, so it represents the
  inland west of the city, not the coast. This is a question for Tilman (`open-questions.md`).

## 2. CAMS European air quality forecast (Amsterdam, incl. pollen)

- **Provider / dataset**: Copernicus Atmosphere Monitoring Service (ECMWF). ADS dataset `cams-europe-air-quality-forecasts`, model `ensemble` (the median of 11 regional models). [docs]
- **Access** [B]: `cdsapi.Client(url=ADS_URL, key=COPERNICUS_KEY)`; request `model: ensemble`, `level: 0`, `type: forecast`, `time: 00:00`, `leadtime_hour` 0–96, `area` [N, W, S, E], `data_format: netcdf`. The ADS crops on its side: one full run for the Amsterdam box is 0.47 MB (10 × 12 cells, 97 steps). Most of the ~2 min is queue time.
- **File layout** [B]: variables `pm2p5_conc`, `pm10_conc`, `no2_conc`, `o3_conc`, pollen `apg_conc` (alder), `bpg_conc` (birch), `gpg_conc` (grass), `mpg_conc` (mugwort), `opg_conc` (olive), `rwpg_conc` (ragweed). `time` is a float in **hours since the run start** (`units: hours`, `long_name: FORECAST time from 20261116`), not a datetime, so valid time = run date 00 UTC + time. Grid cell centres are at x.x5°.
- **Licence**: CC BY 4.0 (the Copernicus licence was replaced by CC BY on the ADS/CDS on 2 July 2025). You must still accept the licence once on the dataset page while logged in, or the API returns an error. [docs]
- **Attribution**: "Generated using Copernicus Atmosphere Monitoring Service information [year]" plus the ensemble citation "METEO-FRANCE et al. (year)". The data is modified (cropped and corrected) and we say so.
- **Resolution**: 0.1° (about 10 km), Europe. [docs]
- **Runs / length**: one run a day at 00 UTC, hourly steps 0–96 h. [docs]
- **Delay**: steps 0–48 h are available at about 06:45 UTC; steps 49–96 h at about 08:30 UTC. Fetch at ~09:00 UTC. [docs]
- **Variables**: `particulate_matter_2.5um`, `particulate_matter_10um`, `nitrogen_dioxide`, `ozone`, and pollen `alder_pollen`, `birch_pollen`, `grass_pollen`, `mugwort_pollen`, `olive_pollen`, `ragweed_pollen`. Pollen is available at the surface only, all year since 21 Nov 2023. [docs]
- **Units**: µg m⁻³ in NetCDF, kg m⁻³ in GRIB (so we request NetCDF). Pollen in grains m⁻³. [docs]
- **Time stamp**: the NetCDF attribute only says `value: hourly values`; neither the docs nor the file say instant vs hourly mean [open]. Treated as instantaneous at valid time; the forecast–observation pairs will show if a half-hour shift fits better.
- **Pitfalls**:
  - **Pollen is unvalidated / experimental according to CAMS.** The CAMS documentation says only NO, NO2, SO2, O3, PM2.5, PM10 and dust are regularly validated against observations; all other variables, **including all six pollen types**, "are unvalidated and should be considered experimental". The data interface carries this as a note on every pollen variable, and user-facing text must not present pollen numbers as precise.
  - The ensemble's lowest model layer height is not stored in the dataset.
- **Known facts handled**:
  - Pollen is stored as one variable per type (`pollen_grass_gm3`, etc.), never summed.
  - The earlier bias (NO2 about ×1.8, PM2.5 about ×1.35 against background stations, varying by hour of day) was measured on a different CAMS feed, so it is re-measured on this ADS feed with the stored pairs against EEA background stations.

## 3. CAMS global atmospheric composition forecast (Chennai)

- **Provider / dataset**: CAMS / ECMWF. ADS dataset `cams-global-atmospheric-composition-forecasts`. [docs]
- **Access** [B]: same ADS client, **two requests per run**:
  1. surface (single-level): `particulate_matter_2.5um`, `particulate_matter_10um`, `surface_pressure`, `2m_temperature`.
  2. `nitrogen_dioxide`, `ozone` with `model_level: ["137"]` (the lowest model level, about 10 m above ground).
  **Pitfall:** NO2 and O3 exist only on model levels. Asked together with the surface fields they are **silently dropped** (no error, the file just lacks them).
- **File layout** [B]: variables `pm2p5`, `pm10` (kg m**-3), `sp` (Pa), `t2m` (K), `no2`, `go3` (kg kg**-1). Coordinates `forecast_reference_time`, `forecast_period` (timedelta) and `valid_time` (datetime). All 121 hourly steps are present for the level-137 gases too (the docs suggest 3-hourly for multi-level fields; this was not the case).
- **Size** [B]: 0.07 + 0.06 MB per run for the Chennai box; ~40 s each, mostly queue.
- **Grid** [B]: the 1° box gives only 3 × 3 cells (lat 12.8/13.2/13.6, lon 80.0/80.4/80.8). The nearest cell to the city point (13.08, 80.27) is 13.2 N 80.4 E, partly over sea. Sample from 16 Nov 00z at that cell: PM2.5 40–53, NO2 3–10, O3 120–150 µg m⁻³ (plausible).
- **Licence / attribution**: CC BY 4.0. "Generated using Copernicus Atmosphere Monitoring Service information [year]". DOI 10.24381/04a0b097. You must accept the licence on the dataset page. [docs]
- **Resolution**: 0.4° (about 44 km), global. A whole city is about one grid cell. [docs]
- **Runs / length**: 00 and 12 UTC, 5 days (120 h). Single-level fields every hour. [docs]
- **Delay**: [open]. The 16 Nov 00z run was available on 17 Nov; same-day timing to be measured when scheduling. Until then the pipeline asks for a run only 12 h after its base time (assumption, `cams_global.READY_AFTER`).
- **Point** [C]: nearest land cell, land share from the ECMWF 0.1° land-sea mask: 13.2, 80.0 for Chennai (see "Point selection").
- **Variables / units**: PM2.5 and PM10 at the surface in **kg m⁻³** (×1e9 → µg m⁻³). NO2 and O3 at the lowest model level in **kg kg⁻¹ (mass mixing ratio)**. To convert to µg m⁻³: multiply by air density ρ = p / (R_d · T), with p = surface pressure and T = 2 m temperature (both available in the same dataset), then by 1e9. [docs, conversion tested in `tests/test_convert.py`]
- **Time stamp**: instantaneous at `valid_time` (GRIB convention for these fields).
- **Pitfalls**:
  - The model is upgraded about once a year, which changes the bias, so bias tables must record the model cycle.
  - It has no pollen.
  - At 0.4° a grid cell mixes the city with sea and countryside, so values are very coarse.
- **Known facts handled**: there are no observations for Chennai in V1 (see CPCB below), so this source cannot be bias-corrected yet. The data interface should mark Chennai air quality as uncorrected.

## 4. EEA air quality observations (E2a up-to-date, E1a validated)

- **Provider / dataset**: European Environment Agency, Air Quality Download Service. Dataset codes: `1` = E2a (unverified, up to date), `2` = E1a (verified, until the previous reporting year), `3` = historical Airbase. [live for code 1; docs for 2 and 3]
- **Access**: no key. `POST https://eeadmz1-downloads-api-appservice.azurewebsites.net/ParquetFile/urls` with `{"countries":["NL"],"cities":["Greater Amsterdam"],"pollutants":[<URI>],"dataset":1,"aggregationType":"hour"}` returns a CSV list of Parquet URLs, one per sampling point. The city name is **"Greater Amsterdam"**, not "Amsterdam" (with "Amsterdam" the list comes back empty). [live]
- **Pollutant URIs**: `http://dd.eionet.europa.eu/vocabulary/aq/pollutant/` + `6001` (PM2.5), `5` (PM10), `8` (NO2), `7` (O3). [live]
- **Licence**: EEA data is reused under CC BY 4.0 per the EEA data policy [open: exact wording on the dataset page]. Attribution: "Source: European Environment Agency (EEA), Air Quality e-Reporting".
- **Resolution**: point stations; we keep stations inside the Amsterdam box and record each station's type (background / traffic) from the station metadata [open: metadata endpoint, needed for the sites table in stage C].
- **Delay**: E2a is about 2–3 h behind real time (last value 22:00 UTC, file written 00:16 local on 17 Nov). [live] E1a is published once a year.
- **Coverage** [B]: Greater Amsterdam has 26 sampling-point files for our 4 pollutants (11 for NO2). One fetch of all 26 is 7.3 MB in 5.5 s; 588 rows for one full day.
- **Columns**: `Samplingpoint, Pollutant, Start, End, Value, Unit, AggType, Validity, Verification, ResultTime, DataCapture, FkObservationLog`. `Value` is a **decimal string** and must be cast. `Unit` is `ug.m-3`. In today's file, Validity = 1 (valid) and Verification = 3 (not verified). [live]
- **Time stamp**: `Start`/`End` mark the hour interval, with **no time zone attached**. They are local standard time, **UTC+1 all year, with no summer time**: EEA 22:00 = Luchtmeetnet 21:00 UTC (November) and EEA 10:00 = Luchtmeetnet 09:00 UTC (July), same station NL49007, same values. So `valid_time_utc = Start − 1 h`. [live] Other countries may use a different offset, so the offset is a per-country setting.
- **Pitfalls**:
  - `/ParquetFile/urls` ignores the date range: each file is the **whole year to date** for one sampling point (~290 kB for NO2). Download, filter to the hours we need, and append.
  - Validity ≤ 0 means invalid → `status = missing`, value null.
  - The legacy CSV service is retired.
- **Known facts handled**: background stations are kept separate from traffic stations, so the bias can be fitted against background stations as before.

## 5. KNMI hourly in-situ observations (weather observations)

- **Provider / dataset**: KNMI Data Platform, collections `hourly-in-situ-meteorological-observations` (near real time, since 2015) and `...-validated` (since 1951). [docs]
- **Access**: EDR API `https://api.dataplatform.knmi.nl/edr/v1/collections/{collection}/locations/{station}` with `datetime=start/end` (optionally `parameter-name=...`); header `Authorization: <KNMI_EDR_API_KEY>`. Station list: `.../locations`. Schiphol location id is `0-20000-0-06240`. The response is CoverageJSON (`CoverageCollection` → `coverages[0].ranges[<param>].values`). One day for one station is 17 kB and takes 0.2 s. [live]
- **Delay** [live, C]: the hourly EDR collection is **not** near real time: fetched at 13:31 UTC on 18 Nov 2026, the newest stamp was 00:00 UTC (≈ 13.5 h behind; looks like a once-a-day update). For fresh observations the 10-minute collection (Open Data API, `10-minute-in-situ-meteorological-observations`) would be needed [open].
- **Limits**: registered users get 200 requests/s and 1000 requests/hour. The anonymous key gets 429 under normal use, so we use the personal keys only. [docs + known fact]
- **Licence / attribution**: CC BY 4.0. "Source: KNMI (Royal Netherlands Meteorological Institute)". [docs]
- **Variables** [live, from the parameter metadata]: `T` air temperature °C (past 1 minute, **at 1.5 m**, not 2 m), `TD` dew point °C (1.5 m), `U` relative humidity %, `FH` hourly mean wind speed m/s (10 m), `FF` past-10-minute wind speed m/s, `DD` wind direction ° (0 = calm, 990 = variable), `Q` hourly global radiation **J/cm²** (W m⁻² = Q × 10 000 / 3600).
- **Name trap**: `RH` is **hourly precipitation in mm**, not relative humidity. Humidity is `U`.
- **Time stamp**: stamps are whole UTC hours (`2026-11-16T00:00:00Z` … `23:00:00Z`). Hourly sums and means (`Q`, `FH`) cover the hour **ending** at the stamp. Checked live on 16 Nov: `Q` is 5 J/cm² at the 16:00 stamp and 0 at 17:00, and sunset at Schiphol was about 15:40 UTC, so the 16:00 value is for 15:00–16:00. `T`, `TD`, `U` are instantaneous (the last minute before the stamp). [live]
- **Pitfalls**:
  - KNMI warns that station moves and instrument changes make the series inhomogeneous.
  - Temperature is measured at 1.5 m and the models give 2 m. The difference is small and becomes part of the fitted bias.
- **Known facts handled**: the old daily-data endpoint is gone after 2026, so nothing here uses it. Only personal keys are used.

## 6. ERA5 (climatology)

- **Provider / dataset**: ECMWF / Copernicus Climate Change Service, CDS dataset `reanalysis-era5-single-levels`. [docs]
- **Access**: `cdsapi.Client(url=CDS_URL, key=COPERNICUS_KEY)` (same key as the ADS; `~/.cdsapirc` holds the same), `area` = the city box, our variables only, one month per request.
- **Status** [B]: the request is **accepted** (so key and licence work), but on 2026-11-17 the jobs stayed queued for over an hour. The CDS/ADS announced maintenance for that day (08:00–17:00 CET) affecting data more than 4–5 days old, which is all of ERA5. Size and time to be measured again after the maintenance. A later size-test request was **rejected**: "Maximum number queued requests for this dataset has been reached" (the earlier check jobs were still queued). Rule for the pipeline: one ERA5 request at a time, never start a new one while one is queued.
- **Licence / attribution**: CC BY 4.0. "Contains modified Copernicus Climate Change Service information [year]"; DOI 10.24381/cds.adbb2d47. Accept the licence on the dataset page. [docs]
- **Resolution / timing**: 0.25°, hourly, 1940 to now. Updated daily, about 5 days behind (preliminary "ERA5T"; final data 2–3 months later). [docs]
- **Time stamp**: instantaneous fields at the stamp. Accumulated fields (`ssrd`) cover the hour **ending** at the stamp.
- **Use**: climatology for both cities (Chennai has no other history). Stored with `kind = reanalysis`.

## 7. KNMI daily mean temperature (frozen copy) — `climatology/`

- **Origin**: copied unchanged on 2026-11-17 from the old local workspace (`~/Documents/TestingAQ/climatology/`, the folder next to the gotime-engine repo). It came from the old KNMI daily-data service, which is gone, so **this data cannot be downloaded again**. Treat it as read-only.
- **Content**: 12 files `knmi_<station>_TG.csv`, no header, rows `YYYY-MM-DD,TG` with TG = daily mean temperature in °C. Stations 240 (Schiphol), 260 (De Bilt), 215, 270, 275, 280, 290, 310, 344, 350, 370, 380. They run from 2006-01-01 to 2026-11-10 (station 215 from 2014-07-16; station 290 is missing 7 days).
- **Licence**: KNMI open data, CC BY 4.0. Attribution "Source: KNMI".

## 8. Luchtmeetnet (fallback only)

- **Access**: `GET https://api.luchtmeetnet.nl/open_api/measurements?station_number=&formula=&start=&end=`, no key. [live] **Always pass `start` and `end`**: without them the API answers `302` to `iq.luchtmeetnet.nl/...` with a default window of exactly 7 days (this is where the "cut to ~7 days" comes from). [B]
- **Time stamp**: `timestamp_measured` = **end of hour**. [live: the response now also has `timestamp_measured_start` and `timestamp_measured_end`, e.g. start 02:00, end 03:00, measured 03:00]
- **Pitfalls (known facts)**: requests are silently cut to about 7 days, so chunk them by ≤ 7 days. It returns 429 with no Retry-After header, so back off exponentially. It is not part of the V1 pipeline; it is the same network that feeds EEA for NL.

## 9. CPCB (India): checked, not used

- data.gov.in lists "Real time Air Quality Index from various locations" (hourly), but the page says the API for this resource does not exist and offers "Request API". No stable, documented CPCB API was found. Third-party relays exist (e.g. aqicn.org), but their licence and validation are unclear. **Decision needed later**; Chennai AQ stays forecast-only in V1.

## 10. OpenAQ (Chennai observations): INTERNAL USE ONLY

- **What**: aggregator that republishes CPCB/TNPCB station data, API v3 `https://api.openaq.org/v3/...`.
- **Use** (decided after stage B): internal verification and later bias correction for Chennai only. OpenAQ data
  never appears in output for partners: `get_hourly` leaves source `openaq` out unless `include_internal=True`.
  OpenAQ data is **never committed** to this public repo; the test fixture is synthetic and labelled as such.
- **Access** [live]: free account key in header `X-API-Key` (`OPENAQ_API_KEY`); without it 401. Limits: 60
  requests/min, 2 000/hour (headers `x-ratelimit-*` confirm 60/min). `/v3/locations?coordinates=13.08,80.27&radius=25000`
  then `/v3/sensors/{id}/hours`.
- **Licence / terms** [docs]: OpenAQ asks for attribution to OpenAQ **and** to the original provider, and users
  must follow each provider's terms. The hosted API may not be used to build services that compete with OpenAQ;
  higher limits need a paid agreement. The `licenses` field is empty for the CPCB/TNPCB locations (AirNow:
  "US Public Domain"). CPCB data upstream: India's Open Government Data licence [docs, to confirm for this feed].
  Attribution if ever shown internally: "Data: CPCB / TNPCB via OpenAQ".
- **Coverage** [live, 17 Nov 2026, last 7 days, 25 km around the centre]: 11 locations listed; 4 are dead (last
  data 2016–2018: Alandur, IIT, US post). Active with PM2.5, PM10, NO2 and O3: Velachery (CPCB), Manali (CPCB),
  Manali Village, Royapuram, Kodungaiyur, Arumbakkam (no O3), Perungudi (TNPCB). Hours with data: **90–99 %**
  per sensor (Velachery PM 92 %, Royapuram PM 90 %, the rest 95–99 %). AirNow "Chennai" (PM2.5 only): 60 %.
- **Delay** [live]: the newest complete hour ended about **2.3 h** before the query (raw 15-min values about
  1.3–1.8 h). Hours are local Indian hours, so periods start at **:30 UTC** (IST = UTC+5:30); stored as
  `valid_time_utc` = period start.
- **Units and conversion**: PM and O3 come in µg/m³; **NO2 in ppb**. ppb → µg/m³ = ppb × M × p / (R × T) / 1000,
  with the station's measured temperature and pressure for that hour where available, else 25 °C / 1013.25 hPa
  (1 ppb NO2 = 1.88 µg/m³, O3 = 1.96 µg/m³). The `method` column records which (e.g. `ppb->ug/m3, measured T,
  1013.25 hPa`). The stations report temperature but no pressure, so pressure is always the standard value.
- **Duplicates**: stations list some pollutants twice (one sensor empty, one with values, sometimes in
  different units). We keep the non-empty sensor; if both have values, the one with more hours, and log the other.

## Test fixtures (public repo)

`tests/fixtures/` holds small cut-down copies of real ECMWF, CAMS (Europe, global, land-sea mask), EEA and KNMI
data. All are CC BY 4.0 and may be redistributed with attribution; `tests/fixtures/README.md` lists the origin and
attribution per folder (EEA: exact licence wording still to confirm, see section 4). The OpenAQ fixture is
**synthetic** (made-up values in the OpenAQ format); real OpenAQ data is never committed.

