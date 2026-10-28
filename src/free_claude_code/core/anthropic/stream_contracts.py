"""Neutral SSE parsing and Anthropic stream shape assertions.

Used by default CI contract tests and by opt-in live smoke scenarios.
"""

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from .server_tool_types import (
    SERVER_TOOL_USE,
    WEB_FETCH_TOOL_RESULT,
    WEB_SEARCH_TOOL_RESULT,
)

# Content blocks that only use content_block_start/stop (no deltas), including
# Anthropic server tools and eager text emitted in a single start event.
_NO_DELTA_BLOCK_KINDS = frozenset(
    {
        SERVER_TOOL_USE,
        WEB_SEARCH_TOOL_RESULT,
        WEB_FETCH_TOOL_RESULT,
        "text_eager",
        "redacted_thinking",
    }
)

_ALLOWED_BLOCK_START_TYPES = frozenset(
    {
        "text",
        "thinking",
        "tool_use",
        "redacted_thinking",
        SERVER_TOOL_USE,
        WEB_SEARCH_TOOL_RESULT,
        WEB_FETCH_TOOL_RESULT,
    }
)


@dataclass(frozen=True, slots=True)
class SSEEvent:
    event: str
    data: dict[str, Any]
    raw: str


def parse_sse_lines(
    lines: Iterable[str], *, event_names: frozenset[str] | None = None
) -> list[SSEEvent]:
    """Decode selected named events and all unnamed events when a filter is supplied."""
    events: list[SSEEvent] = []
    current_event = ""
    data_parts: list[str] = []
    raw_parts: list[str] = []

    for line in lines:
        stripped = line.rstrip("\r\n")
        if stripped == "":
            _append_event(events, current_event, data_parts, raw_parts, event_names)
            current_event = ""
            data_parts = []
            raw_parts = []
            continue
        raw_parts.append(stripped)
        if stripped.startswith("event:"):
            current_event = stripped.split(":", 1)[1].strip()
        elif stripped.startswith("data:"):
            data_parts.append(stripped.split(":", 1)[1].strip())

    _append_event(events, current_event, data_parts, raw_parts, event_names)
    return events


# Compiled once: this runs for every SSE frame, and a pattern string would pay
# a regex-cache lookup on each call while also splitting on exotic Unicode line
# separators that may legally appear inside a JSON payload.
_SSE_LINE_BREAKS = re.compile(r"\r\n|\r|\n")


def parse_sse_text(
    text: str, *, event_names: frozenset[str] | None = None
) -> list[SSEEvent]:
    # SSE uses CR/LF framing; Unicode line separators can occur inside JSON text.
    return parse_sse_lines(_SSE_LINE_BREAKS.split(text), event_names=event_names)


def sse_event_name(frame: str) -> str:
    """Return an SSE frame's named event without decoding its JSON payload."""
    # splitlines is a C-level scan and only an ``event:`` prefix line is used,
    # so exotic separators inside JSON text cannot change the result.
    for line in frame.splitlines():
        if line.startswith("event:"):
            return line[6:].strip()
    return ""


def failure_reason_from_frame(frame: str, failure_events: frozenset[str]) -> str | None:
    """Return a failure reason for one SSE frame without parsing healthy frames.

    Outcome observation only needs the small set of terminal error events. The
    event name lives on a plain ``event:`` line, so the JSON payload is decoded
    only when a frame is a named failure event or an unnamed data frame that
    plausibly carries an error. Successful streams therefore perform no JSON
    decoding at the outcome middleware.
    """
    # Cheap C-level rejection before any line splitting: a failure frame has
    # to mention an error payload or the response.failed event.
    if "error" not in frame and "response.failed" not in frame:
        return None

    event_name = ""
    data_parts: list[str] = []
    for line in _SSE_LINE_BREAKS.split(frame):
        if line.startswith("event:"):
            event_name = line[6:].strip()
        elif line.startswith("data:"):
            data_parts.append(line[5:].lstrip(" "))

    if event_name:
        if event_name not in failure_events:
            return None
    elif not data_parts:
        return None

    try:
        parsed = json.loads("\n".join(data_parts)) if data_parts else {}
    except json.JSONDecodeError:
        return None
    data = parsed if isinstance(parsed, dict) else {"value": parsed}

    kind = event_name or data.get("type") or ("error" if "error" in data else None)
    if not isinstance(kind, str) or kind not in failure_events:
        return None
    response = data.get("response")
    payload = response if isinstance(response, dict) else data
    error = payload.get("error")
    error = error if isinstance(error, dict) else payload
    reason = error.get("code") or error.get("type")
    return reason if isinstance(reason, str) else str(kind)


