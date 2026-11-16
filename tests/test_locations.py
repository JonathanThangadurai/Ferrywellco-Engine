"""Checks on config/locations.yaml so a typo in a box or time zone fails early."""

from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

CONFIG = Path(__file__).parent.parent / "config" / "locations.yaml"


def load_locations() -> dict:
    """Read the locations file as a plain dict."""
    return yaml.safe_load(CONFIG.read_text())["locations"]


def test_both_v1_cities_present():
    assert set(load_locations()) == {"amsterdam", "chennai"}


def test_point_inside_box_and_tz_valid():
    for loc in load_locations().values():
        lat_min, lat_max, lon_min, lon_max = loc["bbox"]
        assert lat_min < loc["lat"] < lat_max
        assert lon_min < loc["lon"] < lon_max
        ZoneInfo(loc["tz"])  # raises if the zone name is wrong


def test_box_spans_two_coarse_cells():
    """CAMS global is 0.4 deg; the box must hold at least 2 cells each way."""
    for loc in load_locations().values():
        lat_min, lat_max, lon_min, lon_max = loc["bbox"]
        assert lat_max - lat_min >= 0.8
        assert lon_max - lon_min >= 0.8
