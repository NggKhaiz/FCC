"""OpenMetrics text exposition (1.0.0) built on Prometheus renderer."""

from __future__ import annotations

from typing import Any

from .prometheus_export import render_prometheus_text


def render_openmetrics_text(
    snapshot: dict[str, Any],
    *,
    namespace: str = "fcc",
    node_id: str = "local",
    version: str = "",
) -> str:
    """Render OpenMetrics 1.0.0 text format.

    OpenMetrics is a strict superset of Prometheus text with:
    - ``# EOF`` terminator
    - optional ``# TYPE … …`` already present from Prometheus path
    - no trailing blank-only noise before EOF
    """
    body = render_prometheus_text(
        snapshot,
        namespace=namespace,
        node_id=node_id,
        version=version,
    )
    # Strip trailing blank lines, append OpenMetrics EOF marker
    lines = [ln for ln in body.splitlines() if ln.strip() != "" or ln.startswith("#")]
    # Keep structure; just ensure EOF
    while lines and lines[-1] == "":
        lines.pop()
    lines.append("# EOF")
    return "\n".join(lines) + "\n"
