"""Grid helpers: crop to a city box and pick the grid cell used as the city's point value.

Point method (decided after stage B): the nearest *land* cell, using the land-sea mask (lsm, 0 = sea,
1 = land). A cell counts as land when lsm >= 0.5. Coastal cities like Chennai otherwise get a cell that
is mostly sea, which has a different temperature, wind and humidity from the city.
"""

from dataclasses import dataclass

import numpy as np
import xarray as xr

LAND_THRESHOLD = 0.5


@dataclass
class Cell:
    """The chosen grid cell and how it was chosen (written to the sites table)."""

    lat: float
    lon: float
    method: str
    land_fraction: float | None = None


def crop(ds: xr.Dataset, bbox: list[float]) -> xr.Dataset:
    """Cut a lat/lon grid to bbox [lat_min, lat_max, lon_min, lon_max]; works for both latitude orders."""
    lat_min, lat_max, lon_min, lon_max = bbox
    lat = ds["latitude"]
    lat_slice = slice(lat_max, lat_min) if lat[0] > lat[-1] else slice(lat_min, lat_max)
    return ds.sel(latitude=lat_slice, longitude=slice(lon_min, lon_max))


def distance_km(lat1, lon1, lat2, lon2):
    """Great-circle distance (haversine), good enough to rank neighbouring cells."""
    p1, p2 = np.radians(lat1), np.radians(lat2)
    dlat, dlon = p2 - p1, np.radians(np.asarray(lon2) - np.asarray(lon1))
    a = np.sin(dlat / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dlon / 2) ** 2
    return 2 * 6371.0 * np.arcsin(np.sqrt(a))


def nearest_cell(lats, lons, lat: float, lon: float) -> Cell:
    """Plain nearest cell, used where no land-sea mask is needed (CAMS Europe over Amsterdam)."""
    glat, glon = np.meshgrid(np.asarray(lats), np.asarray(lons), indexing="ij")
    i = np.unravel_index(np.argmin(distance_km(lat, lon, glat, glon)), glat.shape)
    return Cell(float(glat[i]), float(glon[i]), "nearest cell")


def nearest_land_cell(lats, lons, land_fraction: np.ndarray, lat: float, lon: float) -> Cell:
    """Nearest cell whose land fraction is >= LAND_THRESHOLD.

    land_fraction has shape (len(lats), len(lons)). If no cell is land (never for our boxes), fall back
    to the plain nearest cell and say so in the method.
    """
    glat, glon = np.meshgrid(np.asarray(lats), np.asarray(lons), indexing="ij")
    dist = distance_km(lat, lon, glat, glon)
    land = np.asarray(land_fraction) >= LAND_THRESHOLD
    if not land.any():
        cell = nearest_cell(lats, lons, lat, lon)
        cell.method = "nearest cell (no land cell in box)"
        return cell
    i = np.unravel_index(np.argmin(np.where(land, dist, np.inf)), glat.shape)
    return Cell(
        float(glat[i]),
        float(glon[i]),
        f"nearest land cell (lsm >= {LAND_THRESHOLD})",
        round(float(np.asarray(land_fraction)[i]), 2),
    )


def land_fraction_on_grid(lsm: xr.DataArray, lats, lons, cell_size: float) -> np.ndarray:
    """Land fraction of each coarse cell = mean of the fine land-sea mask inside it.

    Used for CAMS global (0.4 deg), whose land-sea mask we take from the ECMWF 0.1 deg lsm of the
    same box. Cells at the box edge only average the fine points that are inside the box.
    """
    half = cell_size / 2
    out = np.full((len(lats), len(lons)), np.nan)
    for i, la in enumerate(np.asarray(lats)):
        for j, lo in enumerate(np.asarray(lons)):
            inside = lsm.where(
                (abs(lsm.latitude - la) <= half + 1e-6) & (abs(lsm.longitude - lo) <= half + 1e-6)
            )
            out[i, j] = float(inside.mean(skipna=True))
    return out
