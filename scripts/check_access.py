"""Thin wrapper: the access check lives in gotime_data/check.py (also `uv run gotime-data check`)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from gotime_data.check import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
