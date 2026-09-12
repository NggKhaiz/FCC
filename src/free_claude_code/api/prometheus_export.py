"""Render FCC runtime metrics as Prometheus text exposition (0.0.4)."""

from __future__ import annotations

from typing import Any


def _esc_label(value: str) -> str:
    """Escape a Prometheus label value."""
    return (
        str(value)
        .replace("\\", "\\\\")
        .replace("\n", "\\n")
        .replace('"', '\\"')
    )[:256]


def _metric_name(name: str) -> str:
    out = []
    for ch in name:
        if ch.isalnum() or ch == "_":
            out.append(ch)
        else:
            out.append("_")
    s = "".join(out).strip("_") or "metric"
    if s[0].isdigit():
        s = f"m_{s}"
    return s[:128]


def render_prometheus_text(
    snapshot: dict[str, Any],
    *,
    namespace: str = "fcc",
    node_id: str = "local",
    version: str = "",
) -> str:
    """Convert a RuntimeMetrics.snapshot() dict to Prometheus text format.

    Produces counters/gauges/histograms suitable for Prometheus scrape.
    No secrets are emitted.
    """
    ns = _metric_name(namespace or "fcc")
    node = _esc_label(node_id or "local")
    lines: list[str] = [
        f"# HELP {ns}_info FCC process info",
        f"# TYPE {ns}_info gauge",
        f'{ns}_info{{node_id="{node}",version="{_esc_label(version)}"}} 1',
    ]

    def _lab(*pairs: tuple[str, str]) -> str:
        parts = [f'node_id="{node}"']
        for k, v in pairs:
            parts.append(f'{_metric_name(k)}="{_esc_label(v)}"')
        return "{" + ",".join(parts) + "}"

    def gauge(name: str, help_text: str, value: float, *label_pairs: tuple[str, str]) -> None:
        m = f"{ns}_{_metric_name(name)}"
        lines.append(f"# HELP {m} {help_text}")
        lines.append(f"# TYPE {m} gauge")
        lines.append(f"{m}{_lab(*label_pairs)} {float(value)}")

    def counter(name: str, help_text: str, value: float, *label_pairs: tuple[str, str]) -> None:
        m = f"{ns}_{_metric_name(name)}"
        lines.append(f"# HELP {m} {help_text}")
        lines.append(f"# TYPE {m} counter")
        lines.append(f"{m}{_lab(*label_pairs)} {float(value)}")

    snap = snapshot if isinstance(snapshot, dict) else {}

    gauge("uptime_seconds", "Process uptime in seconds", float(snap.get("uptime_seconds") or 0))
    counter(
        "requests_total",
        "Total HTTP requests handled",
        float(snap.get("total_requests") or 0),
    )
    counter(
        "errors_total",
        "Total HTTP responses with status >= 400",
        float(snap.get("total_errors") or 0),
    )
    gauge("error_rate", "Error rate (errors/requests)", float(snap.get("error_rate") or 0))
    gauge(
        "requests_per_second",
        "Approximate lifetime RPS",
        float(snap.get("requests_per_second") or 0),
    )
    counter(
        "rate_limit_hits_total",
        "Rate-limit rejections",
        float(snap.get("rate_limit_hits") or 0),
    )
    counter(
        "latency_overflow_total",
        "Requests slower than largest latency bucket",
        float(snap.get("latency_overflow") or 0),
    )

    status_codes = snap.get("status_codes") or {}
    if isinstance(status_codes, dict) and status_codes:
        m = f"{ns}_http_responses_total"
        lines.append(f"# HELP {m} HTTP responses by status code")
        lines.append(f"# TYPE {m} counter")
        for code, count in sorted(
            status_codes.items(),
            key=lambda kv: int(kv[0]) if str(kv[0]).isdigit() else 0,
        ):
            try:
                c = int(code)
                n = int(count)
            except (TypeError, ValueError):
                continue
            lines.append(f'{m}{_lab(("code", str(c)))} {n}')

    hist = snap.get("latency_histogram_ms") or {}
    if isinstance(hist, dict) and hist:
        m = f"{ns}_request_latency_ms"
        lines.append(f"# HELP {m} Request latency histogram (milliseconds)")
        lines.append(f"# TYPE {m} histogram")
        buckets: list[tuple[float, int]] = []
        for k, v in hist.items():
            try:
                buckets.append((float(k), int(v)))
            except (TypeError, ValueError):
                continue
        buckets.sort(key=lambda x: x[0])
        cumulative = 0
        for le, count in buckets:
            cumulative += count
            lines.append(f'{m}_bucket{_lab(("le", str(le)))} {cumulative}')
        overflow = int(snap.get("latency_overflow") or 0)
        total = cumulative + overflow
        lines.append(f'{m}_bucket{_lab(("le", "+Inf"))} {total}')
        lines.append(f'{m}_count{_lab()} {total}')
        if total == 0:
            lines.append(f'{m}_sum{_lab()} 0')

    providers = snap.get("provider_latency") or []
    if isinstance(providers, list) and providers:
        m_avg = f"{ns}_provider_latency_avg_ms"
        m_max = f"{ns}_provider_latency_max_ms"
        m_cnt = f"{ns}_provider_requests_total"
        m_err = f"{ns}_provider_errors_total"
        lines.append(f"# HELP {m_avg} Average provider latency in milliseconds")
        lines.append(f"# TYPE {m_avg} gauge")
        lines.append(f"# HELP {m_max} Max provider latency in milliseconds")
        lines.append(f"# TYPE {m_max} gauge")
        lines.append(f"# HELP {m_cnt} Provider-bound request count")
        lines.append(f"# TYPE {m_cnt} counter")
        lines.append(f"# HELP {m_err} Provider-bound error count")
        lines.append(f"# TYPE {m_err} counter")
        for row in providers:
            if not isinstance(row, dict):
                continue
            pid = str(row.get("provider_id") or "")[:64]
            if not pid:
                continue
            lab = (("provider", pid),)
            lines.append(f'{m_avg}{_lab(*lab)} {float(row.get("avg_ms") or 0)}')
            lines.append(f'{m_max}{_lab(*lab)} {float(row.get("max_ms") or 0)}')
            lines.append(f'{m_cnt}{_lab(*lab)} {float(row.get("count") or 0)}')
            lines.append(f'{m_err}{_lab(*lab)} {float(row.get("errors") or 0)}')

    routes = snap.get("top_routes") or []
    if isinstance(routes, list) and routes:
        m = f"{ns}_route_requests_total"
        m_err = f"{ns}_route_errors_total"
        m_avg = f"{ns}_route_latency_avg_ms"
        lines.append(f"# HELP {m} Requests by route")
        lines.append(f"# TYPE {m} counter")
        lines.append(f"# HELP {m_err} Errors by route")
        lines.append(f"# TYPE {m_err} counter")
        lines.append(f"# HELP {m_avg} Average latency by route (ms)")
        lines.append(f"# TYPE {m_avg} gauge")
        for row in routes[:40]:
            if not isinstance(row, dict):
                continue
            route = str(row.get("route") or "")[:128]
            if not route:
                continue
            lab = (("route", route),)
            lines.append(f'{m}{_lab(*lab)} {float(row.get("count") or 0)}')
            lines.append(f'{m_err}{_lab(*lab)} {float(row.get("errors") or 0)}')
            lines.append(f'{m_avg}{_lab(*lab)} {float(row.get("avg_ms") or 0)}')

    lines.append("")
    return "\n".join(lines)
