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
from collections import Counter, deque
from dataclasses import dataclass, field
from datetime import UTC, datetime

_START_TIME = time.monotonic()
_lock = threading.Lock()
_total_requests = 0
_by_status_class: Counter[str] = Counter()
_errors_by_path: Counter[str] = Counter()

# Error counters are process-wide (every tenant shares them), so they are keyed
# by route *template* (`/transactions/{transaction_id}`), never by the raw URL:
# a raw URL would leak other tenants' resource ids and let anonymous callers
# grow memory without bound by requesting endless distinct paths.
MAX_TRACKED_PATHS = 200
OVERFLOW_PATH = "(other)"
UNMATCHED_PATH = "(unmatched)"


def route_label(request) -> str:
    """The request path with every path parameter replaced by its `{name}`.

    `/api/v1/transactions/<uuid>` becomes `/api/v1/transactions/{transaction_id}`.
    (Taken from the matched path parameters because the router's own template
    omits the `/api/v1` prefix.) A request that matched no route is labelled
    with a constant, so junk URLs collapse into one bucket.
    """
    if request.scope.get("route") is None:
        return UNMATCHED_PATH
    params = {str(value): name for name, value in request.scope.get("path_params", {}).items()}
    segments = request.url.path.split("/")
    return "/".join("{" + params[seg] + "}" if seg in params else seg for seg in segments)


def tracked_path_count() -> int:
    with _lock:
        return len(_errors_by_path)


@dataclass(frozen=True)
class RecentError:
    at: datetime
    status_code: int
    code: str
    path: str
    request_id: str | None


# Server-side failures only (5xx and unexpected exceptions): the "recent
# errors" a health view should show. Client mistakes (4xx) are not incidents.
_recent_errors: deque[RecentError] = deque(maxlen=50)


def record_error(*, status_code: int, code: str, path: str, request_id: str | None) -> None:
    with _lock:
        _recent_errors.appendleft(
            RecentError(datetime.now(UTC), status_code, code, path, request_id)
        )


def recent_errors(limit: int = 20) -> list[RecentError]:
    with _lock:
        return list(_recent_errors)[:limit]


def record_request(*, path: str, status_code: int) -> None:
    status_class = f"{status_code // 100}xx"
    with _lock:
        global _total_requests
        _total_requests += 1
        _by_status_class[status_class] += 1
        if status_code >= 400:
            key = (
                path
                if path in _errors_by_path or len(_errors_by_path) < MAX_TRACKED_PATHS
                else OVERFLOW_PATH
            )
            _errors_by_path[key] += 1


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
