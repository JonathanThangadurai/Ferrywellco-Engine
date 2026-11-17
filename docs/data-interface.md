# Data interface (for the algorithm side)

> **Status: DRAFT, pending health-engine review.** Nothing here is final until the health-engine side confirms it. Questions that shape it are
> in `open-questions.md`.

This is how the algorithm reads data from the data layer. It is a Python function that returns a pandas DataFrame
in the canonical table format (`data-pipeline-design.md`). Behind it is Parquet read with DuckDB; you never need to
know the file layout.

## Reading hourly values

```python
from datetime import datetime, UTC
from gotime_data.read import get_hourly

df = get_hourly(
    "amsterdam",
    start_utc=datetime(2026, 11, 18, 0, tzinfo=UTC),
    end_utc=datetime(2026, 11, 20, 0, tzinfo=UTC),   # end is exclusive
    variables=["t2m_c", "pm25_ugm3", "no2_ugm3", "o3_ugm3"],
)
```

Arguments:

- `location_id`: `amsterdam` or `chennai` (`config/locations.yaml`).
- `start_utc`, `end_utc`: timezone-aware datetimes; naive times are refused so nobody mixes local time and UTC.
  `end_utc` is exclusive.
- `variables`: names from the list below; `None` means all.
- `kind` (optional): `forecast` (default), `observation`, `reanalysis`, or `None` for all.
- `latest_only` (default `True`): for forecasts, return only the newest complete cycle per source and variable. With
  `False` you get every cycle (needed for verification and bias pairs).

Returns one row per (valid hour, variable, source[, site]) with these columns:

| column | meaning |
|---|---|
| location_id | `amsterdam`, `chennai` |
| valid_time_utc | start of the hour the value is for (UTC, timezone-aware) |
| variable | name with unit, see below |
| value | float, or null when missing (never 0 as a stand-in) |
| source | `ecmwf_ifs`, `cams_eu`, `cams_global`, `eea_e2a`, `knmi_hourly`, `era5` |
| kind | `forecast`, `observation`, `reanalysis` |
| issue_time_utc | forecast base time (observations: time published) |
| lead_h | hours from issue to valid time (null for observations) |
| status | `ok`, `missing`, `interpolated` |
| site_id | station id for observations, null for grid values |

Data age = now minus `issue_time_utc`, never the download time.

## Variables

| variable | unit | Amsterdam source | Chennai source |
|---|---|---|---|
| `t2m_c` | °C, 2 m | ECMWF | ECMWF |
| `td2m_c` | °C dew point, 2 m | ECMWF | ECMWF |
| `rh2m_pct` | % (from T and dew point) | ECMWF | ECMWF |
| `wind10m_ms` | m/s, 10 m | ECMWF | ECMWF |
| `ssrd_wm2` | W/m², mean over the preceding interval | ECMWF | ECMWF |
| `pm25_ugm3`, `pm10_ugm3`, `no2_ugm3`, `o3_ugm3` | µg/m³ | CAMS Europe | CAMS global |
| `pollen_{grass,birch,alder,mugwort,olive,ragweed}_gm3` | grains/m³, per type, never summed | CAMS Europe | none |

Forecast pollen is experimental according to CAMS: treat it as an indication, not a precise number.
Observations (EEA, KNMI) are for verification and bias correction; whether the algorithm uses them is open.

## Averages (computed on read)

```python
from gotime_data.read import mean_24h, max_8h_mean

pm = mean_24h(df, "pm25_ugm3", min_hours=18)      # rolling 24 h mean per hour, with coverage count
o3 = max_8h_mean(df, "o3_ugm3", tz="Europe/Amsterdam", min_hours=6)  # per local day
```

- `mean_24h`: for each hour, the mean of that hour and the 23 before. Columns `value`, `n_hours`; `value` is null
  when `n_hours < min_hours`.
- `max_8h_mean`: rolling 8-hour means, then the maximum per **local** calendar day. Local days are 23, 24 or 25 hours
  long on daylight-saving days; this is handled.
- Coverage thresholds are placeholders until open question 9 (health engine) is answered.

## Local time

Everything is stored in UTC. Convert with the IANA zone per instant:
`df["valid_time_utc"].dt.tz_convert("Europe/Amsterdam")`. Never add a fixed offset.

## Point values

Grid values are taken from the nearest **land** grid cell to the city point (land-sea mask), so a coastal city such as
Chennai does not get a sea cell. The chosen cell per location and source is written to the `sites` table
(`site_id` like `grid:cams_global:13.20,80.00`), so you can see which cell was used.
