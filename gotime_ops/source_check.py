"""Live access and format check for one source (nightly in GitHub Actions; ERA5 weekly).

Fetches the newest data into a temporary storage root, turns it into canonical rows and validates them:
rows exist, every value passes schema validation, and at least some values are present. The reason on
failure is short and scrubbed of keys, because it ends up in a GitHub issue.
"""

import os
import tempfile

from gotime_data.check import scrub
from gotime_data.schema import validate
from gotime_data.sources import SOURCES


def check(name: str) -> tuple[bool, str]:
    old = os.environ.get("GOTIME_STORAGE_ROOT")
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["GOTIME_STORAGE_ROOT"] = tmp
        try:
            module = SOURCES[name]
            batch = module.to_points(module.fetch())
            rows = batch.hourly
            if rows.empty:
                return False, "no rows after parsing"
            problems = validate(rows)
            if problems:
                return False, "format: " + "; ".join(problems)[:300]
            if rows["value"].notna().sum() == 0:
                return False, "all values missing"
            variables = ", ".join(sorted(rows["variable"].unique()))
            return True, f"{len(rows)} rows ({variables})"
        except Exception as e:  # any failure is a finding; the message is scrubbed of keys
            return False, scrub(f"{type(e).__name__}: {e}")[:300]
        finally:
            if old is None:
                os.environ.pop("GOTIME_STORAGE_ROOT", None)
            else:
                os.environ["GOTIME_STORAGE_ROOT"] = old
