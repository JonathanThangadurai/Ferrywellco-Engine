"""Per-partner API keys, stored hashed in a JSON file outside the repo.

Key format: gt_<key_id>_<secret>. key_id is 8 hex characters (fine to log); the secret is 32 random
bytes. The file holds only SHA-256(secret): enough for random secrets, and nothing in it can be used to
call the API. Checks use a constant-time comparison. Revoking takes effect without a restart (the API
re-reads the file when it changes).
"""

import hashlib
import hmac
import json
import os
import secrets
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from gotime_engine.protocol import MODES

from .problems import Problem

PREFIX = "gt"


@dataclass
class KeyRecord:
    key_id: str
    partner: str
    mode: str
    sha256: str
    created_utc: str
    revoked: bool = False
    revoked_utc: str | None = None
    rate_per_minute: int | None = None  # None = default from settings
    rate_burst: int | None = None


def _hash(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def read_keys(path: Path) -> dict[str, KeyRecord]:
    if not path.exists():
        return {}
    return {k["key_id"]: KeyRecord(**k) for k in json.loads(path.read_text()).get("keys", [])}


def write_keys(path: Path, keys: dict[str, KeyRecord]) -> None:
    """Write via a temporary file and rename; the file and its folder are readable by the owner only."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_suffix(f".{secrets.token_hex(4)}.tmp")
    tmp.write_text(json.dumps({"keys": [asdict(k) for k in keys.values()]}, indent=2) + "\n")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def create_key(path: Path, partner: str, mode: str, rate_per_minute: int | None = None) -> tuple[str, str]:
    """Add a key. Returns (key_id, full key). The full key is not stored anywhere by this function.

    Each key has exactly one mode (sandbox or live); requests with it are always served in that mode.
    """
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}")
    keys = read_keys(path)
    key_id = secrets.token_hex(4)
    while key_id in keys:
        key_id = secrets.token_hex(4)
    secret = secrets.token_urlsafe(32)
    keys[key_id] = KeyRecord(
        key_id=key_id,
        partner=partner,
        mode=mode,
        sha256=_hash(secret),
        created_utc=datetime.now(UTC).isoformat(timespec="seconds"),
        rate_per_minute=rate_per_minute,
    )
    write_keys(path, keys)
    return key_id, f"{PREFIX}_{key_id}_{secret}"


def revoke_key(path: Path, key_id: str) -> bool:
    keys = read_keys(path)
    if key_id not in keys or keys[key_id].revoked:
        return False
    keys[key_id].revoked = True
    keys[key_id].revoked_utc = datetime.now(UTC).isoformat(timespec="seconds")
    write_keys(path, keys)
    return True


class KeyStore:
    """The keys file in memory; re-read when its modification time changes (checked every few seconds)."""

    def __init__(self, path: Path, reload_s: float = 5):
        self.path, self.reload_s = path, reload_s
        self._mtime: float | None = None
        self._checked = 0.0
        self.keys: dict[str, KeyRecord] = {}
        self.reload()

    def reload(self) -> None:
        mtime = self.path.stat().st_mtime if self.path.exists() else None
        if mtime != self._mtime:
            self.keys, self._mtime = read_keys(self.path), mtime
        self._checked = time.monotonic()

    def verify(self, authorization: str | None) -> KeyRecord:
        """The key record for an `Authorization: Bearer gt_<id>_<secret>` header, or 401."""
        if time.monotonic() - self._checked > self.reload_s:
            self.reload()
        unauthorized = Problem(401, "unauthorized", headers={"WWW-Authenticate": "Bearer"})
        if not authorization or not authorization.startswith("Bearer "):
            raise unauthorized
        parts = authorization[len("Bearer ") :].strip().split("_", 2)
        if len(parts) != 3 or parts[0] != PREFIX:
            raise unauthorized
        record = self.keys.get(parts[1])
        expected = record.sha256 if record else _hash("not-a-key")
        if not hmac.compare_digest(_hash(parts[2]), expected) or record is None or record.revoked:
            raise unauthorized
        if record.mode not in MODES:  # a hand-edited keys file must not open another mode
            raise unauthorized
        return record
