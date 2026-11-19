"""API settings from config/serving.yaml plus two environment variables.

GOTIME_SERVING_CONFIG  another settings file (default: config/serving.yaml in the repo)
GOTIME_API_KEYS_FILE   the hashed API keys file, outside the repo (default ~/.config/gotime/api_keys.json)
"""

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from gotime_data.config import REPO_ROOT

DEFAULT_CONFIG = REPO_ROOT / "config" / "serving.yaml"
DEFAULT_KEYS_FILE = Path.home() / ".config" / "gotime" / "api_keys.json"


@dataclass
class Settings:
    keys_file: Path
    modes: list[str] = field(default_factory=lambda: ["sandbox"])
    freshness_h: dict[str, dict[str, float]] = field(default_factory=dict)
    attribution: dict[str, str] = field(default_factory=dict)
    rate_per_minute: int = 60
    rate_burst: int = 20
    max_body_bytes: int = 4096
    max_days_ahead: int = 3
    ready_max_snapshot_age_h: float = 18
    snapshot_reload_s: float = 30
    keys_reload_s: float = 5

    def thresholds(self, source: str) -> dict[str, float]:
        return self.freshness_h.get(source) or self.freshness_h.get("default") or {"ok": 24, "degraded": 48}


def keys_file() -> Path:
    return Path(os.environ.get("GOTIME_API_KEYS_FILE", DEFAULT_KEYS_FILE)).expanduser()


def load_settings(path: Path | None = None) -> Settings:
    path = Path(path or os.environ.get("GOTIME_SERVING_CONFIG", DEFAULT_CONFIG))
    cfg = yaml.safe_load(path.read_text()) or {}
    rate = cfg.get("rate_limit", {})
    return Settings(
        keys_file=keys_file(),
        modes=list(cfg.get("modes", ["sandbox"])),
        freshness_h=cfg.get("freshness_h", {}),
        attribution=cfg.get("attribution", {}),
        rate_per_minute=int(rate.get("per_minute", 60)),
        rate_burst=int(rate.get("burst", 20)),
        max_body_bytes=int(cfg.get("max_body_bytes", 4096)),
        max_days_ahead=int(cfg.get("max_days_ahead", 3)),
        ready_max_snapshot_age_h=float(cfg.get("ready_max_snapshot_age_h", 18)),
        snapshot_reload_s=float(cfg.get("snapshot_reload_s", 30)),
        keys_reload_s=float(cfg.get("keys_reload_s", 5)),
    )
