"""The canonical hourly table: columns, allowed values, variables with units, and validation.

Every source turns its data into this table, and the read interface returns it. See
docs/data-pipeline-design.md for the meaning of each column.
"""

from dataclasses import dataclass, field

import pandas as pd

COLUMNS = [
    "location_id",
    "valid_time_utc",
    "variable",
    "value",
    "source",
    "kind",
    "issue_time_utc",
    "lead_h",
    "status",
    "site_id",
    "method",
]
SITE_COLUMNS = ["site_id", "name", "lat", "lon", "station_type", "source"]
CYCLE_COLUMNS = [
    "source",
    "location_id",
    "variable",
    "issue_time_utc",
    "publish_time_utc",
    "fetched_time_utc",
    "n_expected",
    "n_missing",
    "complete",
]

KINDS = {"forecast", "observation", "reanalysis"}
STATUSES = {"ok", "missing", "interpolated"}
SOURCES = {"ecmwf_ifs", "cams_eu", "cams_global", "eea_e2a", "eea_e1a", "knmi_hourly", "era5", "openaq"}
# Internal sources are used for verification only and never appear in output for partners.
# OpenAQ: its terms do not allow building a competing service on its API (docs/data-sources.md).
INTERNAL_SOURCES = {"openaq"}
# Alternative grid cells are stored next to the default cell with this site_id suffix (CAMS global Chennai:
# coastal cell next to the default land cell). The read interface returns them only on request.
ALTERNATIVE_SITE_SUFFIX = ":coastal"
POLLEN_TYPES = ["alder", "birch", "grass", "mugwort", "olive", "ragweed"]

# variable name -> unit and meaning. The unit is also in the name, so a number is never read without it.
VARIABLES = {
    "t2m_c": "deg C, air temperature at 2 m (KNMI stations: 1.5 m)",
    "td2m_c": "deg C, dew point at 2 m (KNMI stations: 1.5 m)",
    "rh2m_pct": "%, relative humidity at 2 m",
    "wind10m_ms": "m/s, wind speed at 10 m",
    "ssrd_wm2": "W/m2, mean downward solar radiation at the surface over the interval",
    "precip_mm": "mm, precipitation in the hour (observations only)",
    "pm25_ugm3": "ug/m3, PM2.5",
    "pm10_ugm3": "ug/m3, PM10",
    "no2_ugm3": "ug/m3, NO2",
    "o3_ugm3": "ug/m3, O3",
    **{f"pollen_{p}_gm3": f"grains/m3, {p} pollen (CAMS: experimental)" for p in POLLEN_TYPES},
}


@dataclass
class Batch:
    """What one source produces from one raw download: hourly rows, sites seen, and cycle records."""

    hourly: pd.DataFrame
    sites: pd.DataFrame = field(default_factory=lambda: pd.DataFrame(columns=SITE_COLUMNS))
    cycles: pd.DataFrame = field(default_factory=lambda: pd.DataFrame(columns=CYCLE_COLUMNS))


def empty_hourly() -> pd.DataFrame:
    """An empty canonical table with the right dtypes."""
    return normalise(pd.DataFrame(columns=COLUMNS))


def normalise(df: pd.DataFrame) -> pd.DataFrame:
    """Put columns in order and give them fixed dtypes (UTC timestamps, float values, nullable lead)."""
    df = df.copy()
    for col in COLUMNS:
        if col not in df:
            df[col] = None
    df = df[COLUMNS]
    for col in ("valid_time_utc", "issue_time_utc"):
        df[col] = pd.to_datetime(df[col], utc=True).dt.as_unit("us")
    df["value"] = pd.to_numeric(df["value"], errors="coerce").astype("float64")
    df["lead_h"] = pd.to_numeric(df["lead_h"], errors="coerce").astype("Int64")
    for col in ("location_id", "variable", "source", "kind", "status", "site_id", "method"):
        df[col] = df[col].astype("object").where(df[col].notna(), None)
    return df.reset_index(drop=True)


def validate(df: pd.DataFrame) -> list[str]:
    """Return a list of problems (empty = valid). Checked before anything is written."""
    problems = []
    missing_cols = [c for c in COLUMNS if c not in df.columns]
    if missing_cols:
        return [f"missing columns: {missing_cols}"]
    if df.empty:
        return problems
    from .config import locations

    checks = [
        ("location_id", set(locations())),
        ("variable", set(VARIABLES)),
        ("source", SOURCES),
        ("kind", KINDS),
        ("status", STATUSES),
    ]
    for col, allowed in checks:
        bad = set(df[col].dropna().unique()) - allowed
        if bad or df[col].isna().any():
            problems.append(f"{col}: unknown or empty values {sorted(map(str, bad)) or ['<null>']}")
    for col in ("valid_time_utc", "issue_time_utc"):
        if not isinstance(df[col].dtype, pd.DatetimeTZDtype) or str(df[col].dt.tz) != "UTC":
            problems.append(f"{col} must be a UTC timestamp")
    if df["valid_time_utc"].isna().any():
        problems.append("valid_time_utc has nulls")
    # Missing must be null + status missing, never a number standing in for it.
    if (df["value"].isna() & (df["status"] != "missing")).any():
        problems.append("null value without status 'missing'")
    if (df["value"].notna() & (df["status"] == "missing")).any():
        problems.append("status 'missing' with a value")
    fc = df["kind"] == "forecast"
    if df.loc[fc, "lead_h"].isna().any():
        problems.append("forecast rows need lead_h")
    if (df.loc[~fc & (df["kind"] == "observation"), "lead_h"].notna()).any():
        problems.append("observation rows must have lead_h null")
    if df.duplicated(
        ["location_id", "valid_time_utc", "variable", "source", "issue_time_utc", "site_id"]
    ).any():
        problems.append("duplicate rows for the same key")
    return problems
