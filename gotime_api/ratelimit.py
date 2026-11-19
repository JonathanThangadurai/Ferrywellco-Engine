"""Rate limiting per API key: a token bucket in memory.

Each key gets `per_minute` tokens a minute and can save up `burst`. With several API copies each keeps
its own buckets; a shared counter (e.g. Redis) comes when we run more than one copy (api-design.md).
"""

import math
import threading
import time

from .problems import Problem


class RateLimiter:
    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self._buckets: dict[str, tuple[float, float]] = {}  # key_id -> (tokens, last time)
        self._lock = threading.Lock()

    def check(self, key_id: str, per_minute: int, burst: int) -> None:
        """Take one token, or raise 429 with Retry-After (seconds until the next token)."""
        rate = per_minute / 60.0
        now = self._clock()
        with self._lock:
            tokens, last = self._buckets.get(key_id, (float(burst), now))
            tokens = min(float(burst), tokens + (now - last) * rate)
            if tokens < 1:
                self._buckets[key_id] = (tokens, now)
                wait = math.ceil((1 - tokens) / rate)
                raise Problem(429, "rate-limited", headers={"Retry-After": str(max(wait, 1))})
            self._buckets[key_id] = (tokens - 1, now)
