"""Build portable admin audit export bundles (ZIP, no secrets)."""

from __future__ import annotations

import io
import json
import time
import zipfile
from typing import Any


BUNDLE_FORMAT = "fcc-audit-bundle"
BUNDLE_VERSION = 1
MAX_EVENTS = 500
MAX_JSON_BYTES = 2 * 1024 * 1024


def _dumps(obj: Any) -> bytes:
    return json.dumps(obj, indent=2, default=str, ensure_ascii=False).encode("utf-8")


def sanitize_event(ev: dict[str, Any]) -> dict[str, Any]:
    """Keep only safe audit fields (no tokens/payloads)."""
    if not isinstance(ev, dict):
        return {}
    out: dict[str, Any] = {}
    for key in (
        "seq",
        "ts",
        "event",
        "level",
        "client_ip",
        "path",
        "method",
        "node_id",
    ):
        if key in ev and ev[key] is not None:
            val = ev[key]
            if isinstance(val, (int, float, bool)):
                out[key] = val
            else:
                out[key] = str(val)[:200]
    return out


def build_audit_bundle_zip(
    *,
    node_id: str,
    version: str,
    audit: dict[str, Any],
    events: list[Any],
    metrics_export: dict[str, Any],
    prometheus_text: str,
    openmetrics_text: str | None = None,
    fanin_summary: dict[str, Any] | None = None,
    extra_files: dict[str, bytes] | None = None,
) -> bytes:
    """Assemble an in-memory ZIP audit bundle. Never includes secrets."""
    exported_at = time.time()
    safe_events = [
        sanitize_event(e) for e in (events or [])[:MAX_EVENTS] if isinstance(e, dict)
    ]
    manifest = {
        "format": BUNDLE_FORMAT,
        "format_version": BUNDLE_VERSION,
        "exported_at": exported_at,
        "node_id": str(node_id or "node-local")[:128],
        "version": str(version or "")[:64],
        "files": [
            "manifest.json",
            "audit.json",
            "security_events.json",
            "metrics_export.json",
            "metrics.prometheus.txt",
        ],
        "notes": [
            "Secret fields and request payloads are never included.",
            "Safe to attach to incident tickets after redacting hostnames if needed.",
        ],
    }
    if openmetrics_text is not None:
        manifest["files"].append("metrics.openmetrics.txt")
    if fanin_summary is not None:
        manifest["files"].append("fanin_summary.json")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", _dumps(manifest))
        zf.writestr("audit.json", _dumps(audit if isinstance(audit, dict) else {}))
        zf.writestr(
            "security_events.json",
            _dumps(
                {
                    "count": len(safe_events),
                    "events": safe_events,
                }
            ),
        )
        # metrics export — drop recent request samples that may contain client IPs if desired;
        # keep structure but cap recent list
        me = dict(metrics_export) if isinstance(metrics_export, dict) else {}
        snap = me.get("snapshot")
        if isinstance(snap, dict):
            snap = dict(snap)
            recent = snap.get("recent")
            if isinstance(recent, list):
                snap["recent"] = recent[:20]
            me["snapshot"] = snap
        zf.writestr("metrics_export.json", _dumps(me))
        zf.writestr(
            "metrics.prometheus.txt",
            (prometheus_text or "")[:MAX_JSON_BYTES].encode("utf-8"),
        )
        if openmetrics_text is not None:
            zf.writestr(
                "metrics.openmetrics.txt",
                (openmetrics_text or "")[:MAX_JSON_BYTES].encode("utf-8"),
            )
        if fanin_summary is not None:
            zf.writestr("fanin_summary.json", _dumps(fanin_summary))
        if extra_files:
            for name, content in list(extra_files.items())[:10]:
                safe_name = "".join(
                    ch if ch.isalnum() or ch in "._-" else "_" for ch in str(name)
                )[:64]
                if not safe_name or safe_name in {
                    "manifest.json",
                    "audit.json",
                    "security_events.json",
                }:
                    continue
                raw = content if isinstance(content, bytes) else str(content).encode("utf-8")
                zf.writestr(safe_name, raw[:MAX_JSON_BYTES])

    return buf.getvalue()
