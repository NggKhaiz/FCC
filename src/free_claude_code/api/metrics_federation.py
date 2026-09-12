"""Merge multi-node FCC metrics federation exports (pure, no side effects)."""

from typing import Any


def merge_metric_exports(nodes: list[Any]) -> dict[str, Any]:
    """Aggregate federation payloads from several FCC processes.

    Each node should look like the ``/admin/api/metrics/export`` response
    (or a raw snapshot). Secrets must never appear in snapshots.
    """
    total_requests = 0
    total_errors = 0
    rate_limit_hits = 0
    status_codes: dict[int, int] = {}
    hist: dict[str, int] = {}
    overflow = 0
    provider_acc: dict[str, dict[str, float | int]] = {}
    route_acc: dict[str, dict[str, float | int]] = {}
    node_summaries: list[dict[str, Any]] = []
    accepted = 0

    for raw in nodes:
        if not isinstance(raw, dict):
            continue
        snap = raw.get("snapshot") if isinstance(raw.get("snapshot"), dict) else raw
        if not isinstance(snap, dict):
            continue
        accepted += 1
        total_requests += int(snap.get("total_requests") or 0)
        total_errors += int(snap.get("total_errors") or 0)
        rate_limit_hits += int(snap.get("rate_limit_hits") or 0)
        for k, v in (snap.get("status_codes") or {}).items():
            try:
                status_codes[int(k)] = status_codes.get(int(k), 0) + int(v)
            except (TypeError, ValueError):
                continue
        for k, v in (snap.get("latency_histogram_ms") or {}).items():
            try:
                hist[str(k)] = hist.get(str(k), 0) + int(v)
            except (TypeError, ValueError):
                continue
        overflow += int(snap.get("latency_overflow") or 0)

        for row in snap.get("provider_latency") or []:
            if not isinstance(row, dict):
                continue
            pid = str(row.get("provider_id") or "")[:64]
            if not pid:
                continue
            acc = provider_acc.setdefault(
                pid, {"count": 0, "errors": 0, "total_ms": 0.0, "max_ms": 0.0}
            )
            count = int(row.get("count") or 0)
            avg = float(row.get("avg_ms") or 0.0)
            acc["count"] = int(acc["count"]) + count
            acc["errors"] = int(acc["errors"]) + int(row.get("errors") or 0)
            acc["total_ms"] = float(acc["total_ms"]) + avg * count
            acc["max_ms"] = max(float(acc["max_ms"]), float(row.get("max_ms") or 0.0))

        for row in snap.get("top_routes") or []:
            if not isinstance(row, dict):
                continue
            route = str(row.get("route") or "")[:128]
            if not route:
                continue
            acc = route_acc.setdefault(
                route, {"count": 0, "errors": 0, "total_ms": 0.0, "max_ms": 0.0}
            )
            count = int(row.get("count") or 0)
            avg = float(row.get("avg_ms") or 0.0)
            acc["count"] = int(acc["count"]) + count
            acc["errors"] = int(acc["errors"]) + int(row.get("errors") or 0)
            acc["total_ms"] = float(acc["total_ms"]) + avg * count
            acc["max_ms"] = max(float(acc["max_ms"]), float(row.get("max_ms") or 0.0))

        node_summaries.append(
            {
                "node_id": raw.get("node_id") or snap.get("node_id") or f"node-{accepted}",
                "version": raw.get("version"),
                "total_requests": int(snap.get("total_requests") or 0),
                "uptime_seconds": snap.get("uptime_seconds"),
                "requests_per_second": snap.get("requests_per_second"),
            }
        )

    providers = []
    for pid, acc in provider_acc.items():
        count = int(acc["count"]) or 1
        providers.append(
            {
                "provider_id": pid,
                "count": int(acc["count"]),
                "errors": int(acc["errors"]),
                "avg_ms": round(float(acc["total_ms"]) / count, 2),
                "max_ms": round(float(acc["max_ms"]), 2),
            }
        )
    providers.sort(key=lambda r: r["avg_ms"], reverse=True)

    routes = []
    for route, acc in route_acc.items():
        count = int(acc["count"]) or 1
        routes.append(
            {
                "route": route,
                "count": int(acc["count"]),
                "errors": int(acc["errors"]),
                "avg_ms": round(float(acc["total_ms"]) / count, 2),
                "max_ms": round(float(acc["max_ms"]), 2),
            }
        )
    routes.sort(key=lambda r: r["count"], reverse=True)

    error_rate = (total_errors / total_requests) if total_requests else 0.0
    return {
        "format": "fcc-metrics-federation-merged",
        "format_version": 1,
        "nodes_accepted": accepted,
        "nodes": node_summaries,
        "total_requests": total_requests,
        "total_errors": total_errors,
        "error_rate": round(error_rate, 4),
        "rate_limit_hits": rate_limit_hits,
        "status_codes": dict(sorted(status_codes.items())),
        "latency_histogram_ms": hist,
        "latency_overflow": overflow,
        "provider_latency": providers[:30],
        "top_routes": routes[:25],
    }
