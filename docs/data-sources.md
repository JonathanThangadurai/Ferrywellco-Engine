# Data sources (V1)

Checked live on 2026-11-17. Each item is marked:
- **[live]**: checked against the actual data or API response today.
- **[docs]**: taken from the provider's current documentation page.
- **[to verify B]**: could not be checked yet (needs a key or a download); Stage B checks it.

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
| EEA E2a / E1a | AQ observations, Amsterdam | REST + Parquet (`httpx`) | EEA reuse policy (CC BY 4.0) [to verify B] | none |
| KNMI hourly in-situ observations | weather observations, Amsterdam | KNMI EDR API (`httpx`) | CC BY 4.0 | KNMI EDR key |
| ERA5 single levels | climatology, both cities | `cdsapi` via CDS | CC BY 4.0 | CDS key + accept dataset licence |
| KNMI daily TG (frozen copy) | climatology, Amsterdam | file in `climatology/` | CC BY 4.0 (KNMI) | none |
| Luchtmeetnet | fallback only, AQ observations NL | REST (`httpx`) | open data [to verify B] | none |
| CPCB (India) | not used in V1 | no stable API found | n/a | n/a |

---

## 1. ECMWF IFS open data (weather forecast)

- **Provider / dataset**: ECMWF, IFS HRES "oper" stream, open data subset. [docs]
- **Access**: `ecmwf-opendata` client with `source="aws"`. Bucket `ecmwf-forecasts` (eu-central-1), path `YYYYMMDD/HHz/ifs/{0p25|0p1}/oper/`. One GRIB2 file per step plus a JSON-lines `.index` with the byte offset of each field, so we download only our fields with HTTP range requests. [live]
- **Licence**: CC BY 4.0 plus the ECMWF Terms of Use; commercial use allowed. [docs]
- **Attribution**: "Contains ECMWF open data, © ECMWF, licensed under CC BY 4.0." State the changes we make (cropping, interpolation, bias correction).
- **Resolution**: 0.25°, and 0.1° since 20 Oct 2026 (the 0.1° surface file is `...-oper-fc-sfc.grib2`). [docs, live: the 0p1 folder exists on AWS]
- **Runs / length**: 00, 06, 12, 18 UTC. 00/12 UTC: 0–144 h every 3 h, then 150–360 h every 6 h. 06/18 UTC: 0–144 h every 3 h. Only about the last 12 runs (2–3 days) are kept online. [docs]
- **Delay**: the 0.25° data comes at the end of the ECMWF dissemination schedule (roughly 7–9 h after base time [to verify B]). The 0.1° data comes about 2 h after the 0.25° data. [docs]
- **Variables**: `2t` (K), `2d` (K), `10u` and `10v` (m s⁻¹), `ssrd` (J m⁻², accumulated since base time). Relative humidity is derived from 2t and 2d. [live: all five fields are in the 0.1° index; global field size 1.4–3.7 MB each, about 12 MB per step for the five]
- **Time stamp**: `2t`, `2d`, `10u` and `10v` are instantaneous at valid time = base + step. `ssrd` is accumulated from base time to the step: mean W m⁻² = (ssrd[step] − ssrd[prev]) / (Δt in seconds), and the result is the mean over the interval that **ends** at the step.
- **Pitfalls**:
  - Steps are 3-hourly, not hourly. Hourly rows between steps are filled by interpolation and get `status = interpolated`. Each GRIB field is global, so a field must be cropped to the city box straight after download (never keep a global array).
  - Converting radiation (de-accumulating `ssrd`) needs the previous step's field, so we keep the previous step's cropped field rather than the global one.
  - Kelvin → °C.
- **Known facts handled**: CC BY confirmed. The model temperature bias against Schiphol (about −1.4 °C at 10:00, +0.9 °C at 21:00) was measured on an earlier model feed, so it must be re-measured: forecast–observation pairs are stored with `lead_h` and the valid hour so a per-hour-of-day correction can be fitted later.

## 2. CAMS European air quality forecast (Amsterdam, incl. pollen)

