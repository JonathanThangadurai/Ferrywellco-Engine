"""Point selection: nearest land cell with the land-sea mask (the Chennai coast case)."""

import xarray as xr

from gotime_data import grid
from gotime_data.config import locations
from gotime_data.sources import cams_global

from .conftest import FIXTURES

CHENNAI = locations()["chennai"]


def chennai_lsm():
    return xr.open_dataset(FIXTURES / "static" / "ecmwf_lsm_chennai.nc")["lsm"].load()


def test_chennai_ecmwf_nearest_cell_is_mostly_sea_and_land_cell_is_chosen():
    lsm = chennai_lsm()
    plain = grid.nearest_cell(lsm.latitude, lsm.longitude, CHENNAI["lat"], CHENNAI["lon"])
    assert (round(plain.lat, 1), round(plain.lon, 1)) == (13.1, 80.3)
    assert float(lsm.sel(latitude=plain.lat, longitude=plain.lon, method="nearest")) < 0.5  # 0.37: mostly sea

    land = grid.nearest_land_cell(lsm.latitude, lsm.longitude, lsm.values, CHENNAI["lat"], CHENNAI["lon"])
    assert land.land_fraction >= 0.5
    assert (round(land.lat, 1), round(land.lon, 1)) == (13.1, 80.2)  # one cell inland
    assert grid.distance_km(CHENNAI["lat"], CHENNAI["lon"], land.lat, land.lon) < 15
    assert "nearest land cell" in land.method


def test_chennai_cams_global_cell_uses_ecmwf_land_share():
    """CAMS 0.4 deg: the nearest cell (13.2, 80.4) is mostly sea; the land share picks (13.2, 80.0)."""
    lats, lons = [13.6, 13.2, 12.8], [80.0, 80.4, 80.8]
    share = grid.land_fraction_on_grid(chennai_lsm(), lats, lons, cams_global.CELL_SIZE)
    assert share[1, 1] < 0.5  # 13.2, 80.4
    cell = cams_global.choose_cell(lats, lons, CHENNAI, chennai_lsm())
    assert (cell.lat, cell.lon) == (13.2, 80.0)
    assert cell.method == "nearest land cell (lsm >= 0.5), land share from ECMWF 0.1 deg lsm"


def test_amsterdam_nearest_cell_is_already_land():
    lsm = xr.open_dataset(FIXTURES / "static" / "ecmwf_lsm_amsterdam.nc")["lsm"].load()
    ams = locations()["amsterdam"]
    plain = grid.nearest_cell(lsm.latitude, lsm.longitude, ams["lat"], ams["lon"])
    land = grid.nearest_land_cell(lsm.latitude, lsm.longitude, lsm.values, ams["lat"], ams["lon"])
    assert (round(plain.lat, 1), round(plain.lon, 1)) == (round(land.lat, 1), round(land.lon, 1))


def test_chennai_cams_own_land_sea_mask_picks_the_same_cell():
    """Cross-check (ADS land_sea_mask, fetched 18 Nov): CAMS's own mask agrees with the ECMWF land share."""
    own = xr.open_dataset(FIXTURES / "static" / "cams_global_lsm_chennai.nc")["lsm"].load()
    cell = grid.nearest_land_cell(own.latitude, own.longitude, own.values, CHENNAI["lat"], CHENNAI["lon"])
    assert (round(cell.lat, 1), round(cell.lon, 1)) == (13.2, 80.0)
    assert float(own.sel(latitude=13.2, longitude=80.4)) < 0.5
