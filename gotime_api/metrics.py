"""Basic metrics as a JSON log line per worker every minute: request count, latency, errors, data age.

No metrics endpoint and no extra service: each worker logs its own numbers for the last interval
("msg": "metrics"); add the lines up across workers to get totals.
"""

import statistics
import threading
from datetime import datetime


class Metrics:
    def __init__(self):
        self._lock = threading.Lock()
        self._reset()

    def _reset(self) -> None:
        self.by_class: dict[str, int] = {}
        self.latencies: list[float] = []

    def record(self, status: int, latency_ms: float) -> None:
        with self._lock:
            key = f"{status // 100}xx"
            self.by_class[key] = self.by_class.get(key, 0) + 1
            self.latencies.append(latency_ms)

    def flush(self, snapshots: dict, now_utc: datetime) -> dict:
        """Numbers since the last flush, plus the data age per source of each served snapshot."""
        with self._lock:
            lat, by_class = self.latencies, self.by_class
            self._reset()
        out = {"requests": len(lat), "by_status": by_class, "errors_5xx": by_class.get("5xx", 0)}
        if len(lat) >= 2:
            q = statistics.quantiles(lat, n=20)
            out.update(latency_p50_ms=round(statistics.median(lat), 2), latency_p95_ms=round(q[18], 2))
        ages = {}
        for mode, loaded in snapshots.items():
            if loaded is None:
                continue
            for loc in loaded.snap["locations"]:
                for s in loc["sources"]:
                    age = (now_utc - datetime.fromisoformat(s["issue_time_utc"])).total_seconds() / 3600
                    key = f"{mode}.{s['source']}"
                    ages[key] = round(max(age, ages.get(key, 0.0)), 1)
        out["data_age_h"] = ages
        return out
