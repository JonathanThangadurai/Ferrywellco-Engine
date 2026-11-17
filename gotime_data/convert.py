"""Small conversions shared by the sources. Pure functions, so they can be tested without network."""

from datetime import timedelta

import numpy as np
import pandas as pd

R_DRY_AIR = 287.05  # J kg-1 K-1, specific gas constant for dry air

# EEA E2a/E1a Start/End have no time zone. For NL they are UTC+1 all year (no summer time),
# checked against Luchtmeetnet on 2026-11-17 for a winter and a summer day. Per country, so it can grow.
EEA_UTC_OFFSET_H = {"NL": 1}


def kelvin_to_celsius(k):
    """K -> degrees C."""
    return k - 273.15


def eea_to_utc(start_local_naive: pd.Series, country: str) -> pd.Series:
    """Turn EEA naive Start times (fixed offset, no DST) into UTC-aware start-of-hour times."""
    offset = timedelta(hours=EEA_UTC_OFFSET_H[country])
    return (pd.to_datetime(start_local_naive) - offset).dt.tz_localize("UTC")


def deaccumulate(accum: pd.Series) -> pd.Series:
    """Turn a field accumulated since forecast start (e.g. ECMWF ssrd, J m-2) into a mean rate (W m-2).

    Input: values indexed by valid time (UTC), sorted, accumulation since base time.
    Output: mean rate over the interval that ENDS at each index time; the first value
    (step 0, nothing accumulated yet) is NaN. Small negatives from GRIB packing are clipped to 0.
    """
    accum = accum.sort_index()
    seconds = accum.index.to_series().diff().dt.total_seconds()
    rate = accum.diff() / seconds
    return rate.clip(lower=0)


def fill_to_hourly(values: pd.Series) -> pd.DataFrame:
    """Fill a 3-hourly (or coarser) series to hourly by linear interpolation in time.

    Returns columns value and status: 'ok' at the original steps, 'interpolated' in between,
    'missing' where an original value was missing (NaN stays NaN; we never invent across a gap).
    """
    values = values.sort_index()
    hourly_index = pd.date_range(values.index[0], values.index[-1], freq="h")
    filled = values.dropna().reindex(hourly_index.union(values.dropna().index))
    filled = filled.interpolate(method="time", limit_area="inside").reindex(hourly_index)

    # Do not interpolate across a missing original step: an hour is only filled when the
    # original steps just before and just after it both have a value.
    steps = values.index
    prev_pos = steps.searchsorted(hourly_index, side="right") - 1
    next_pos = steps.searchsorted(hourly_index, side="left")
    prev_ok = values.iloc[prev_pos].notna().to_numpy()
    next_ok = values.iloc[np.minimum(next_pos, len(steps) - 1)].notna().to_numpy()
    filled[~(prev_ok & next_ok)] = np.nan

    status = pd.Series("interpolated", index=hourly_index)
    status[hourly_index.isin(values.index)] = "ok"
    status[filled.isna()] = "missing"
    return pd.DataFrame({"value": filled, "status": status})


def mmr_to_ugm3(mmr_kgkg, surface_pressure_pa, t2m_k):
    """Mass mixing ratio (kg/kg) -> mass concentration (ug/m3).

    concentration = mmr * air density, with air density rho = p / (R_d * T) from the ideal gas law.
    Used for CAMS global NO2 and O3, which come as kg/kg.
    """
    rho = surface_pressure_pa / (R_DRY_AIR * t2m_k)  # kg m-3
    return mmr_kgkg * rho * 1e9  # kg m-3 -> ug m-3


def kgm3_to_ugm3(x):
    """kg/m3 -> ug/m3 (CAMS global PM2.5 and PM10)."""
    return x * 1e9
