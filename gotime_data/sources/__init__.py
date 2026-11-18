"""One module per source, each with the same small interface:

fetch(now=None) -> Path      download the newest data into a raw batch folder (cropped, our variables)
to_points(raw: Path) -> Batch  turn that folder into canonical rows, sites and cycle records
"""

from . import cams_eu, cams_global, ecmwf, eea, era5, knmi, openaq

SOURCES = {
    "ecmwf": ecmwf,
    "cams_eu": cams_eu,
    "cams_global": cams_global,
    "eea": eea,
    "knmi": knmi,
    "era5": era5,
    "openaq": openaq,
}
