# Data interface (for the algorithm side)

> **Status: DRAFT, pending health-engine review.** Nothing here is final until the health-engine side confirms it. Questions that shape it are
> in `open-questions.md`.

This is how the algorithm reads data from the data layer: Python functions that return a pandas DataFrame in the
canonical table format (`data-pipeline-design.md`). Behind them is Parquet read with DuckDB; you never need to know
the file layout. Code: `gotime_data/read.py`.

## Reading hourly values

```python
from datetime import datetime, UTC
from gotime_data.read import get_hourly

df = get_hourly(
    "amsterdam",
    start_utc=datetime(2026, 11, 18, 0, tzinfo=UTC),
    end_utc=datetime(2026, 11, 20, 0, tzinfo=UTC),  # end is exclusive
    variables=["t2m_c", "pm25_ugm3", "no2_ugm3", "o3_ugm3"],
)
```

Arguments:

- `location_id`: `amsterdam` or `chennai` (`config/locations.yaml`).
- `start_utc`, `end_utc`: timezone-aware datetimes; naive times raise an error so nobody mixes local time and
  UTC. `end_utc` is exclusive.
- `variables`: names from the list below; `None` means all.
- `kind`: `forecast` (default), `observation`, `reanalysis`, or `None` for all.
- `latest_only` (default `True`): for forecasts, only the newest **complete** cycle per source and variable
  (complete = every hour present). For observations, the newest version of each hour. With `False` you get every
  cycle (for verification and bias pairs). Note: the newest cycle starts at its issue time, so for a window in the
  past use `latest_only=False` and pick the cycle you want.
- `include_internal` (default `False`): internal-only sources (OpenAQ) are left out. Never set it for anything
  partners see.
- `include_alternatives` (default `False`): alternative grid cells are left out. Today there is one: the coastal
  CAMS global cell for Chennai (`site_id = grid:cams_global:chennai:coastal`), stored next to the default land
  cell so you can compare them (open question 18).

Returns one row per (valid hour, variable, source[, site]) with these columns:

| column | meaning |
|---|---|
| location_id | `amsterdam`, `chennai` |
| valid_time_utc | start of the hour the value is for (instant values: that instant), UTC, timezone-aware |
| variable | name with unit, see below |
| value | float, or null when missing (never 0 as a stand-in) |
| source | `ecmwf_ifs`, `cams_eu`, `cams_global`, `eea_e2a`, `knmi_hourly`, `era5` (`openaq` internal only) |
| kind | `forecast`, `observation`, `reanalysis` |
| issue_time_utc | forecast base time; observations: when the value could first be known |
| lead_h | hours from issue to valid time (null for observations) |
| status | `ok`, `missing`, `interpolated` |
| site_id | station id for observations (`eea:...`, `knmi:06240`); for grid values the chosen cell, `grid:<source>:<location>` |
| method | how a value was made, where that matters (e.g. `KNMI 1.5 m`, `ppb->ug/m3, 25 C, 1013.25 hPa`, `3-hour mean spread over its hours`); mostly null |

Data age = now minus `issue_time_utc`, never the download time. At 06:00 local the newest weather forecast is
about 11 h old in Amsterdam and 12.5 h in Chennai (`data-sources.md`, ECMWF).

## Variables

| variable | unit | Amsterdam source | Chennai source |
|---|---|---|---|
| `t2m_c` | °C, 2 m (KNMI stations 1.5 m) | ECMWF | ECMWF |
| `td2m_c` | °C dew point, 2 m | ECMWF | ECMWF |
| `rh2m_pct` | % (forecast: from T and dew point) | ECMWF | ECMWF |
| `wind10m_ms` | m/s, 10 m | ECMWF | ECMWF |
| `ssrd_wm2` | W/m², mean over the interval; forecast hours carry the 3-hour mean (`interpolated`) | ECMWF | ECMWF |
| `pm25_ugm3`, `pm10_ugm3`, `no2_ugm3`, `o3_ugm3` | µg/m³ | CAMS Europe | CAMS global |
| `pollen_{alder,birch,grass,mugwort,olive,ragweed}_gm3` | grains/m³, per type, never summed | CAMS Europe | none |
| `precip_mm` | mm in the hour (observations only) | KNMI | none |

- ECMWF steps are 3-hourly; the hours in between are linear interpolations with `status = interpolated`.
- Forecast pollen is experimental according to CAMS: treat it as an indication, not a precise number.
- Observations (EEA, KNMI) are for verification and bias correction; whether the algorithm uses them is open.
- Chennai observation hours (internal) are Indian local hours and start at :30 UTC.

## Averages (computed on read)

```python
from gotime_data.read import mean_24h, max_8h_mean

pm = mean_24h(df, "pm25_ugm3", min_hours=18)  # rolling 24 h mean per hour, with coverage count
o3 = max_8h_mean(df, "o3_ugm3", tz="Europe/Amsterdam", min_hours=6, min_windows=18)  # per local day
```

- `mean_24h`: for each hour, the mean of that hour and the 23 before. Columns `valid_time_utc`, `value`,
  `n_hours`; `value` is null when `n_hours < min_hours`.
- `max_8h_mean`: 8-hour rolling means (each needs `min_hours` hours), then the maximum per **local** calendar day
  (needs `min_windows` valid windows). Columns `local_date`, `value`, `n_windows`, `n_hours`. Local days are 23,
  24 or 25 hours long on daylight-saving days; this is handled and tested.
- Averages are computed per source and site; missing hours are skipped and counted.
- Coverage thresholds are placeholders until open question 9 (health engine) is answered.

## Local time

Everything is stored in UTC. Convert with the IANA zone per instant:
`df["valid_time_utc"].dt.tz_convert("Europe/Amsterdam")`. Never add a fixed offset.

## Point values and sites

Grid values come from the nearest **land** grid cell to the city point (land-sea mask ≥ 0.5), so a coastal city
such as Chennai does not get a sea cell. The chosen cell per source and location is in the sites table:

```python
from gotime_data.store import read_sites

read_sites()  # site_id, name (method), lat, lon, station_type, source
```

Grid cells have `site_id = grid:<source>:<location>` and `station_type = grid`; stations have their own ids.
Alternative cells add a suffix (`grid:cams_global:chennai:coastal`).
