"""In-process sliding-window rate limiting for the unauthenticated endpoints.

Deliberately small: a lock, a dict of timestamps per key. It slows credential
stuffing and signup spam against a single API process. It does *not* coordinate
across several processes or hosts - a multi-instance deployment needs limiting
at the edge (reverse proxy / WAF) as well, and the README says so.
"""

from __future__ import annotations

import math
import threading
import time
from collections import deque


class SlidingWindowLimiter:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._hits: dict[str, deque[float]] = {}

    def check(self, key: str, *, limit: int, window_seconds: float) -> tuple[bool, int]:
        """Record a hit. Returns (allowed, retry_after_seconds)."""
        now = time.monotonic()
        cutoff = now - window_seconds
        with self._lock:
            hits = self._hits.setdefault(key, deque())
            while hits and hits[0] <= cutoff:
                hits.popleft()
            if len(hits) >= limit:
                return False, max(1, math.ceil(hits[0] + window_seconds - now))
            hits.append(now)
            # Opportunistic cleanup so abandoned keys do not accumulate forever.
            if len(self._hits) > 10_000:
                for stale in [k for k, v in self._hits.items() if not v or v[-1] <= cutoff]:
                    del self._hits[stale]
            return True, 0

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


limiter = SlidingWindowLimiter()
