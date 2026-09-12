"""Multi-hub federation mesh — register hubs, per-hub tokens, pull targets."""

from __future__ import annotations

import threading
import time
from typing import Any

MAX_HUBS = 16
STALE_AFTER_SEC = 600.0
MAX_TOKEN_LEN = 512


class HubMeshRegistry:
    """In-process registry of peer admin hubs and their last fan-in summaries.

    Per-hub admin tokens are stored only in memory (never returned by snapshot).
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._hubs: dict[str, dict[str, Any]] = {}
        self._tokens: dict[str, str] = {}

    def register(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise ValueError("payload must be object")
        hub_id = str(payload.get("hub_id") or "").strip()[:64]
        if not hub_id:
            raise ValueError("hub_id required")
        base_url = str(payload.get("base_url") or "").strip()[:512]
        token_raw = payload.get("token")
        token: str | None = None
        if token_raw is not None:
            token = str(token_raw).strip()[:MAX_TOKEN_LEN]
            if token == "":
                token = None  # explicit clear

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
                old_id = str(oldest.get("hub_id") or "")
                self._hubs.pop(old_id, None)
                self._tokens.pop(old_id, None)
            # preserve existing base_url if not provided on refresh
            prev = self._hubs.get(hub_id)
            if prev and not entry["base_url"] and prev.get("base_url"):
                entry["base_url"] = prev.get("base_url")
            self._hubs[hub_id] = entry
            if "token" in payload:
                if token:
                    self._tokens[hub_id] = token
                else:
                    self._tokens.pop(hub_id, None)
            return {
                "ok": True,
                "hub_id": hub_id,
                "hubs_tracked": len(self._hubs),
                "token_set": hub_id in self._tokens,
            }

    def set_token(self, hub_id: str, token: str | None) -> dict[str, Any]:
        hid = str(hub_id or "").strip()[:64]
        if not hid:
            raise ValueError("hub_id required")
        with self._lock:
            if hid not in self._hubs:
                raise ValueError("unknown hub_id")
            if token:
                self._tokens[hid] = str(token).strip()[:MAX_TOKEN_LEN]
            else:
                self._tokens.pop(hid, None)
            return {"ok": True, "hub_id": hid, "token_set": hid in self._tokens}

    def get_token(self, hub_id: str) -> str | None:
        with self._lock:
            return self._tokens.get(str(hub_id))

    def _evict_unlocked(self) -> None:
        now = time.time()
        stale = [
            hid
            for hid, h in self._hubs.items()
            if (now - float(h.get("received_at") or 0)) > STALE_AFTER_SEC
        ]
        for hid in stale:
            self._hubs.pop(hid, None)
            self._tokens.pop(hid, None)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            self._evict_unlocked()
            hubs = []
            for h in self._hubs.values():
                row = dict(h)
                hid = str(row.get("hub_id") or "")
                row["token_set"] = hid in self._tokens
                # never expose token value
                hubs.append(row)
        hubs.sort(key=lambda h: str(h.get("hub_id") or ""))
        for h in hubs:
            h["age_seconds"] = round(
                max(0.0, time.time() - float(h.get("received_at") or time.time())), 1
            )
        return {
            "format": "fcc-hub-mesh",
            "format_version": 2,
            "hubs_tracked": len(hubs),
            "hubs": hubs,
            "ts": time.time(),
        }

    def clear(self) -> None:
        with self._lock:
            self._hubs.clear()
            self._tokens.clear()

    def pull_targets(self, *, limit: int = 16) -> list[dict[str, Any]]:
        """Return hubs that have a scrapeable base_url (includes token if set)."""
        limit = max(1, min(int(limit or 16), MAX_HUBS))
        with self._lock:
            self._evict_unlocked()
            items = list(self._hubs.values())
            tokens = dict(self._tokens)
        out: list[dict[str, Any]] = []
        for h in sorted(items, key=lambda x: str(x.get("hub_id") or "")):
            base = h.get("base_url")
            hub_id = h.get("hub_id")
            if not base or not hub_id:
                continue
            hid = str(hub_id)[:64]
            row: dict[str, Any] = {
                "hub_id": hid,
                "base_url": str(base)[:512],
                "token_set": hid in tokens,
            }
            tok = tokens.get(hid)
            if tok:
                row["token"] = tok
            out.append(row)
            if len(out) >= limit:
                break
        return out


hub_mesh = HubMeshRegistry()
