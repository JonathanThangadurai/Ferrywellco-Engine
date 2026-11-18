"""Low-level reads of the stored table with DuckDB. The public interface is gotime_data.read."""

from datetime import datetime

import duckdb
import pandas as pd

from ..config import filesystem
from ..schema import COLUMNS, CYCLE_COLUMNS, SITE_COLUMNS, empty_hourly, normalise


def _files(pattern: str) -> list[str]:
    """Full paths (with protocol for remote storage) of the files matching pattern under the root."""
    fs, root = filesystem()
    paths = sorted(fs.glob(f"{root}/{pattern}"))
    return [
        p if fs.protocol in ("file", "local") or "file" in fs.protocol else fs.unstrip_protocol(p)
        for p in paths
    ]


def _connect() -> duckdb.DuckDBPyConnection:
    """DuckDB connection that can read the storage root (registers fsspec for s3:// and similar)."""
    con = duckdb.connect()
    fs, _ = filesystem()
    if not ("file" in fs.protocol or "local" in fs.protocol):
        con.register_filesystem(fs)
    return con


def read_hourly(
    location_id: str,
    start_utc: datetime,
    end_utc: datetime,
    variables: list[str] | None = None,
    kinds: list[str] | None = None,
) -> pd.DataFrame:
    """All stored rows (live and archive) for one location in [start_utc, end_utc)."""
    files = _files("live/hourly/*/*/*.parquet") + _files("archive/hourly/*/*/*.parquet")
    if not files:
        return empty_hourly()
    sql = (
        f"SELECT DISTINCT {', '.join(COLUMNS)} FROM read_parquet(?, union_by_name = true) "
        "WHERE location_id = ? AND valid_time_utc >= ? AND valid_time_utc < ?"
    )
    params: list = [files, location_id, start_utc, end_utc]
    if variables:
        sql += " AND list_contains(?, variable)"
        params.append(list(variables))
    if kinds:
        sql += " AND list_contains(?, kind)"
        params.append(list(kinds))
    con = _connect()
    con.execute("SET TimeZone = 'UTC'")
    df = (
        con.sql(sql + " ORDER BY valid_time_utc, variable, source", params=params)
        .to_arrow_table()
        .to_pandas()
    )
    return normalise(df)


def read_sites() -> pd.DataFrame:
    """The sites table: stations and the grid cell chosen per location and source."""
    files = _files("meta/sites/*.parquet")
    if not files:
        return pd.DataFrame(columns=SITE_COLUMNS)
    return (
        _connect()
        .sql("SELECT * FROM read_parquet(?, union_by_name = true)", params=[files])
        .to_arrow_table()
        .to_pandas()
    )


def read_cycles() -> pd.DataFrame:
    """One row per ingested cycle, location and variable, with publish time and completeness."""
    files = _files("meta/cycles/*/*.parquet")
    if not files:
        return pd.DataFrame(columns=CYCLE_COLUMNS)
    con = _connect()
    con.execute("SET TimeZone = 'UTC'")
    return (
        con.sql("SELECT * FROM read_parquet(?, union_by_name = true)", params=[files])
        .to_arrow_table()
        .to_pandas()
    )
