"""The serving snapshot in memory, with atomic reload.

reload() checks serving/<mode>/CURRENT. When the id changed it loads and checks the new snapshot next to
the old one, then swaps a single reference. A request that already holds the old snapshot keeps using it.
If the new one cannot be loaded (broken file, unknown engine or engine version), the old one stays.
"""

import logging
from dataclasses import dataclass
from datetime import datetime

from gotime_engine import ENGINES
from gotime_engine import snapshot as snapshot_files
from gotime_engine.protocol import GuidanceEngine

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Loaded:
    """One loaded snapshot and the engine that made it."""

    snap: dict
    engine: GuidanceEngine
    created_utc: datetime
    expected_next_utc: datetime
    locations: dict  # location id -> location entry

    @property
    def id(self) -> str:
        return self.snap["snapshot_id"]


def _prepare(snap: dict) -> Loaded:
    meta = snap["engine"]
    engine_cls = ENGINES.get(meta["name"])
    if engine_cls is None:
        raise ValueError(f"unknown engine {meta['name']}")
    engine = engine_cls()
    if engine.version != meta["version"]:
        raise ValueError(f"engine {meta['name']} is {engine.version}, snapshot needs {meta['version']}")
    return Loaded(
        snap=snap,
        engine=engine,
        created_utc=datetime.fromisoformat(snap["created_utc"]),
        expected_next_utc=datetime.fromisoformat(snap["expected_next_utc"]),
        locations={loc["id"]: loc for loc in snap["locations"]},
    )


class SnapshotStore:
    def __init__(self, mode: str):
        self.mode = mode
        self.current: Loaded | None = None

    def reload(self) -> bool:
        """Load CURRENT if it changed. Returns True when a new snapshot is now served."""
        try:
            sid = snapshot_files.current_id(self.mode)
            if sid is None or (self.current is not None and self.current.id == sid):
                return False
            loaded = _prepare(snapshot_files.load(self.mode, sid))
        except Exception:
            log.exception("snapshot reload failed for mode %s; keeping the current one", self.mode)
            return False
        self.current = loaded  # one assignment: requests see the old or the new snapshot, never a mix
        log.info("serving snapshot %s (mode %s)", sid, self.mode)
        return True
