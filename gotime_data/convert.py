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


def rh_from_t_td(t_c, td_c):
    """Relative humidity (%) from temperature and dew point (deg C), Magnus formula over water.

    Used for ECMWF and ERA5, which give dew point, not humidity.
    """
    a, b = 17.625, 243.04
    return 100 * np.exp(a * td_c / (b + td_c)) / np.exp(a * t_c / (b + t_c))


def wind_speed(u, v):
    """Wind speed (m/s) from the u and v components."""
    return np.hypot(u, v)


def jcm2_per_hour_to_wm2(q):
    """KNMI Q (J/cm2 summed over one hour) -> mean W/m2 over that hour: x 10 000 cm2/m2 / 3600 s."""
    return q * 10_000 / 3600


R_GAS = 8.314462618  # J mol-1 K-1
MOLAR_MASS_G = {"no2": 46.0055, "o3": 47.9982}
STANDARD_T_C = 25.0
STANDARD_P_HPA = 1013.25


def ppb_to_ugm3(ppb: pd.Series, gas: str, t_c: pd.Series | None = None, p_hpa: pd.Series | None = None):
    """Volume mixing ratio (ppb) -> ug/m3 = ppb x M x p / (R x T) / 1000.

    Uses the measured temperature and pressure for each hour where available, else the 25 C / 1013.25 hPa
    convention (1 ppb NO2 = 1.88 ug/m3, 1 ppb O3 = 1.96 ug/m3). Returns (values, method per value) so
    every value records which one was used.
    """
    ppb = ppb.astype("float64")
    t = (t_c if t_c is not None else pd.Series(np.nan, index=ppb.index)).reindex(ppb.index)
    p = (p_hpa if p_hpa is not None else pd.Series(np.nan, index=ppb.index)).reindex(ppb.index)
    t_used, p_used = t.fillna(STANDARD_T_C), p.fillna(STANDARD_P_HPA)
    ugm3 = ppb * MOLAR_MASS_G[gas] * (p_used * 100) / (R_GAS * (t_used + 273.15)) / 1000
    t_part = np.where(t.notna(), "measured T", f"{STANDARD_T_C:g} C")
    p_part = np.where(p.notna(), "measured p", f"{STANDARD_P_HPA:g} hPa")
    method = pd.Series(
        [f"ppb->ug/m3, {a}, {b}" for a, b in zip(t_part, p_part, strict=True)], index=ppb.index
    )
    return ugm3, method