def _append_event(
    events: list[SSEEvent],
    current_event: str,
    data_parts: list[str],
    raw_parts: list[str],
    event_names: frozenset[str] | None,
) -> None:
    if not current_event and not data_parts:
        return
    if event_names is not None and current_event and current_event not in event_names:
        return
    data_text = "\n".join(data_parts)
    data: dict[str, Any]
    try:
        parsed = json.loads(data_text) if data_text else {}
        data = parsed if isinstance(parsed, dict) else {"value": parsed}
    except json.JSONDecodeError:
        data = {"raw": data_text}
    events.append(SSEEvent(current_event, data, "\n".join(raw_parts)))


def assert_anthropic_stream_contract(
    events: list[SSEEvent], *, allow_error: bool = False
) -> None:
    """Check minimal Anthropic-style SSE invariants and block nesting.

    Does *not* assert strict event ordering (e.g. :class:`message_delta` vs
    content blocks). Successful streams end in ``message_stop``; when explicitly
    allowed, failed streams may instead end in a protocol-native ``error``.
    """
    assert events, "stream produced no SSE events"
    event_names = [event.event for event in events]
    assert "message_start" in event_names, event_names
    allowed_terminal_events = (
        {"message_stop", "error"} if allow_error else {"message_stop"}
    )
    assert event_names[-1] in allowed_terminal_events, event_names

    open_blocks: dict[int, str] = {}
    seen_blocks: set[int] = set()
    for event in events:
        if event.event == "error" and not allow_error:
            raise AssertionError(f"unexpected SSE error event: {event.data}")

        if event.event == "content_block_start":
            index = event_index(event)
            block = event.data.get("content_block", {})
            assert isinstance(block, dict), event.data
            block_type = str(block.get("type", ""))
            assert block_type in _ALLOWED_BLOCK_START_TYPES, event.data
            assert index not in open_blocks, f"block {index} started twice"
            assert index not in seen_blocks, f"block {index} reused after stop"
            if block_type == "text" and str(block.get("text", "")).strip():
                storage = "text_eager"
            else:
                storage = block_type
            open_blocks[index] = storage
            seen_blocks.add(index)
            continue

        if event.event == "content_block_delta":
            index = event_index(event)
            assert index in open_blocks, f"delta for unopened block {index}"
            kind = open_blocks[index]
            assert kind not in _NO_DELTA_BLOCK_KINDS, (
                f"unexpected delta for start/stop-only block {kind} at index {index}"
            )
            delta = event.data.get("delta", {})
            assert isinstance(delta, dict), event.data
            delta_type = str(delta.get("type", ""))
            if kind == "thinking":
                assert delta_type in (
                    "thinking_delta",
                    "signature_delta",
                ), f"block {index} is {kind}, got {delta_type}"
                continue
            expected = {
                "text": "text_delta",
                "tool_use": "input_json_delta",
            }[kind]
            assert delta_type == expected, f"block {index} is {kind}, got {delta_type}"
            continue

        if event.event == "content_block_stop":
            index = event_index(event)
            assert index in open_blocks, f"stop for unopened block {index}"
            open_blocks.pop(index)

    assert not open_blocks, f"unclosed blocks: {open_blocks}"
    assert seen_blocks, "stream did not emit any content blocks"


def event_names(events: list[SSEEvent]) -> list[str]:
    return [event.event for event in events]


def text_content(events: list[SSEEvent]) -> str:
    parts: list[str] = []
    for event in events:
        if event.event == "content_block_start":
            block = event.data.get("content_block", {})
            if isinstance(block, dict) and block.get("type") == "text":
                eager = str(block.get("text", ""))
                if eager:
                    parts.append(eager)
        delta = event.data.get("delta", {})
        if isinstance(delta, dict) and delta.get("type") == "text_delta":
            parts.append(str(delta.get("text", "")))
    return "".join(parts)


def thinking_content(events: list[SSEEvent]) -> str:
    parts: list[str] = []
    for event in events:
        delta = event.data.get("delta", {})
        if isinstance(delta, dict) and delta.get("type") == "thinking_delta":
            parts.append(str(delta.get("thinking", "")))
    return "".join(parts)


def has_tool_use(events: list[SSEEvent]) -> bool:
    for event in events:
        block = event.data.get("content_block", {})
        if isinstance(block, dict) and block.get("type") == "tool_use":
            return True
    return False


def event_index(event: SSEEvent) -> int:
    """Return the content block ``index`` field from an SSE payload (strict)."""
    value = event.data.get("index")
    assert isinstance(value, int), event.data
    return value
