"""Storage of the canonical table: write, read, retention. Layout under GOTIME_STORAGE_ROOT:

    live/hourly/source=<source>/date=<YYYY-MM-DD>/part.parquet     last 10 days
    archive/hourly/source=<source>/date=<YYYY-MM-DD>/part.parquet  older (kept)
    meta/sites/<source>.parquet                                    stations and chosen grid cells
    meta/cycles/<source>/<batch_id>.parquet                        one row per cycle, location, variable

`date` is the UTC date of the forecast issue time (forecasts) or of the valid time (observations).
"""

from .read import read_cycles, read_hourly, read_sites
from .retention import clean
from .write import write_batch

__all__ = ["clean", "read_cycles", "read_hourly", "read_sites", "write_batch"]
