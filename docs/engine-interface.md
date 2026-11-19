# Guidance engine interface (for Tilman)

> **Status: DRAFT.** Every output field below is marked *draft, to agree with Tilman*. The data layer and the
> API only define where the engine plugs in. All guidance science (what counts as a good hour, thresholds,
> reasons) is Tilman's. The code here will contain only a `SandboxEngine` with clearly synthetic output, so the
> API can be built and tested before the real engine exists.

## Why two methods

The API answers each request in under 100 ms from a snapshot built once per forecast cycle (`api-design.md`).
Some request options are continuous (earliest/latest start, duration), so we cannot precompute every answer.
The engine therefore has two methods:

1. `assess` (slow is fine, runs once per cycle in the precompute job): judges every hour of one local date for
   one *profile* (the discrete options).
2. `choose` (must be fast, runs per request in the API): picks the window from the ready assessment and the
   window options.

If you prefer a different split, tell us; only the boundary matters to the API.

## Protocol

```python
from datetime import date, datetime
from typing import Literal, Protocol
import pandas as pd


class GuidanceEngine(Protocol):
    name: str                                # e.g. "sandbox", "gotime-v1"
    version: str                             # changes whenever results can change; part of the snapshot id
    mode: Literal["live", "sandbox"]

    def required_variables(self) -> list[str]:
        """Canonical variable names the engine needs (data-interface.md). Used for freshness status."""

    def profiles(self) -> list["Profile"]:
        """Every discrete option combination to precompute."""

    def assess(self, hourly: pd.DataFrame, location: "LocationInfo", local_date: date,
               profile: "Profile", now_utc: datetime) -> "Assessment":
        """Judge each hour of local_date for this profile. Runs once per cycle."""

    def choose(self, assessment: "Assessment", options: "WindowOptions") -> "GuidanceResult":
        """Pick the window for one request. Pure and fast (< 5 ms)."""
```

## Inputs (fixed by the data layer)

- `hourly`: the canonical table from `get_hourly(location_id, start, end, variables=required_variables(),
  kind=None, latest_only=True)` for the local date **plus the 24 h before**, so 24-hour means can be made with
  `mean_24h` and `max_8h_mean`.
  - Columns: `data-interface.md`.
  - Missing hours are `value = null` with `status = missing`, never 0.
  - Internal sources (OpenAQ) are never passed in.
- `location`: `LocationInfo(id, alias, timezone, lat, lon)`. The timezone is IANA; use it per instant.
- `local_date`: a date at the location (a day of 23, 24 or 25 hours).
- `now_utc`: passed in, so the engine never reads the clock and results can be reproduced.
- `Profile` (discrete; precomputed for every combination):

  | field | values | note |
  |---|---|---|
  | `activity_type` | `walk`, `run`, `cycle`, `outdoor_workout` | draft list, to agree with AEIVA and Tilman |
  | `heat_adapted` | `yes`, `no`, `unknown` | |
  | `hay_fever` | `yes`, `no`, `unknown` | |

- `WindowOptions` (per request): `earliest_start` and `latest_start` (local times on `local_date`) and
  `duration_min` (10–240).

## Outputs (all fields: draft, to agree with Tilman)

`Assessment` is whatever `choose` needs. It must be JSON-serialisable, because it is stored in the snapshot.
Suggested: one entry per local hour with a code or score, plus the reasons that apply to that hour. The API
never looks inside it.

`GuidanceResult`:

| field | type | meaning | status |
|---|---|---|---|
| `window.status` | `found` \| `none` | whether any window fits the options | draft, to agree with Tilman |
| `window.start`, `window.end` | aware datetime (local) | the suggested window; present only if `found` | draft, to agree with Tilman |
| `window.latest_start` | aware datetime (local) | latest start that still gives the same intensity hint | draft, to agree with Tilman |
| `intensity_hint` | code | e.g. `as_planned`, `go_easier`, `light_only` (placeholder codes) | draft, to agree with Tilman |
| `indoor_alternative` | `suggested` \| `not_suggested` | whether to offer an indoor option | draft, to agree with Tilman |
| `reasons[]` | list | why; ordered by importance | draft, to agree with Tilman |
| `reasons[].code` | stable code | e.g. `heat_high`, `pm25_elevated`, `pollen_birch_high` (placeholders) | draft, to agree with Tilman |
| `reasons[].params` | dict | numbers behind the reason, keys with units (`t2m_c`, `pm25_ugm3`) | draft, to agree with Tilman |
| `reasons[].source` | source id | `ecmwf_ifs`, `cams_eu`, `cams_global` | draft, to agree with Tilman |
| `reasons[].text` | optional str | English text; partners may translate by `code` | draft, to agree with Tilman |
| `variables_used` | list[str] | which variables shaped the result (for data status) | draft, to agree with Tilman |

The API adds `mode`, `location`, `data` (freshness and attribution) and `request_id` itself; the engine does
not.

## Rules for any engine

- No I/O, network, clock or global state in `assess` or `choose`. The same inputs give the same output.
- Handle missing hours explicitly (`status = missing`). Never treat null as 0.
- Wellness, not health: no text says "safe", "clean" or "healthy", and no medical claims.
- Codes are stable once agreed: partners build on them. New codes are added; old ones are not renamed.
- Raise an exception on a bug; the API turns it into a 500 `engine-error`. Do not return partial results.

## Sandbox engine (built in stage D2)

`SandboxEngine` gives plausible but clearly synthetic output:
- `mode = sandbox`.
- Every reason code is prefixed `sandbox_`, with the text "Sandbox example, not guidance".
- No thresholds taken from literature.

It exists so partners can integrate and we can test the API end to end. It is not a first version of the
science.

## Questions for Tilman

1. Does the assess/choose split work for you? What should `Assessment` hold per hour?
2. Which profile options change the result (activity type, heat_adapted, hay_fever, others)?
3. Intensity hint and reason codes: your list.
4. Do you need observations too (e.g. the last measured hours) as input, or forecasts only?
