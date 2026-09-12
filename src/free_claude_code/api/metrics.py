"""In-memory runtime metrics for admin dashboard and health."""

import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any


# Latency buckets in milliseconds
_LATENCY_BUCKETS_MS = (5, 10, 25, 50, 100, 250, 500, 1000, 2500, 5000, 10000)


@dataclass
class _PathStats:
    count: int = 0
    errors: int = 0
    total_ms: float = 0.0
    max_ms: float = 0.0
    min_ms: float = float("inf")
    buckets: dict[int, int] = field(
        default_factory=lambda: {b: 0 for b in _LATENCY_BUCKETS_MS}
    )
    overflow: int = 0  # > largest bucket


class RuntimeMetrics:
    """Thread-safe process metrics collector (single-process friendly)."""

    def __init__(self, recent_limit: int = 200) -> None:
        self._lock = threading.Lock()
        self._started_at = time.time()
        self._total_requests = 0
        self._total_errors = 0
        self._rate_limit_hits = 0
        self._by_path: dict[str, _PathStats] = defaultdict(_PathStats)
        self._by_status: dict[int, int] = defaultdict(int)
        self._recent: deque[dict[str, Any]] = deque(maxlen=recent_limit)
        self._provider_tests: dict[str, dict[str, Any]] = {}
        self._provider_latency: dict[str, _PathStats] = defaultdict(_PathStats)
        self._global_buckets: dict[int, int] = {b: 0 for b in _LATENCY_BUCKETS_MS}
        self._global_overflow = 0

    def record_request(
        self,
        *,
        method: str,
        path: str,
        status: int,
        duration_ms: float,
        client: str | None = None,
    ) -> None:
        """Record one completed HTTP request."""
        normalized = _normalize_path(path)
        is_error = status >= 400
        with self._lock:
            self._total_requests += 1
            if is_error:
                self._total_errors += 1
            self._by_status[status] += 1
            stats = self._by_path[f"{method.upper()} {normalized}"]
            stats.count += 1
            if is_error:
                stats.errors += 1
            stats.total_ms += duration_ms
            stats.max_ms = max(stats.max_ms, duration_ms)
            stats.min_ms = min(stats.min_ms, duration_ms)
            placed = False
            for bucket in _LATENCY_BUCKETS_MS:
                if duration_ms <= bucket:
                    stats.buckets[bucket] += 1
                    placed = True
                    break
            if not placed:
                stats.overflow += 1
            # Global latency histogram (all routes)
            g_placed = False
            for bucket in _LATENCY_BUCKETS_MS:
                if duration_ms <= bucket:
                    self._global_buckets[bucket] += 1
                    g_placed = True
                    break
            if not g_placed:
                self._global_overflow += 1
            self._recent.appendleft(
                {
                    "ts": time.time(),
                    "method": method.upper(),
                    "path": normalized,
                    "status": status,
                    "duration_ms": round(duration_ms, 2),
                    "client": (client or "")[:64] or None,
                }
            )

    def record_rate_limit_hit(self) -> None:
        with self._lock:
            self._rate_limit_hits += 1

    def record_provider_test(
        self, provider_id: str, *, ok: bool, message: str, latency_ms: float
    ) -> None:
        pid = provider_id[:64]
        with self._lock:
            self._provider_tests[pid] = {
                "ok": ok,
                "message": message[:300],
                "latency_ms": round(latency_ms, 2),
                "ts": time.time(),
            }
            # Rolling latency aggregate per provider (test path)
            stats = self._provider_latency[pid]
            stats.count += 1
            if not ok:
                stats.errors += 1
            stats.total_ms += latency_ms
            stats.max_ms = max(stats.max_ms, latency_ms)
            stats.min_ms = min(stats.min_ms, latency_ms)
            placed = False
            for bucket in _LATENCY_BUCKETS_MS:
                if latency_ms <= bucket:
                    stats.buckets[bucket] += 1
                    placed = True
                    break
            if not placed:
                stats.overflow += 1

    def record_provider_latency(
        self, provider_id: str, *, latency_ms: float, ok: bool = True
    ) -> None:
        """Record a provider-bound operation latency (proxy turn / test)."""
        pid = (provider_id or "unknown")[:64]
        with self._lock:
            stats = self._provider_latency[pid]
            stats.count += 1
            if not ok:
                stats.errors += 1
            stats.total_ms += latency_ms
            stats.max_ms = max(stats.max_ms, latency_ms)
            stats.min_ms = min(stats.min_ms, latency_ms)

    def snapshot(self) -> dict[str, Any]:
        """Return a JSON-serializable metrics snapshot."""
        with self._lock:
            uptime = max(0.0, time.time() - self._started_at)
            paths: list[dict[str, Any]] = []
            for key, stats in sorted(
                self._by_path.items(), key=lambda item: item[1].count, reverse=True
            ):
                avg = (stats.total_ms / stats.count) if stats.count else 0.0
                paths.append(
                    {
                        "route": key,
                        "count": stats.count,
                        "errors": stats.errors,
                        "avg_ms": round(avg, 2),
                        "max_ms": round(stats.max_ms if stats.count else 0.0, 2),
                        "min_ms": round(
                            stats.min_ms if stats.min_ms != float("inf") else 0.0, 2
                        ),
                        "buckets_ms": {
                            str(k): v for k, v in stats.buckets.items() if v
                        },
                        "overflow": stats.overflow,
                    }
                )
            rps = (self._total_requests / uptime) if uptime > 0 else 0.0
            providers: list[dict[str, Any]] = []
            for pid, stats in sorted(
                self._provider_latency.items(),
                key=lambda item: item[1].total_ms / item[1].count if item[1].count else 0,
                reverse=True,
            ):
                avg = (stats.total_ms / stats.count) if stats.count else 0.0
                providers.append(
                    {
                        "provider_id": pid,
                        "count": stats.count,
                        "errors": stats.errors,
                        "avg_ms": round(avg, 2),
                        "max_ms": round(stats.max_ms if stats.count else 0.0, 2),
                        "min_ms": round(
                            stats.min_ms if stats.min_ms != float("inf") else 0.0, 2
                        ),
                        "last_test": self._provider_tests.get(pid),
                    }
                )
            return {
                "uptime_seconds": round(uptime, 1),
                "started_at": self._started_at,
                "total_requests": self._total_requests,
                "total_errors": self._total_errors,
                "error_rate": round(
                    (self._total_errors / self._total_requests)
                    if self._total_requests
                    else 0.0,
                    4,
                ),
                "requests_per_second": round(rps, 3),
                "rate_limit_hits": self._rate_limit_hits,
                "status_codes": dict(sorted(self._by_status.items())),
                "latency_histogram_ms": {
                    str(k): v for k, v in self._global_buckets.items() if v
                },
                "latency_overflow": self._global_overflow,
                "top_routes": paths[:25],
                "provider_latency": providers[:30],
                "recent": list(self._recent)[:50],
                "provider_tests": dict(self._provider_tests),
            }


def _normalize_path(path: str) -> str:
    """Collapse dynamic path segments for aggregation (native ultra)."""
    from free_claude_code.native import normalize_path_key

    return normalize_path_key(path or "/")


# Process-wide singleton
metrics = RuntimeMetrics()
