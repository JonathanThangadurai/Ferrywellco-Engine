"""Build the fixed end-to-end snapshot in tests/fixtures/e2e/ (synthetic data, committed).

Used by the golden-response tests (tests/test_golden.py) and by the Newman run in CI, which starts the API
container with GOTIME_TEST_NOW. Rebuild it on purpose only, e.g. after a SandboxEngine version change:

    uv run python -m tests.e2e.make_snapshot

then update the golden files (UPDATE_GOLDEN=1 uv run pytest tests/test_golden.py) in the same commit.
"""

import os
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path

ISSUE = "2026-11-19 00:00"
NOW = datetime(2026, 11, 19, 10, tzinfo=UTC)  # the clock of the "fresh" API in tests and CI
DEGRADED_NOW = datetime(2026, 11, 19, 20, tzinfo=UTC)  # ECMWF 20 h old: degraded (> 16 h)
UNAVAILABLE_NOW = datetime(2026, 11, 20, 10, tzinfo=UTC)  # ECMWF 34 h old: unavailable (> 30 h)
OUT = Path(__file__).resolve().parent.parent / "fixtures" / "e2e"


def build(out: Path = OUT) -> str:
    from gotime_engine import SandboxEngine
    from gotime_engine.precompute import run
    from tests.api_support import write_forecast

    old = os.environ.get("GOTIME_STORAGE_ROOT")
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["GOTIME_STORAGE_ROOT"] = tmp
        try:
            write_forecast(ISSUE)
            sid = run(SandboxEngine(), NOW)
            shutil.rmtree(out / "serving", ignore_errors=True)
            shutil.copytree(Path(tmp) / "serving", out / "serving")
        finally:
            if old is None:
                os.environ.pop("GOTIME_STORAGE_ROOT", None)
            else:
                os.environ["GOTIME_STORAGE_ROOT"] = old
    return sid


if __name__ == "__main__":
    print(build())
