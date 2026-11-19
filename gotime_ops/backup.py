"""Backups: copy what cannot be downloaded again to a target folder (local now, s3:// later via fsspec).

Copied: archive/ (processed hourly data older than 10 days), meta/ (sites, cycles, quality) and the frozen
climatology/ folder (KNMI daily TG, cannot be downloaded again). live/ and raw/ are not backed up: they are
rebuilt from the sources within days. Serving snapshots are rebuilt by the next precompute.

Incremental: a file is copied when it is missing at the target or its size differs. Restore is the same
copy in the other direction.
"""

import logging
import os

import fsspec

from gotime_data.config import REPO_ROOT, filesystem

FOLDERS = ["archive", "meta"]
log = logging.getLogger(__name__)


def _sync(src_fs, src: str, dst_fs, dst: str) -> int:
    """Copy new or changed files from src to dst. Returns the number of files copied."""
    if not src_fs.exists(src):
        return 0
    copied = 0
    for path, info in src_fs.find(src, detail=True).items():
        rel = path[len(src.rstrip("/")) + 1 :]
        target = f"{dst.rstrip('/')}/{rel}"
        if dst_fs.exists(target) and dst_fs.size(target) == info["size"]:
            continue
        dst_fs.makedirs(target.rsplit("/", 1)[0], exist_ok=True)
        dst_fs.pipe_file(target, src_fs.cat_file(path))
        copied += 1
    return copied


def target() -> str:
    t = os.environ.get("GOTIME_BACKUP_TARGET")
    if not t:
        raise RuntimeError("GOTIME_BACKUP_TARGET is not set (a folder or s3://bucket/prefix)")
    return t.rstrip("/")


def backup() -> dict[str, int]:
    fs, root = filesystem()
    dst_fs, dst = fsspec.core.url_to_fs(target())
    local = fsspec.filesystem("file")
    counts = {name: _sync(fs, f"{root}/{name}", dst_fs, f"{dst}/{name}") for name in FOLDERS}
    counts["climatology"] = _sync(local, str(REPO_ROOT / "climatology"), dst_fs, f"{dst}/climatology")
    log.info("backup done", extra={"fields": {"copied": counts}})
    return counts


def restore(climatology_to: str | None = None) -> dict[str, int]:
    """Copy archive/ and meta/ back into the storage root (and climatology/ to a folder, if given)."""
    fs, root = filesystem()
    src_fs, src = fsspec.core.url_to_fs(target())
    counts = {name: _sync(src_fs, f"{src}/{name}", fs, f"{root}/{name}") for name in FOLDERS}
    if climatology_to:
        counts["climatology"] = _sync(src_fs, f"{src}/climatology", fsspec.filesystem("file"), climatology_to)
    log.info("restore done", extra={"fields": {"copied": counts}})
    return counts
