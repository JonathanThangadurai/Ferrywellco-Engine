"""The serving snapshot: one file per forecast cycle with ready guidance per location, date and profile.

Layout under GOTIME_STORAGE_ROOT:
    serving/<mode>/<snapshot_id>.json.gz   the snapshot (written to a temporary name, then renamed)
    serving/<mode>/CURRENT                 one line: the snapshot id the API should serve

Written by the precompute job, read by the API. Only per-location results are stored: never anything
about a request or a person.
"""

import gzip
import json
import logging
import uuid

from gotime_data.config import filesystem

FORMAT = 1
KEEP = 4  # snapshots kept per mode, for rollback (write an older id to CURRENT)
log = logging.getLogger(__name__)


def assessment_key(location_id: str, local_date: str, profile_id: str) -> str:
    return f"{location_id}|{local_date}|{profile_id}"


def _dir(mode: str) -> tuple:
    fs, root = filesystem()
    return fs, f"{root}/serving/{mode}"


def _atomic_write(fs, path: str, data: bytes) -> None:
    """Write to a temporary file, then rename over the target: readers see the old or the new file."""
    tmp = f"{path}.{uuid.uuid4().hex}.tmp"
    with fs.open(tmp, "wb") as f:
        f.write(data)
    fs.mv(tmp, path)


def write(snapshot: dict) -> str:
    """Store a snapshot, then point CURRENT at it (in that order). Returns the snapshot id."""
    fs, base = _dir(snapshot["mode"])
    fs.makedirs(base, exist_ok=True)
    sid = snapshot["snapshot_id"]
    body = gzip.compress(json.dumps(snapshot, separators=(",", ":")).encode(), mtime=0)
    _atomic_write(fs, f"{base}/{sid}.json.gz", body)
    load(snapshot["mode"], sid)  # read it back before switching: a broken file never becomes CURRENT
    _atomic_write(fs, f"{base}/CURRENT", f"{sid}\n".encode())
    prune(snapshot["mode"])
    return sid


def current_id(mode: str) -> str | None:
    """The id in CURRENT, or None when no snapshot was written yet."""
    fs, base = _dir(mode)
    path = f"{base}/CURRENT"
    if not fs.exists(path):
        return None
    return fs.cat_file(path).decode().strip() or None


def load(mode: str, snapshot_id: str) -> dict:
    """Read and check one snapshot. Raises ValueError when it is incomplete or of another format."""
    fs, base = _dir(mode)
    snap = json.loads(gzip.decompress(fs.cat_file(f"{base}/{snapshot_id}.json.gz")))
    for key in ("snapshot_id", "mode", "engine", "created_utc", "locations", "assessments"):
        if key not in snap:
            raise ValueError(f"snapshot {snapshot_id}: missing {key}")
    if snap.get("format") != FORMAT or snap["snapshot_id"] != snapshot_id or snap["mode"] != mode:
        raise ValueError(f"snapshot {snapshot_id}: wrong format, id or mode")
    return snap


def prune(mode: str, keep: int = KEEP) -> None:
    """Delete all but the newest `keep` snapshots (never the CURRENT one)."""
    fs, base = _dir(mode)
    current = current_id(mode)
    files = [f for f in fs.ls(base, detail=True) if f["name"].endswith(".json.gz")]
    files.sort(key=lambda f: f.get("mtime") or f.get("LastModified") or 0, reverse=True)
    for f in files[keep:]:
        if not f["name"].endswith(f"/{current}.json.gz"):
            fs.rm(f["name"])
