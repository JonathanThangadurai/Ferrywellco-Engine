# Open questions: health engine and partner integration

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
