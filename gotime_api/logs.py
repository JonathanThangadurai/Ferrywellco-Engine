"""Structured JSON logs: one line per event.

Never logged: coordinates, request bodies, API keys or client IPs. A request line carries request_id,
key_id, route, status, problem code, latency, location_id, distance_km, snapshot_id and mode only.
Pass fields with `log.info("msg", extra={"fields": {...}})`.
"""

import json
import logging
import sys
from datetime import UTC, datetime

ACCESS = logging.getLogger("gotime_api.access")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        line = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname.lower(),
            "logger": record.name,
            "msg": record.getMessage(),
            **getattr(record, "fields", {}),
        }
        if record.exc_info:
            line["exc"] = self.formatException(record.exc_info)
        return json.dumps(line, default=str)


def setup_logging(level: int = logging.INFO) -> None:
    """Send all logs to stdout as JSON lines (Docker collects stdout)."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    logging.getLogger("uvicorn.access").disabled = True  # our own request line replaces it (it logs IPs)
