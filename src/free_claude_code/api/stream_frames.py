"""Read and replace SSE data without losing framing or decimal precision."""

import re
from collections.abc import Mapping
from typing import Any

import simplejson

# SSE wire line endings are CR/LF/CRLF. The patterns are compiled once at module
# load: passing pattern *strings* to ``re.split`` forces a cache lookup on every
# frame, and frames are parsed on the hottest path in a streaming response.
_LINE_BREAKS = re.compile(r"\r\n|\r|\n")
_LINE_BREAKS_CAPTURING = re.compile(r"(\r\n|\r|\n)")
_DATA_PREFIX = "data:"


def frame_data(frame: str) -> dict[str, Any] | None:
    """Read only SSE data, retaining decimal precision for envelope edits."""
    text = "\n".join(
        line[5:].lstrip(" ")
        for line in _LINE_BREAKS.split(frame)
        if line.startswith(_DATA_PREFIX)
    )
    try:
        value: object = simplejson.loads(text, use_decimal=True)
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def replace_frame_data(frame: str, payload: Mapping[str, object]) -> str:
    """Replace data lines while preserving other SSE fields and line endings."""
    lines = _LINE_BREAKS_CAPTURING.split(frame)
    replacement = "data: " + simplejson.dumps(
        payload, use_decimal=True, ensure_ascii=False
    )
    parts: list[str] = []
    replaced = False
    for index in range(0, len(lines), 2):
        line = lines[index]
        ending = lines[index + 1] if index + 1 < len(lines) else ""
        if line.startswith("data:"):
            if replaced:
                continue
            line = replacement
            replaced = True
        parts.extend((line, ending))
    return "".join(parts)


class FramePayloadCache:
    """Memoize decimal-aware frame parses across one response's delivery stages.

    A single SSE frame is inspected by several delivery stages (tool-call
    holdback, finalization, publication, evidence). Each stage otherwise
    re-parsed the identical frame string. The cache keeps the *pristine* parse
    so the common read-only path parses once; callers that edit a payload must
    copy it first, and edited frames are re-serialized to new strings (cache
    misses), preserving the original frame semantics exactly.

    A small FIFO bound keeps retained memory flat regardless of stream length.
    The repeated inspections of one frame happen back-to-back, so even a short
    window captures every reuse while a miss only costs a fresh parse.
    """

    __slots__ = ("_capacity", "_entries")

    def __init__(self, capacity: int = 64) -> None:
        self._entries: dict[str, dict[str, Any] | None] = {}
        self._capacity = capacity

    def get(self, frame: str) -> dict[str, Any] | None:
        if frame in self._entries:
            return self._entries[frame]
        parsed = frame_data(frame)
        if len(self._entries) >= self._capacity:
            # Evict the oldest entry; plain dicts preserve insertion order.
            del self._entries[next(iter(self._entries))]
        self._entries[frame] = parsed
        return parsed

    def clear(self) -> None:
        self._entries.clear()
