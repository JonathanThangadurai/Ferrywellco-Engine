"""Read interface for the algorithm side (docs/data-interface.md, draft pending Tilman's review).

    get_hourly(location_id, start_utc, end_utc, variables)  -> canonical table (pandas DataFrame)
    mean_24h(df, variable)                                  -> rolling 24-hour mean per hour
    max_8h_mean(df, variable, tz)                           -> maximum 8-hour mean per local day

Averages are computed on read from the stored hourly values, never stored over them.
"""

from datetime import datetime

import pandas as pd

from .schema import ALTERNATIVE_SITE_SUFFIX, INTERNAL_SOURCES, normalise
from .store import read_cycles, read_hourly

GROUP = ["location_id", "source", "kind", "site_id"]


def _check_utc(t: datetime, name: str) -> pd.Timestamp:
    """Refuse naive times, so nobody mixes local time and UTC by accident."""
    ts = pd.Timestamp(t)
    if ts.tzinfo is None:
        raise ValueError(f"{name} must be timezone-aware (UTC), got naive {t}")
    return ts.tz_convert("UTC")


def latest_complete_cycles(df: pd.DataFrame, cycles: pd.DataFrame) -> pd.DataFrame:
    """Keep, per source and variable, only the newest forecast cycle that is complete.

    Complete = every expected hour present (cycles table). Without cycle records the newest cycle in
    the data is used. Observations and reanalysis rows pass through, with the newest issue time per hour.
    """
    fc = df[df["kind"] == "forecast"]
    keep = []
    for (loc, source, var), part in fc.groupby(["location_id", "source", "variable"]):
        c = cycles[
            (cycles["location_id"] == loc) & (cycles["source"] == source) & (cycles["variable"] == var)
        ]
        complete = pd.to_datetime(c.loc[c["complete"].astype(bool), "issue_time_utc"], utc=True)
        present = part["issue_time_utc"]
        candidates = complete[complete.isin(present)]
        newest = candidates.max() if not candidates.empty else present.max()
        keep.append(part[part["issue_time_utc"] == newest])
    other = df[df["kind"] != "forecast"].sort_values("issue_time_utc")
    other = other.drop_duplicates(
        ["location_id", "valid_time_utc", "variable", "source", "site_id"], keep="last"
    )
    out = pd.concat(keep + [other], ignore_index=True) if keep else other
    return normalise(out).sort_values(["valid_time_utc", "variable", "source"]).reset_index(drop=True)


def get_hourly(
    location_id: str,
    start_utc: datetime,
    end_utc: datetime,
    variables: list[str] | None = None,
    kind: str | None = "forecast",
    latest_only: bool = True,
    include_internal: bool = False,
    include_alternatives: bool = False,
) -> pd.DataFrame:
    """Hourly rows for one location in [start_utc, end_utc), in the canonical table format.

    kind: 'forecast' (default), 'observation', 'reanalysis', or None for all.
    latest_only: newest complete forecast cycle per source and variable (False = all cycles).
    include_internal: also return internal-only sources (OpenAQ). Never True for anything partners see.
    include_alternatives: also return alternative grid cells (site_id ending in ':coastal', CAMS global
        Chennai); by default only the default cell per source is returned.
    """
    start, end = _check_utc(start_utc, "start_utc"), _check_utc(end_utc, "end_utc")
    df = read_hourly(location_id, start, end, variables, [kind] if kind else None)
    if not include_internal:
        df = df[~df["source"].isin(INTERNAL_SOURCES)]
    if not include_alternatives:
        df = df[~df["site_id"].fillna("").str.endswith(ALTERNATIVE_SITE_SUFFIX)]
    return latest_complete_cycles(df, read_cycles()) if latest_only else df.reset_index(drop=True)


def _hourly_series(part: pd.DataFrame) -> pd.Series:
    """One group's values on a gap-free hourly index (missing hours become NaN)."""
    s = part.set_index("valid_time_utc")["value"].sort_index()
    s = s[~s.index.duplicated(keep="last")]
    return s.reindex(pd.date_range(s.index.min(), s.index.max(), freq="h"))


def mean_24h(df: pd.DataFrame, variable: str, min_hours: int = 18) -> pd.DataFrame:
    """Rolling 24-hour mean: for each hour, the mean of that hour and the 23 before it.

    Returns valid_time_utc (last hour of the window), value, n_hours; value is null when fewer than
    min_hours hours have data (placeholder threshold until Tilman decides, open question 9).
    """
    out = []
    for key, part in df[df["variable"] == variable].groupby(GROUP, dropna=False):
        s = _hourly_series(part)
        n = s.rolling(24, min_periods=1).count()
        mean = s.rolling(24, min_periods=1).mean().where(n >= min_hours)
        frame = pd.DataFrame({"valid_time_utc": s.index, "value": mean.to_numpy(), "n_hours": n.astype(int)})
        out.append(frame.assign(**dict(zip(GROUP, key, strict=True)), variable=f"{variable}_mean24h"))
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame()


def max_8h_mean(df: pd.DataFrame, variable: str, tz: str, min_hours: int = 6, min_windows: int = 18):
    """Maximum 8-hour mean per local calendar day (as for ozone).

    Each 8-hour window (hour h and the 7 before) needs min_hours hours with data, and belongs to the
    local day of its last hour. A day's maximum needs min_windows valid windows. Local days are 23, 24
    or 25 hours long around daylight-saving changes; grouping by local date handles that.
    """
    out = []
    for key, part in df[df["variable"] == variable].groupby(GROUP, dropna=False):
        s = _hourly_series(part)
        n = s.rolling(8, min_periods=1).count()
        m8 = s.rolling(8, min_periods=1).mean().where(n >= min_hours)
        local_date = s.index.tz_convert(tz).date
        g = pd.DataFrame({"m8": m8.to_numpy(), "local_date": local_date}).groupby("local_date")
        day = pd.DataFrame({"value": g["m8"].max(), "n_windows": g["m8"].count(), "n_hours": g["m8"].size()})
        day.loc[day["n_windows"] < min_windows, "value"] = float("nan")
        day = day.reset_index().assign(**dict(zip(GROUP, key, strict=True)), variable=f"{variable}_max8h")
        out.append(day)
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame()
