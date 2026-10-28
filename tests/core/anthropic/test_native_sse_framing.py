"""Only SSE line terminators split frames; Unicode text stays verbatim."""

import json
import random

import pytest

from free_claude_code.core.anthropic.stream_contracts import parse_sse_text
from free_claude_code.core.anthropic.streaming import decoder as decoder_module
from free_claude_code.core.anthropic.streaming.decoder import AnthropicSSEDecoder


@pytest.mark.parametrize("newline", ["\r", "\n", "\r\n"])
@pytest.mark.parametrize("width", range(1, 8))
def test_incremental_sse_terminators_preserve_unicode_text(
    newline: str, width: int
) -> None:
    payload = {"type": "text_delta", "text": "left\u2028middle\u2029right\u0085end"}
    frame = f"event: content_block_delta{newline}data: {json.dumps(payload, ensure_ascii=False)}{newline}{newline}"
    raw = frame * 2
    assert [event.data for event in parse_sse_text(raw)] == [payload, payload]
    decoder = AnthropicSSEDecoder()
    events = []
    for start in range(0, len(raw), width):
        events.extend(decoder.feed(raw[start : start + width]))
    assert [event.data for event in events] == [payload, payload]
    assert decoder.finish() == ()


@pytest.mark.parametrize("newline", ["\r", "\n", "\r\n"])
@pytest.mark.parametrize("width", range(1, 8))
def test_raw_frames_preserve_comments_fields_delimiters_and_unicode(newline, width):
    payload = json.dumps({"text": "left\u2028middle\u2029right"}, ensure_ascii=False)
    raw = (
        f": keepalive{newline}{newline}"
        f"id: upstream{newline}event: custom{newline}"
        f"data: {payload}{newline}{newline}"
    )
    decoder = AnthropicSSEDecoder()
    frames = []
    for start in range(0, len(raw), width):
        frames.extend(decoder.feed_frames(raw[start : start + width]))
    frames.extend(decoder.finish_frames())
    assert "".join(frames) == raw


def test_fast_path_frames_match_regex_path_on_random_streams(monkeypatch):
    """The split-based fast path must be byte-identical to the regex decoder."""

    rng = random.Random(20260417)
    alphabet = ["event: x\n", "data: {}\n", ": keep\n", "\n", "\r", "a", "中"]
    for _ in range(400):
        stream = "".join(rng.choice(alphabet) for _ in range(rng.randrange(1, 120)))

        fast = AnthropicSSEDecoder()
        slow = AnthropicSSEDecoder()

        cursor = 0
        while cursor < len(stream):
            width = rng.randrange(1, 8)
            chunk = stream[cursor : cursor + width]
            cursor += width
            monkeypatch.setattr(decoder_module, "_FAST_PATH", True)
            fast_frames = fast.feed_frames(chunk)
            monkeypatch.setattr(decoder_module, "_FAST_PATH", False)
            slow_frames = slow.feed_frames(chunk)
            assert fast_frames == slow_frames
            assert fast._boundary_tail == slow._boundary_tail
            assert fast._parts == slow._parts
        monkeypatch.setattr(decoder_module, "_FAST_PATH", True)
        fast_tail = fast.finish_frames()
        monkeypatch.setattr(decoder_module, "_FAST_PATH", False)
        assert fast_tail == slow.finish_frames()
