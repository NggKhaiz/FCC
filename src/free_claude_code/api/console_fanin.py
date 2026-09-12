"""Multi-replica admin console fan-in hub (in-process, pure merge helpers)."""

from __future__ import annotations

import threading
import time
from typing import Any

from .audit_bundle import sanitize_event

MAX_NODES = 32
MAX_EVENTS_PER_NODE = 200
MAX_NODE_ID_LEN = 64
STALE_AFTER_SEC = 300.0


class ConsoleFanInHub:
    """Thread-safe store of latest security-event batches from peer FCC nodes.

    Hub role: one admin process collects exports from replicas and serves a
    merged live tail (WebSocket ``fanin`` channel / HTTP snapshot).
    Does not open outbound connections — peers push or operator posts exports.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._nodes: dict[str, dict[str, Any]] = {}

    def ingest(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Accept one node export. Returns accepted summary."""
        if not isinstance(payload, dict):
            raise ValueError("payload must be object")
        node_id = str(payload.get("node_id") or "").strip()[:MAX_NODE_ID_LEN]
        if not node_id:
            raise ValueError("node_id required")
        events_raw = payload.get("events")
        if events_raw is None and isinstance(payload.get("snapshot"), dict):
            events_raw = payload["snapshot"].get("events")
        if not isinstance(events_raw, list):
            events_raw = []
        events = [
            sanitize_event(e)
            for e in events_raw[:MAX_EVENTS_PER_NODE]
            if isinstance(e, dict)
        ]
        # newest-first preferred; tag node
        for ev in events:
            ev.setdefault("node_id", node_id)
        entry = {
            "node_id": node_id,
            "version": str(payload.get("version") or "")[:64] or None,
            "received_at": time.time(),
            "exported_at": payload.get("exported_at"),
            "latest_seq": int(payload.get("latest_seq") or 0),
            "event_count": len(events),
            "events": events,
        }
        with self._lock:
            # evict stale before insert if at capacity
            self._evict_unlocked()
            if node_id not in self._nodes and len(self._nodes) >= MAX_NODES:
                # drop oldest received
                oldest = min(self._nodes.values(), key=lambda n: n.get("received_at") or 0)
                self._nodes.pop(str(oldest.get("node_id")), None)
            self._nodes[node_id] = entry
            return {
                "ok": True,
                "node_id": node_id,
                "event_count": len(events),
                "nodes_tracked": len(self._nodes),
            }

    def _evict_unlocked(self) -> None:
        now = time.time()
        stale = [
            nid
            for nid, n in self._nodes.items()
            if (now - float(n.get("received_at") or 0)) > STALE_AFTER_SEC
        ]
        for nid in stale:
            self._nodes.pop(nid, None)

    def snapshot(self, *, limit: int = 100) -> dict[str, Any]:
        """Merged newest-first events across nodes + node roster."""
        limit = max(1, min(int(limit or 100), 500))
        with self._lock:
            self._evict_unlocked()
            nodes = [dict(n) for n in self._nodes.values()]
        # merge events
        merged: list[dict[str, Any]] = []
        for n in nodes:
            for ev in n.get("events") or []:
                if isinstance(ev, dict):
                    row = dict(ev)
                    row.setdefault("node_id", n.get("node_id"))
                    merged.append(row)
        # sort by ts desc then seq desc
        def _key(e: dict[str, Any]) -> tuple:
            return (float(e.get("ts") or 0), int(e.get("seq") or 0))

        merged.sort(key=_key, reverse=True)
        roster = [
            {
                "node_id": n.get("node_id"),
                "version": n.get("version"),
                "received_at": n.get("received_at"),
                "exported_at": n.get("exported_at"),
                "latest_seq": n.get("latest_seq"),
                "event_count": n.get("event_count"),
                "age_seconds": round(
                    max(0.0, time.time() - float(n.get("received_at") or time.time())), 1
                ),
            }
            for n in sorted(nodes, key=lambda x: str(x.get("node_id") or ""))
        ]
        return {
            "format": "fcc-console-fanin",
            "format_version": 1,
            "nodes_tracked": len(roster),
            "nodes": roster,
            "events": merged[:limit],
            "ts": time.time(),
        }

    def summary(self) -> dict[str, Any]:
        snap = self.snapshot(limit=1)
        return {
            "nodes_tracked": snap["nodes_tracked"],
            "nodes": snap["nodes"],
            "ts": snap["ts"],
        }

    def clear(self) -> None:
        with self._lock:
            self._nodes.clear()


def merge_event_exports(nodes: list[Any], *, limit: int = 200) -> dict[str, Any]:
    """Pure merge of security-event export payloads (no hub mutation)."""
    hub = ConsoleFanInHub()
    accepted = 0
    errors: list[str] = []
    for raw in (nodes or [])[:MAX_NODES]:
        if not isinstance(raw, dict):
            continue
        try:
            hub.ingest(raw)
            accepted += 1
        except ValueError as exc:
            errors.append(str(exc)[:120])
    out = hub.snapshot(limit=limit)
    out["nodes_accepted"] = accepted
    if errors:
        out["errors"] = errors[:10]
    return out


def export_local_events(
    *,
    node_id: str,
    version: str,
    events: list[Any],
    latest_seq: int,
) -> dict[str, Any]:
    """Build a portable security-events export for fan-in / bundle."""
    safe = [
        sanitize_event(e) for e in (events or [])[:MAX_EVENTS_PER_NODE] if isinstance(e, dict)
    ]
    return {
        "format": "fcc-security-events",
        "format_version": 1,
        "exported_at": time.time(),
        "node_id": str(node_id or "node-local")[:MAX_NODE_ID_LEN],
        "version": str(version or "")[:64],
        "latest_seq": int(latest_seq or 0),
        "events": safe,
    }


# Process-wide hub for this FCC instance (hub role optional)
fanin_hub = ConsoleFanInHub()
