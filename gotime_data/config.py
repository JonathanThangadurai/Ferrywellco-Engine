"""Settings: locations from config/locations.yaml and the storage root.

The storage root (GOTIME_STORAGE_ROOT) is a local path now and can become s3://... later; all
processed data goes through fsspec, so that change needs no code change. Raw files are always local
working files, because cfgrib and cdsapi need a real file on disk.
"""

import os
from functools import cache
from pathlib import Path

import fsspec
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
LOCATIONS_FILE = REPO_ROOT / "config" / "locations.yaml"

RAW_RETENTION_DAYS = 2
LIVE_RETENTION_DAYS = 10


@cache
def locations() -> dict[str, dict]:
    """All locations by id: name, lat, lon, tz, bbox [lat_min, lat_max, lon_min, lon_max]."""
    return yaml.safe_load(LOCATIONS_FILE.read_text())["locations"]


def storage_root() -> str:
    """Root for live/, archive/ and meta/. Default ./data next to the repo."""
    return os.environ.get("GOTIME_STORAGE_ROOT", str(REPO_ROOT / "data")).rstrip("/")


def filesystem() -> tuple[fsspec.AbstractFileSystem, str]:
    """fsspec filesystem and root path inside it (local or s3)."""
    fs, root = fsspec.core.url_to_fs(storage_root())
    return fs, root


def raw_dir() -> Path:
    """Local folder for raw downloads: <root>/raw when the root is local, else ./data/raw."""
    root = storage_root()
    base = Path(root) if "://" not in root else REPO_ROOT / "data"
    return base / "raw"
