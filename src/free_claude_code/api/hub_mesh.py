"""Multi-hub federation mesh — register hubs, push/pull fan-in snapshots."""

from __future__ import annotations

import threading
import time
from typing import Any

MAX_HUBS = 16
STALE_AFTER_SEC = 600.0


class HubMeshRegistry:
    """In-process registry of peer admin hubs and their last fan-in summaries."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._hubs: dict[str, dict[str, Any]] = {}

    def register(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise ValueError("payload must be object")
        hub_id = str(payload.get("hub_id") or "").strip()[:64]
        if not hub_id:
            raise ValueError("hub_id required")
        base_url = str(payload.get("base_url") or "").strip()[:512]
        entry = {
            "hub_id": hub_id,
            "base_url": base_url or None,
            "version": str(payload.get("version") or "")[:64] or None,
            "nodes_tracked": int(payload.get("nodes_tracked") or 0),
            "received_at": time.time(),
            "summary": payload.get("summary")
            if isinstance(payload.get("summary"), dict)
            else None,
            "label": str(payload.get("label") or "")[:64] or None,
        }
        with self._lock:
            self._evict_unlocked()
            if hub_id not in self._hubs and len(self._hubs) >= MAX_HUBS:
                oldest = min(self._hubs.values(), key=lambda h: h.get("received_at") or 0)
                self._hubs.pop(str(oldest.get("hub_id")), None)
            self._hubs[hub_id] = entry
            return {
                "ok": True,
                "hub_id": hub_id,
                "hubs_tracked": len(self._hubs),
            }

    def _evict_unlocked(self) -> None:
        now = time.time()
        stale = [
            hid
            for hid, h in self._hubs.items()
            if (now - float(h.get("received_at") or 0)) > STALE_AFTER_SEC
        ]
        for hid in stale:
            self._hubs.pop(hid, None)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            self._evict_unlocked()
            hubs = [dict(h) for h in self._hubs.values()]
        hubs.sort(key=lambda h: str(h.get("hub_id") or ""))
        for h in hubs:
            h["age_seconds"] = round(
                max(0.0, time.time() - float(h.get("received_at") or time.time())), 1
            )
        return {
            "format": "fcc-hub-mesh",
            "format_version": 1,
            "hubs_tracked": len(hubs),
            "hubs": hubs,
            "ts": time.time(),
        }

    def clear(self) -> None:
        with self._lock:
            self._hubs.clear()


hub_mesh = HubMeshRegistry()