- **Provider / dataset**: Copernicus Atmosphere Monitoring Service (ECMWF). ADS dataset `cams-europe-air-quality-forecasts`, model `ensemble` (the median of 11 regional models). [docs]
- **Access**: `cdsapi.Client(url=ADS_URL, key=COPERNICUS_KEY)`; request `level: 0` (surface), `type: forecast`, `leadtime_hour` 0–96, `area` = the city box, format NetCDF. The ADS crops on the server, so we download only the box. [to verify B: request keys]
- **Licence**: CC BY 4.0 (the Copernicus licence was replaced by CC BY on the ADS/CDS on 2 July 2025). You must still accept the licence once on the dataset page while logged in, or the API returns an error. [docs]
- **Attribution**: "Generated using Copernicus Atmosphere Monitoring Service information [year]" plus the ensemble citation "METEO-FRANCE et al. (year)". The data is modified (cropped and corrected) and we say so.
- **Resolution**: 0.1° (about 10 km), Europe. [docs]
- **Runs / length**: one run a day at 00 UTC, hourly steps 0–96 h. [docs]
- **Delay**: steps 0–48 h are available at about 06:45 UTC; steps 49–96 h at about 08:30 UTC. Fetch at ~09:00 UTC. [docs]
- **Variables**: `particulate_matter_2.5um`, `particulate_matter_10um`, `nitrogen_dioxide`, `ozone`, and pollen `alder_pollen`, `birch_pollen`, `grass_pollen`, `mugwort_pollen`, `olive_pollen`, `ragweed_pollen`. Pollen is available at the surface only, all year since 21 Nov 2023. [docs]
- **Units**: µg m⁻³ in NetCDF, kg m⁻³ in GRIB (so we request NetCDF). Pollen in grains m⁻³. [docs]
- **Time stamp**: hourly values. The docs do not say whether a value is an instant or an hourly mean [to verify B: check the NetCDF attributes]. Until checked, it is treated as instantaneous at valid time.
- **Pitfalls**:
  - Variables other than NO, NO2, SO2, O3, PM2.5, PM10 and dust, **pollen included**, are "unvalidated and experimental" according to CAMS. Record this; do not present pollen numbers as precise.
  - The ensemble's lowest model layer height is not stored in the dataset.
- **Known facts handled**:
  - Pollen is stored as one variable per type (`pollen_grass_gm3`, etc.), never summed.
  - The earlier bias (NO2 about ×1.8, PM2.5 about ×1.35 against background stations, varying by hour of day) was measured on a different CAMS feed, so it is re-measured on this ADS feed with the stored pairs against EEA background stations.

## 3. CAMS global atmospheric composition forecast (Chennai)

- **Provider / dataset**: CAMS / ECMWF. ADS dataset `cams-global-atmospheric-composition-forecasts`. [docs]
- **Access**: same ADS client as above; single-level surface fields, `area` = the Chennai box, `leadtime_hour` 0–120. [to verify B]
- **Licence / attribution**: CC BY 4.0. "Generated using Copernicus Atmosphere Monitoring Service information [year]". DOI 10.24381/04a0b097. You must accept the licence on the dataset page. [docs]
- **Resolution**: 0.4° (about 44 km), global. A whole city is about one grid cell. [docs]
- **Runs / length**: 00 and 12 UTC, 5 days (120 h). Single-level fields every hour. [docs]
- **Delay**: [to verify B] (expected a few hours after base time).
- **Variables / units**: PM2.5 and PM10 at the surface in **kg m⁻³** (×1e9 → µg m⁻³). NO2 and O3 at the lowest model level in **kg kg⁻¹ (mass mixing ratio)**. To convert to µg m⁻³: multiply by air density ρ = p / (R_d · T), with p = surface pressure and T = 2 m temperature (both available in the same dataset), then by 1e9. [docs; exact variable names to verify B]
- **Time stamp**: instantaneous at valid time [to verify B].
- **Pitfalls**:
  - The model is upgraded about once a year, which changes the bias, so bias tables must record the model cycle.
  - It has no pollen.
  - At 0.4° a grid cell mixes the city with sea and countryside, so values are very coarse.
- **Known facts handled**: there are no observations for Chennai in V1 (see CPCB below), so this source cannot be bias-corrected yet. The data interface should mark Chennai air quality as uncorrected.

## 4. EEA air quality observations (E2a up-to-date, E1a validated)

- **Provider / dataset**: European Environment Agency, Air Quality Download Service. Dataset codes: `1` = E2a (unverified, up to date), `2` = E1a (verified, until the previous reporting year), `3` = historical Airbase. [live for code 1; docs for 2 and 3]
- **Access**: no key. `POST https://eeadmz1-downloads-api-appservice.azurewebsites.net/ParquetFile/urls` with `{"countries":["NL"],"cities":["Greater Amsterdam"],"pollutants":[<URI>],"dataset":1,"aggregationType":"hour"}` returns a CSV list of Parquet URLs, one per sampling point. The city name is **"Greater Amsterdam"**, not "Amsterdam" (with "Amsterdam" the list comes back empty). [live]
- **Pollutant URIs**: `http://dd.eionet.europa.eu/vocabulary/aq/pollutant/` + `6001` (PM2.5), `5` (PM10), `8` (NO2), `7` (O3). [live]
- **Licence**: EEA data is reused under CC BY 4.0 per the EEA data policy [to verify B: exact wording on the dataset page]. Attribution: "Source: European Environment Agency (EEA), Air Quality e-Reporting".
- **Resolution**: point stations; we keep stations inside the Amsterdam box and record each station's type (background / traffic) from the station metadata [to verify B: metadata endpoint].
- **Delay**: E2a is about 2–3 h behind real time (last value 22:00 UTC, file written 00:16 local on 17 Nov). [live] E1a is published once a year.
- **Columns**: `Samplingpoint, Pollutant, Start, End, Value, Unit, AggType, Validity, Verification, ResultTime, DataCapture, FkObservationLog`. `Value` is a **decimal string** and must be cast. `Unit` is `ug.m-3`. In today's file, Validity = 1 (valid) and Verification = 3 (not verified). [live]
- **Time stamp**: `Start`/`End` mark the hour interval, with **no time zone attached**. They are local standard time, **UTC+1 all year, with no summer time**: EEA 22:00 = Luchtmeetnet 21:00 UTC (November) and EEA 10:00 = Luchtmeetnet 09:00 UTC (July), same station NL49007, same values. So `valid_time_utc = Start − 1 h`. [live] Other countries may use a different offset, so the offset is a per-country setting.
- **Pitfalls**:
  - `/ParquetFile/urls` ignores the date range: each file is the **whole year to date** for one sampling point (~290 kB for NO2). Download, filter to the hours we need, and append.
  - Validity ≤ 0 means invalid → `status = missing`, value null.
  - The legacy CSV service is retired.
