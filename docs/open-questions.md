# Open questions

Grouped by component: **health engine** (guidance algorithm, sections 1 to 7) and **partner integration** (partner API
fields, section 8).

## Open questions: health engine

The data layer stores hourly values in UTC in one table (see `data-pipeline-design.md`). Please answer inline
so we can fix the interface (`docs/data-interface.md`, written in Stage C).

## 1. Inputs

1. Which variables do you need? Proposed: `t2m_c`, `td2m_c`, `rh2m_pct`, `wind10m_ms`, `ssrd_wm2`, `pm25_ugm3`,
   `pm10_ugm3`, `no2_ugm3`, `o3_ugm3`, `pollen_{grass,birch,alder,mugwort,olive,ragweed}_gm3`. Anything missing
   (e.g. UV index, apparent temperature, precipitation, cloud cover)?
2. Should derived values (relative humidity from dew point, wind speed from u/v, apparent temperature) be computed on our side or yours?
3. Do you need forecasts only, or also the latest observations (e.g. "measured PM2.5 in the last hour")?
4. Chennai has no pollen and no air quality observations in V1. How should the algorithm behave when a variable is absent?

## 2. Time resolution and horizon

5. Is hourly resolution enough, or do you need finer (e.g. 10-minute observations)?
6. How far ahead: 24 h, 48 h, 4 days? CAMS Europe ends at 96 h; ECMWF goes further.
7. Do you work in local time (day = local midnight to midnight) or in rolling windows? Local days are 23 or 25 hours
   long on daylight-saving change days.

## 3. Averaging

8. Which averages do you need? We plan to provide helpers for the 24-hour mean (PM2.5, PM10, NO2) and the maximum
   8-hour mean (O3), computed on read. Rolling (each hour looks back 24 h) or per calendar day?
9. Minimum coverage for an average to count (e.g. 18 of 24 hours, 6 of 8)? Below that we return null and a status.
10. Should averages mix observed past hours with forecast future hours, or use one kind only?

## 4. Missing data and quality

11. When a value is missing (`value = null`, `status = missing`), should you skip that hour, fall back to another source, or show nothing?
12. Do you want the `interpolated` hours (ECMWF 3-hourly steps filled to hourly) marked differently in your output?
13. Do you want an uncertainty or "data age" field per value (hours since forecast issue)?

## 5. How outputs come back

14. What does your algorithm return per location and hour: a score, a category, text keys? Do we store it in the same
    storage (another table) so the API reads one place?
15. Do you call our read interface as a Python function (`get_hourly(...)` returning a DataFrame), or do you prefer a
    Parquet/DuckDB query you run yourself?
16. Wording: user-facing outputs must be wellness guidance and never say "safe", "clean" or "healthy". Agreed list of terms?

## 6. Added after stage B (data side findings that need your view)

17. **CAMS Europe: instant or hourly mean?** CAMS Europe gives one value per hour, but neither the file nor the
    documentation says whether it is the value at that instant or the mean over the hour. We store it at
    run time + lead hour. For 24-hour means it hardly matters; for "the hour from 14:00 to 15:00" it shifts the
    value by up to half an hour. Is that acceptable, or should we ask CAMS user support?
18. **Chennai land cell.** Chennai is on the coast. The grid cell nearest to the city point is mostly sea
    (ECMWF 0.1°: 37 % land; CAMS global 0.4°: about 30 % land). We now use the nearest *land* cell: for ECMWF
    8 km west of the centre (fine), but for CAMS global 32 km west (13.2, 80.0), i.e. inland rather than the city.
    Options: (a) keep the nearest land cell, (b) a weighted mix of the coastal and inland cells, (c) the nearest
    cell regardless of sea. Which fits the algorithm best? Observations (OpenAQ, internal) can tell us later
    which one verifies better.
    **Decided for now (Jon, after stage C):** the land cell (13.2, 80.0) is the default
    (`site_id = grid:cams_global:chennai`). The coastal cell (13.2, 80.4) is stored too
    (`site_id = grid:cams_global:chennai:coastal`); `get_hourly(..., include_alternatives=True)` returns both so
    you can compare them and choose.
19. **ECMWF 0.1° versus 0.25°.** 0.1° shows more local detail (coast, city) but is published about 2 h later
    (00/12 UTC runs: 9 h 34 min after the run start, versus 7 h 34 min for 0.25°). We chose 0.1° with all four
    runs (00/06/12/18 UTC). Do you prefer resolution or the 2 h fresher 0.25° forecast?
20. **Forecast age in the morning.** Even with the 06/18 UTC runs, the newest weather forecast at 06:00 local
    time is about **11 h old in Amsterdam** (10 h in summer) and **12.5 h old in Chennai**. Observations arrive
    much faster: EEA about 2–3 h and OpenAQ about 1.5–2.5 h after the hour. KNMI's hourly collection lags
    much more (at 13:31 UTC on 18 Nov the newest hour was 00:00 UTC); KNMI's 10-minute data may be fresher (to
    check). Idea (not built):
    adjust the first forecast hours towards the latest observation (e.g. the current forecast error fading out
    over the next 6–12 hours). Would that be useful for the morning guidance, and which variables matter most?

## 7. Engine interface (stage D, `engine-interface.md`) — health engine

21. **Two steps.** `assess` runs once per forecast cycle per location, local date and profile; `choose` picks
    the window per request from the stored assessment (< 5 ms). Does that split work for you, and what should
    an assessment hold per hour?
22. **Profiles.** Which options change the result? Today all 36 combinations of activity type (4),
    heat_adapted (3) and hay_fever (3) are precomputed.
23. **Codes.** Your list of intensity-hint codes (placeholders: `as_planned`, `go_easier`, `light_only`) and
    reason codes. Once agreed, codes are stable for partners.
24. **Inputs.** Do you need observations as input, or the 24 h before the date (the newest forecast cycle only
    starts at its issue time)?
25. **Half-hour time zones.** In Chennai (UTC+5:30) a local day touches 25 UTC hours, the first and last half
    outside the day. Acceptable, or should values be interpolated to local half hours?

## Open questions: partner integration (partner API fields, `openapi.yaml`)

## 8. Request and response fields (stage D)

- **A1.** **Activity types:** `walk`, `run`, `cycle`, `outdoor_workout`. Which do you need?
- **A2.** **hay_fever** and **heat_adapted** as `yes` / `no` / `unknown`. Does that match what your app knows?
- **A3.** **No window:** when nothing fits, the answer is 200 with `window.status: none` (not an error). OK?
- **A4.** **Dates and times:** today to today + 3; start times cannot cross midnight; the end of the range can be
  cut back to where the forecast ends. OK?
- **A5.** **Time format:** local times with offset (e.g. `2026-11-20T06:00:00+05:30`); forecast issue times in UTC.
- **A6.** **Attribution:** data sources must be shown in a visible place (e.g. a data-sources screen). Where will
  you show them?
