"""In-process request metrics.

Deliberately not Prometheus: a single-process counter behind a lock,
reset on restart, exposed as one JSON endpoint. That's the right amount of
observability for a service with no multi-instance deployment story - a
metrics *exporter* would be infrastructure for a scaling problem this
project doesn't have. What it answers honestly: is this process seeing
errors, and where.
"""

import threading
import time
from collections import Counter
from dataclasses import dataclass, field

_START_TIME = time.monotonic()
_lock = threading.Lock()
_total_requests = 0
_by_status_class: Counter[str] = Counter()
_errors_by_path: Counter[str] = Counter()


def record_request(*, path: str, status_code: int) -> None:
    status_class = f"{status_code // 100}xx"
    with _lock:
        global _total_requests
        _total_requests += 1
        _by_status_class[status_class] += 1
        if status_code >= 400:
            _errors_by_path[path] += 1


@dataclass(frozen=True)
class MetricsSnapshot:
    uptime_seconds: float
    total_requests: int
    requests_by_status_class: dict[str, int] = field(default_factory=dict)
    top_error_paths: list[tuple[str, int]] = field(default_factory=list)


def snapshot(*, top_n: int = 10) -> MetricsSnapshot:
    with _lock:
        return MetricsSnapshot(
            uptime_seconds=round(time.monotonic() - _START_TIME, 1),
            total_requests=_total_requests,
            requests_by_status_class=dict(_by_status_class),
            top_error_paths=_errors_by_path.most_common(top_n),
        )


def error_count_for(path: str) -> int:
    """Exact per-path error count, independent of the top-N ranking
    `snapshot()` exposes - the counter itself, not a discoverability view
    over it that unrelated traffic could push a path out of.
    """
    with _lock:
        return _errors_by_path[path]