- **Known facts handled**: background stations are kept separate from traffic stations, so the bias can be fitted against background stations as before.

## 5. KNMI hourly in-situ observations (weather observations)

- **Provider / dataset**: KNMI Data Platform, collections `hourly-in-situ-meteorological-observations` (near real time, since 2015) and `...-validated` (since 1951). [docs]
- **Access**: EDR API `https://api.dataplatform.knmi.nl/edr/v1/collections/{collection}/locations/{station}` with `datetime=start/end` and `parameter-name=...`; header `Authorization: <KNMI_EDR_API_KEY>`. Station list: `.../locations`. Schiphol is WMO 06240. [docs; parameter names to verify B]
- **Limits**: registered users get 200 requests/s and 1000 requests/hour. The anonymous key gets 429 under normal use, so we use the personal keys only. [docs + known fact]
- **Licence / attribution**: CC BY 4.0. "Source: KNMI (Royal Netherlands Meteorological Institute)". [docs]
- **Variables**: temperature, dew point, relative humidity, wind speed and direction, global radiation [names and units to verify B].
- **Time stamp**: KNMI hourly values cover the hour **ending** at the stamp (the classic KNMI "HH" convention) [to verify B on the EDR output].
- **Pitfalls**: KNMI warns that station moves and instrument changes make the series inhomogeneous.
- **Known facts handled**: the old daily-data endpoint is gone after 2026, so nothing here uses it. Only personal keys are used.

## 6. ERA5 (climatology)

- **Provider / dataset**: ECMWF / Copernicus Climate Change Service, CDS dataset `reanalysis-era5-single-levels`. [docs]
- **Access**: `cdsapi.Client()` (reads `~/.cdsapirc`), `area` = the city box, our variables only, one month per request.
- **Licence / attribution**: CC BY 4.0. "Contains modified Copernicus Climate Change Service information [year]"; DOI 10.24381/cds.adbb2d47. Accept the licence on the dataset page. [docs]
- **Resolution / timing**: 0.25°, hourly, 1940 to now. Updated daily, about 5 days behind (preliminary "ERA5T"; final data 2–3 months later). [docs]
- **Time stamp**: instantaneous fields at the stamp. Accumulated fields (`ssrd`) cover the hour **ending** at the stamp.
- **Use**: climatology for both cities (Chennai has no other history). Stored with `kind = reanalysis`.

## 7. KNMI daily mean temperature (frozen copy) — `climatology/`

- **Origin**: copied unchanged on 2026-11-17 from the old local workspace (`~/Documents/TestingAQ/climatology/`, the folder next to the gotime-engine repo). It came from the old KNMI daily-data service, which is gone, so **this data cannot be downloaded again**. Treat it as read-only.
- **Content**: 12 files `knmi_<station>_TG.csv`, no header, rows `YYYY-MM-DD,TG` with TG = daily mean temperature in °C. Stations 240 (Schiphol), 260 (De Bilt), 215, 270, 275, 280, 290, 310, 344, 350, 370, 380. They run from 2006-01-01 to 2026-11-10 (station 215 from 2014-07-16; station 290 is missing 7 days).
- **Licence**: KNMI open data, CC BY 4.0. Attribution "Source: KNMI".

## 8. Luchtmeetnet (fallback only)

- **Access**: `GET https://api.luchtmeetnet.nl/open_api/measurements?station_number=&formula=&start=&end=`, no key. [live]
- **Time stamp**: `timestamp_measured` = **end of hour**. [live: the response now also has `timestamp_measured_start` and `timestamp_measured_end`, e.g. start 02:00, end 03:00, measured 03:00]
- **Pitfalls (known facts)**: requests are silently cut to about 7 days, so chunk them by ≤ 7 days. It returns 429 with no Retry-After header, so back off exponentially. It is not part of the V1 pipeline; it is the same network that feeds EEA for NL.

## 9. CPCB (India): checked, not used

- data.gov.in lists "Real time Air Quality Index from various locations" (hourly), but the page says the API for this resource does not exist and offers "Request API". No stable, documented CPCB API was found. Third-party relays exist (e.g. aqicn.org), but their licence and validation are unclear. **Decision needed later**; Chennai AQ stays forecast-only in V1.
