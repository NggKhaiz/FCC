"""Minimal OpenMetrics-inspired protobuf-lite encoding (no external deps).

Wire format (little-endian):
  magic: b'FCCOM1\\0' (8 bytes)
  version: u32 = 1
  metric_count: u32
  repeated Metric:
    name_len: u16, name: utf-8
    type: u8 (0=gauge, 1=counter, 2=info)
    value: f64
    label_count: u16
    repeated Label: key_len u16, key, val_len u16, val
"""

from __future__ import annotations

import struct
from typing import Any

MAGIC = b"FCCOM1\x00\x00"  # 8-byte magic
TYPE_GAUGE = 0
TYPE_COUNTER = 1
TYPE_INFO = 2


def _u16(n: int) -> bytes:
    return struct.pack("<H", max(0, min(int(n), 65535)))


def _u32(n: int) -> bytes:
    return struct.pack("<I", max(0, int(n)) & 0xFFFFFFFF)


def _f64(n: float) -> bytes:
    return struct.pack("<d", float(n))


def _str(s: str, max_len: int = 128) -> bytes:
    raw = str(s or "").encode("utf-8")[:max_len]
    return _u16(len(raw)) + raw


def _metric(name: str, mtype: int, value: float, labels: list[tuple[str, str]]) -> bytes:
    body = _str(name, 64) + bytes([mtype & 0xFF]) + _f64(value) + _u16(len(labels))
    for k, v in labels[:16]:
        body += _str(k, 32) + _str(v, 128)
    return body


def render_openmetrics_protobuf(
    snapshot: dict[str, Any],
    *,
    namespace: str = "fcc",
    node_id: str = "local",
    version: str = "",
) -> bytes:
    """Encode a compact binary metrics blob for scrape agents / fan-in."""
    snap = snapshot if isinstance(snapshot, dict) else {}
    ns = (namespace or "fcc")[:32]
    node = str(node_id or "local")[:64]
    labels_base = [("node_id", node)]
    metrics: list[bytes] = []

    def add(name: str, mtype: int, value: float, extra: list[tuple[str, str]] | None = None) -> None:
        labs = list(labels_base) + (extra or [])
        metrics.append(_metric(f"{ns}_{name}", mtype, value, labs))

    add("info", TYPE_INFO, 1.0, [("version", str(version or "")[:64])])
    add("uptime_seconds", TYPE_GAUGE, float(snap.get("uptime_seconds") or 0))
    add("requests_total", TYPE_COUNTER, float(snap.get("total_requests") or 0))
    add("errors_total", TYPE_COUNTER, float(snap.get("total_errors") or 0))
    add("error_rate", TYPE_GAUGE, float(snap.get("error_rate") or 0))
    add("requests_per_second", TYPE_GAUGE, float(snap.get("requests_per_second") or 0))
    add("rate_limit_hits_total", TYPE_COUNTER, float(snap.get("rate_limit_hits") or 0))
    add("latency_overflow_total", TYPE_COUNTER, float(snap.get("latency_overflow") or 0))

    for code, count in (snap.get("status_codes") or {}).items():
        try:
            add(
                "http_responses_total",
                TYPE_COUNTER,
                float(count),
                [("code", str(int(code)))],
            )
        except (TypeError, ValueError):
            continue

    for row in (snap.get("provider_latency") or [])[:20]:
        if not isinstance(row, dict):
            continue
        pid = str(row.get("provider_id") or "")[:64]
        if not pid:
            continue
        extra = [("provider", pid)]
        add("provider_latency_avg_ms", TYPE_GAUGE, float(row.get("avg_ms") or 0), extra)
        add("provider_requests_total", TYPE_COUNTER, float(row.get("count") or 0), extra)
        add("provider_errors_total", TYPE_COUNTER, float(row.get("errors") or 0), extra)

    header = MAGIC + _u32(1) + _u32(len(metrics))
    return header + b"".join(metrics)


def parse_openmetrics_protobuf(blob: bytes) -> dict[str, Any]:
    """Decode FCCOM1 blob for tests / verify tooling."""
    if not isinstance(blob, (bytes, bytearray)) or len(blob) < 16:
        raise ValueError("too short")
    if bytes(blob[:8]) != MAGIC:
        raise ValueError("bad magic")
    version = struct.unpack_from("<I", blob, 8)[0]
    count = struct.unpack_from("<I", blob, 12)[0]
    offset = 16
    metrics: list[dict[str, Any]] = []

    def read_str(off: int) -> tuple[str, int]:
        (ln,) = struct.unpack_from("<H", blob, off)
        off += 2
        s = bytes(blob[off : off + ln]).decode("utf-8", errors="replace")
        return s, off + ln

    for _ in range(min(count, 500)):
        name, offset = read_str(offset)
        mtype = blob[offset]
        offset += 1
        (value,) = struct.unpack_from("<d", blob, offset)
        offset += 8
        (lc,) = struct.unpack_from("<H", blob, offset)
        offset += 2
        labels: dict[str, str] = {}
        for _i in range(min(lc, 16)):
            k, offset = read_str(offset)
            v, offset = read_str(offset)
            labels[k] = v
        metrics.append({"name": name, "type": mtype, "value": value, "labels": labels})
    return {"version": version, "metrics": metrics, "count": len(metrics)}
