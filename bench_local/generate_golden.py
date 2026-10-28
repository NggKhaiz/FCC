"""Generate cross-language golden data for the Go core parity tests.

Run from the repository root with the project virtualenv:
    .venv/bin/python bench_local/generate_golden.py

The output is committed at gocore/testdata/golden.json and exercised by
gocore/sse and gocore/retention tests. Every expected value is produced by the
Python reference implementation, so the Go port is checked against the exact
semantics shipped today rather than against an independent re-derivation.
"""

import json
import random
import sys
from pathlib import Path

sys.path.insert(0, "src")

from free_claude_code.api.stream_frames import _LINE_BREAKS, frame_data
from free_claude_code.core.anthropic.stream_contracts import (
    failure_reason_from_frame,
    sse_event_name,
)
from free_claude_code.core.anthropic.streaming import decoder as decoder_module
from free_claude_code.core.anthropic.streaming.decoder import (
    AnthropicSSEDecoder,
)
from free_claude_code.core.delivered_response import _json_byte_size

FAILURE_EVENTS = frozenset({"error", "response.error", "response.failed"})

OUTPUT = Path("gocore/testdata/golden.json")


def realistic_frame(seq: int) -> str:
    """One frame of the canonical Anthropic message stream used in benches."""
    if seq == 0:
        payload = {
            "type": "message_start",
            "message": {
                "id": "msg_01Golden",
                "type": "message",
                "role": "assistant",
                "model": "claude-sonnet-4",
                "usage": {"input_tokens": 1024, "output_tokens": 1},
            },
        }
        return (
            f"event: message_start\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
        )
    block = (seq // 97) % 4
    phase = seq % 97
    if phase == 0:
        block_type = ("text", "thinking", "tool_use", "text_eager")[block]
        payload = {
            "type": "content_block_start",
            "index": block,
            "content_block": {
                "type": block_type,
                "text": "go" if block_type == "text_eager" else "",
            },
        }
    elif phase == 96:
        payload = {"type": "content_block_stop", "index": block}
    elif block == 2:
        payload = {
            "type": "content_block_delta",
            "index": block,
            "delta": {
                "type": "input_json_delta",
                "partial_json": f'"k{seq}":{seq % 7},',
            },
        }
    elif block == 1:
        payload = {
            "type": "content_block_delta",
            "index": block,
            "delta": {"type": "thinking_delta", "thinking": "reasoning " * 4},
        }
    else:
        payload = {
            "type": "content_block_delta",
            "index": block,
            "delta": {"type": "text_delta", "text": f"chunk {seq} unicode 中文 🚀\n"},
        }
    event = payload["type"]
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def build_streams() -> list[str]:
    streams: list[str] = []
    # Realistic long stream, sized like the benchmark workload (2005 frames).
    streams.append("".join(realistic_frame(i) for i in range(2005)))
    # Exotic but legal wire shapes.
    streams.append(
        'event: x\ndata: {"a": 1}\r\n\r\n'
        ": keepalive\n\n"
        'event: y\rdata: {"b": "line\u2028sep"}\r\r'
        'data: {"price": 0.00000001}\n\n'
    )
    streams.append("\n\n\nevent: z\ndata: {}\n\n\n")
    streams.append('event: unterminated\ndata: {"no": "boundary"}')
    return streams


def framing_cases() -> list[dict]:
    rng = random.Random(20261028)
    alphabet = ["event: x\n", "data: {}\n", ": keep\n", "\n", "\r", "a", "中"]
    cases: list[dict] = []
    for _ in range(120):
        stream = "".join(rng.choice(alphabet) for _ in range(rng.randrange(1, 160)))
        width = rng.randrange(1, 9)
        decoder_module._FAST_PATH = True
        fast = AnthropicSSEDecoder()
        decoder_module._FAST_PATH = False
        slow = AnthropicSSEDecoder()
        chunks: list[str] = []
        fast_frames: list[str] = []
        slow_frames: list[str] = []
        cursor = 0
        while cursor < len(stream):
            chunk = stream[cursor : cursor + width]
            cursor += width
            chunks.append(chunk)
            decoder_module._FAST_PATH = True
            fast_frames.extend(fast.feed_frames(chunk))
            decoder_module._FAST_PATH = False
            slow_frames.extend(slow.feed_frames(chunk))
        assert fast_frames == slow_frames, "fast/regex divergence in fixture"
        cases.append(
            {
                "chunks": chunks,
                "frames": fast_frames,
                "finish": fast.finish_frames(),
            }
        )
    return cases


def data_text(frame: str) -> str:
    """Join data lines with the strict SSE line-break regex.

    splitlines() would also split on U+2028/U+2029/NEL, which may legally
    occur inside JSON text; frame_data itself only recognizes CR/LF/CRLF.
    """
    return "\n".join(
        line[5:].lstrip(" ")
        for line in _LINE_BREAKS.split(frame)
        if line.startswith("data:")
    )


def frame_cases(streams: list[str]) -> list[dict]:
    decoder_module._FAST_PATH = True
    cases: list[dict] = []
    for stream in streams:
        decoder = AnthropicSSEDecoder()
        frames = list(decoder.feed_frames(stream))
        frames.extend(decoder.finish_frames())
        for frame in frames:
            parsed = frame_data(frame)
            cases.append(
                {
                    "frame": frame,
                    "event": sse_event_name(frame),
                    "failure": failure_reason_from_frame(frame, FAILURE_EVENTS),
                    "data_text": data_text(frame),
                    "has_object_data": isinstance(parsed, dict),
                }
            )
    # Explicit failure-frame coverage.
    failures = [
        'event: error\ndata: {"type":"error","error":{"type":"overloaded_error","code":"overloaded"}}\n\n',
        'event: response.failed\ndata: {"type":"response.failed","response":{"error":{"code":"rate_limit"}}}\n\n',
        'data: {"type":"error","error":{"type":"api_error"}}\n\n',
        'event: response.error\ndata: {"type":"response.error","error":{"message":"boom"}}\n\n',
        'event: message_stop\ndata: {"type":"message_stop"}\n\n',
        "event: error\ndata: not-json\n\n",
    ]
    cases.extend(
        {
            "frame": frame,
            "event": sse_event_name(frame),
            "failure": failure_reason_from_frame(frame, FAILURE_EVENTS),
            "data_text": data_text(frame),
            "has_object_data": isinstance(frame_data(frame), dict),
        }
        for frame in failures
    )
    return cases


def retention_cases() -> list[dict]:
    values = [
        "hello world",
        "x" * 1000,
        "unicode 中文 🚀 U+2028 \u2028 end",
        1,
        3.14159,
        True,
        False,
        None,
        [],
        {},
        {"type": "text", "text": "hello world"},
        {
            "index": 0,
            "content_block": {
                "type": "tool_use",
                "id": "toolu_1",
                "input": {"k": [1, 2, 3]},
            },
        },
        {"nested": {"a": [1, True, None, "x"], "b": {"c": {}}}, "empty": [], "zero": 0},
        [{"text": "a"}, {"text": "bb"}, {"thought": True, "text": "中文"}],
    ]
    return [{"value": value, "size": _json_byte_size(value)} for value in values]


def main() -> None:
    streams = build_streams()
    (OUTPUT.parent / "stream.txt").write_text(streams[0])
    golden = {
        "failure_events": sorted(FAILURE_EVENTS),
        "framing": framing_cases(),
        "frames": frame_cases(streams),
        "retention": retention_cases(),
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(golden, ensure_ascii=False, indent=2))
    print(
        f"wrote {OUTPUT}: {len(golden['framing'])} framing cases, "
        f"{len(golden['frames'])} frame cases, {len(golden['retention'])} retention cases"
    )


if __name__ == "__main__":
    main()
