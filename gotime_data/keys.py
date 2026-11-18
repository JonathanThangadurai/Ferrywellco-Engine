"""Read API keys from the environment, falling back to ~/.config/gotime/keys.env.

Keys are never printed or logged. Use `presence()` to report which keys are set.
"""

import os
from pathlib import Path

KEYS_FILE = Path.home() / ".config" / "gotime" / "keys.env"
KEY_NAMES = ["KNMI_API_KEY", "KNMI_EDR_API_KEY", "COPERNICUS_KEY", "ADS_URL", "CDS_URL", "OPENAQ_API_KEY"]


def parse_env_file(path: Path) -> dict[str, str]:
    """Parse simple NAME=value lines; skip blanks and # comments. No shell expansion."""
    values = {}
    if not path.exists():
        return values
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        values[name.strip()] = value.strip().strip('"').strip("'")
    return values


def load_keys(path: Path = KEYS_FILE) -> None:
    """Put keys from the file into os.environ, but only where the variable is not already set.

    The environment wins so a server can inject keys without the file.
    """
    for name, value in parse_env_file(path).items():
        os.environ.setdefault(name, value)


def get(name: str) -> str:
    """Return one key, loading the keys file first if needed. Raises if missing (without the value)."""
    if name not in os.environ:
        load_keys()
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"{name} is not set (env or {KEYS_FILE})")
    return value


def presence() -> dict[str, bool]:
    """Which keys are available, as yes/no only."""
    load_keys()
    return {name: bool(os.environ.get(name)) for name in KEY_NAMES}
