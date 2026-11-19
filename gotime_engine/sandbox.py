"""SandboxEngine: plausible but clearly synthetic output, so partners can integrate before the real engine.

This is NOT a first version of the guidance science (that belongs to the health engine). It uses no thresholds from
literature: each hour is only compared with the other hours of the same day ("warmer than most hours
today"), and every reason code starts with `sandbox_` and carries the text "Sandbox example, not guidance."
"""

from datetime import UTC, date, datetime, timedelta

import pandas as pd

from .protocol import (
    Assessment,
    GuidanceResult,
    LocationInfo,
    Profile,
    Reason,
    WindowOptions,
    all_profiles,
    local_day_hours,
)

SANDBOX_TEXT = "Sandbox example, not guidance."
POLLEN = [f"pollen_{p}_gm3" for p in ("alder", "birch", "grass", "mugwort", "olive", "ragweed")]
HINTS = {0: "as_planned", 1: "go_easier", 2: "light_only"}  # placeholder codes, to agree with the health engine
STEP = timedelta(minutes=15)  # candidate start times are tried every 15 minutes


class SandboxEngine:
    name = "sandbox"
    version = "0.1.0"
    mode = "sandbox"

    def required_variables(self) -> list[str]:
        return ["t2m_c", "pm25_ugm3"]

    def optional_variables(self) -> list[str]:
        return list(POLLEN)

    def profiles(self) -> list[Profile]:
        return all_profiles()

    def assess(
        self,
        hourly: pd.DataFrame,
        location: LocationInfo,
        local_date: date,
        profile: Profile,
        now_utc: datetime,
    ) -> Assessment:
        """Level 0-2 per hour from how the hour compares with the rest of the day (synthetic)."""
        hours = local_day_hours(local_date, location.timezone)
        day = hourly[hourly["valid_time_utc"].isin(hours) & hourly["value"].notna()]
        values = day.pivot_table(index="valid_time_utc", columns="variable", values="value", aggfunc="first")
        values = values.reindex(hours)
        sources = day.drop_duplicates("variable").set_index("variable")["source"].to_dict()
        required = self.required_variables()
        for var in required:
            if var not in values:
                values[var] = float("nan")
        ok = values[required].notna().all(axis=1)

        t = values["t2m_c"][ok]
        warm_band = 0.4 if profile.heat_adapted == "no" else 0.25  # synthetic: a wider band, nothing more
        warm_from = t.max() - warm_band * (t.max() - t.min()) if t.max() > t.min() else float("inf")
        pm = values["pm25_ugm3"][ok]
        pm_from = pm.quantile(0.75) if pm.max() > pm.min() else float("inf")
        pollen = [p for p in POLLEN if p in values and values[p].notna().any()]

        out = []
        for h in hours:
            entry = {
                "start_utc": h.isoformat(),
                "status": "ok" if ok[h] else "missing",
                "level": 0,
                "reasons": [],
            }
            if ok[h]:
                if values.at[h, "t2m_c"] >= warm_from:
                    entry["reasons"].append(
                        _reason("sandbox_warmer_hour", sources.get("t2m_c"), t2m_c=values.at[h, "t2m_c"])
                    )
                if values.at[h, "pm25_ugm3"] >= pm_from:
                    entry["reasons"].append(
                        _reason(
                            "sandbox_higher_pm25",
                            sources.get("pm25_ugm3"),
                            pm25_ugm3=values.at[h, "pm25_ugm3"],
                        )
                    )
                if profile.hay_fever == "yes" and pollen:
                    top = max(pollen, key=lambda p: values.at[h, p] if pd.notna(values.at[h, p]) else -1)
                    v = values.at[h, top]
                    if pd.notna(v) and v > 0 and v >= values[top].quantile(0.75):
                        entry["reasons"].append(_reason("sandbox_more_pollen", sources.get(top), **{top: v}))
                entry["level"] = min(len(entry["reasons"]), 2)
            out.append(entry)
        return {
            "engine": f"{self.name}/{self.version}",
            "local_date": local_date.isoformat(),
            "timezone": location.timezone,
            "variables_used": sorted(set(required) | set(pollen if profile.hay_fever == "yes" else [])),
            "hours": out,
        }

    def choose(self, assessment: Assessment, options: WindowOptions) -> GuidanceResult:
        """Earliest start with the lowest level, where every hour the activity touches has data."""
        hours = {datetime.fromisoformat(h["start_utc"]): h for h in assessment["hours"]}
        duration = timedelta(minutes=options.duration_min)

        def level(start: datetime) -> int | None:
            """Highest level over the hours [start, start + duration) touches; None if one is missing."""
            first = start.replace(minute=0, second=0, microsecond=0)
            touched, h = [], first
            while h < start + duration:
                entry = hours.get(h)
                if entry is None or entry["status"] != "ok":
                    return None
                touched.append(entry)
                h += timedelta(hours=1)
            return max(e["level"] for e in touched)

        starts, s = [], options.earliest_start.astimezone(UTC)
        while s <= options.latest_start.astimezone(UTC):
            starts.append(s)
            s += STEP
        levels = [level(s) for s in starts]
        valid = [(lv, i) for i, lv in enumerate(levels) if lv is not None]
        example = Reason(**_reason("sandbox_example", "sandbox"))
        if not valid:
            return GuidanceResult("none", None, None, None, None, "suggested", [example], [])
        best, i = min(valid)
        j = i
        while j + 1 < len(starts) and levels[j + 1] is not None and levels[j + 1] <= best:
            j += 1
        start = starts[i]
        reasons = {}
        h = start.replace(minute=0, second=0, microsecond=0)
        while h < start + duration:
            for r in hours[h]["reasons"]:
                reasons.setdefault(r["code"], Reason(**r))
            h += timedelta(hours=1)
        return GuidanceResult(
            window_status="found",
            window_start=start,
            window_end=start + duration,
            latest_start=starts[j],
            intensity_hint=HINTS[best],
            indoor_alternative="suggested" if best == 2 else "not_suggested",
            reasons=[example, *reasons.values()],
            variables_used=assessment["variables_used"],
        )


def _reason(code: str, source: str | None, **params: float) -> dict:
    """A sandbox reason as stored in the assessment (JSON-friendly)."""
    return {
        "code": code,
        "source": source or "unknown",
        "params": {k: round(float(v), 1) for k, v in params.items()},
        "text": SANDBOX_TEXT,
    }
