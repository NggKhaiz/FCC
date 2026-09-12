"""Bidirectional admin WebSocket console protocol (pure helpers)."""

from __future__ import annotations

import json
import time
from typing import Any

PROTOCOL_VERSION = 1
MAX_MESSAGE_BYTES = 8 * 1024
MAX_SUBSCRIBE = 8
ALLOWED_CHANNELS = frozenset({"security", "metrics", "system", "fanin"})


def dumps(payload: dict[str, Any]) -> str:
    return json.dumps(payload, separators=(",", ":"), default=str)


def parse_client_message(raw: str | bytes) -> dict[str, Any]:
    """Parse and validate a client frame. Raises ValueError on bad input."""
    if isinstance(raw, bytes):
        if len(raw) > MAX_MESSAGE_BYTES:
            raise ValueError("message too large")
        raw = raw.decode("utf-8", errors="strict")
    if not isinstance(raw, str) or len(raw) > MAX_MESSAGE_BYTES:
        raise ValueError("message too large")
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("frame must be object")
    op = str(data.get("op") or "").strip().lower()[:32]
    if not op:
        raise ValueError("missing op")
    data["op"] = op
    return data


def welcome_message(*, node_id: str, version: str) -> dict[str, Any]:
    return {
        "op": "welcome",
        "protocol": PROTOCOL_VERSION,
        "ts": time.time(),
        "node_id": node_id,
        "version": version,
        "channels": sorted(ALLOWED_CHANNELS),
        "hint": "Send {\"op\":\"subscribe\",\"channels\":[\"security\",\"metrics\"]} or {\"op\":\"ping\"}",
    }


def error_message(code: str, message: str) -> dict[str, Any]:
    return {"op": "error", "code": str(code)[:64], "message": str(message)[:300], "ts": time.time()}


def pong_message(nonce: str | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {"op": "pong", "ts": time.time()}
    if nonce:
        out["nonce"] = str(nonce)[:64]
    return out


def normalize_subscribe(channels: Any) -> list[str]:
    if not isinstance(channels, list):
        raise ValueError("channels must be a list")
    out: list[str] = []
    for item in channels[:MAX_SUBSCRIBE]:
        ch = str(item or "").strip().lower()[:32]
        if ch in ALLOWED_CHANNELS and ch not in out:
            out.append(ch)
    return out


def security_event_frame(events: list[Any], latest_seq: int) -> dict[str, Any]:
    # Strip potentially large/sensitive fields already limited by ring
    safe = []
    for ev in events[:50]:
        if not isinstance(ev, dict):
            continue
        safe.append(
            {
                "seq": ev.get("seq"),
                "event": str(ev.get("event") or "")[:128],
                "ts": ev.get("ts"),
                "level": str(ev.get("level") or "")[:16],
                "client_ip": str(ev.get("client_ip") or "")[:64],
                "path": str(ev.get("path") or "")[:128],
                "method": str(ev.get("method") or "")[:16],
            }
        )
    return {
        "op": "event",
        "channel": "security",
        "ts": time.time(),
        "latest_seq": int(latest_seq or 0),
        "events": safe,
    }


def metrics_frame(slim: dict[str, Any]) -> dict[str, Any]:
    return {
        "op": "event",
        "channel": "metrics",
        "ts": time.time(),
        "metrics": {
            "uptime_seconds": slim.get("uptime_seconds"),
            "total_requests": slim.get("total_requests"),
            "total_errors": slim.get("total_errors"),
            "error_rate": slim.get("error_rate"),
            "requests_per_second": slim.get("requests_per_second"),
            "rate_limit_hits": slim.get("rate_limit_hits"),
            "provider_latency": (slim.get("provider_latency") or [])[:10],
            "top_routes": (slim.get("top_routes") or [])[:8],
            "status_codes": slim.get("status_codes"),
        },
    }


def fanin_frame(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Frame for multi-replica merged security events."""
    events = []
    for ev in (snapshot.get("events") or [])[:40]:
        if not isinstance(ev, dict):
            continue
        events.append(
            {
                "seq": ev.get("seq"),
                "event": str(ev.get("event") or "")[:128],
                "ts": ev.get("ts"),
                "level": str(ev.get("level") or "")[:16],
                "client_ip": str(ev.get("client_ip") or "")[:64],
                "path": str(ev.get("path") or "")[:128],
                "method": str(ev.get("method") or "")[:16],
                "node_id": str(ev.get("node_id") or "")[:64],
            }
        )
    return {
        "op": "event",
        "channel": "fanin",
        "ts": time.time(),
        "nodes_tracked": int(snapshot.get("nodes_tracked") or 0),
        "nodes": (snapshot.get("nodes") or [])[:32],
        "events": events,
    }


def system_frame(message: str, *, level: str = "info") -> dict[str, Any]:

    return {
        "op": "event",
        "channel": "system",
        "ts": time.time(),
        "level": str(level)[:16],
        "message": str(message)[:500],
    }


def handle_command(op: str, data: dict[str, Any], *, subscribed: set[str]) -> tuple[dict[str, Any] | None, set[str]]:
    """Process a non-auth client op. Returns (reply_or_None, updated_subscriptions)."""
    if op == "ping":
        return pong_message(data.get("nonce")), subscribed
    if op == "subscribe":
        channels = normalize_subscribe(data.get("channels") or [])
        new_set = set(channels)
        return (
            {
                "op": "subscribed",
                "ts": time.time(),
                "channels": sorted(new_set),
            },
            new_set,
        )
    if op == "unsubscribe":
        channels = normalize_subscribe(data.get("channels") or list(subscribed))
        new_set = set(subscribed) - set(channels)
        return (
            {
                "op": "subscribed",
                "ts": time.time(),
                "channels": sorted(new_set),
            },
            new_set,
        )
    if op == "help":
        return (
            {
                "op": "help",
                "ts": time.time(),
                "ops": ["ping", "subscribe", "unsubscribe", "help", "auth"],
                "channels": sorted(ALLOWED_CHANNELS),
            },
            subscribed,
        )
    return error_message("unknown_op", f"Unknown op: {op}"), subscribed
