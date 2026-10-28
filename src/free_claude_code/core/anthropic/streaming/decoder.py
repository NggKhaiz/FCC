"""Incremental framing for Anthropic-compatible SSE streams."""

import re

from ..stream_contracts import SSEEvent, parse_sse_text

_EVENT_BOUNDARY = re.compile(r"(?>\r\n|\r|\n){2}")

# Toggle for tests: when disabled, feed_frames always uses the regex path so
# the fast path can be checked against it byte for byte.
_FAST_PATH = True


class AnthropicSSEDecoder:
    """Decode split SSE text, optionally filtering named events before JSON parsing."""

    def __init__(self, *, event_names: frozenset[str] | None = None) -> None:
        self._event_names = event_names
        self._parts: list[str] = []
        self._boundary_tail = ""

    def feed(self, chunk: str) -> tuple[SSEEvent, ...]:
        """Consume one wire chunk and return its selected complete events."""

        return tuple(
            event
            for raw in self.feed_frames(chunk)
            for event in parse_sse_text(raw, event_names=self._event_names)
        )

    def feed_frames(self, chunk: str) -> tuple[str, ...]:
        """Retain original framing, including comments and unknown SSE fields."""

        # Fast path: fresh buffer, LF-only stream, and no blank comment events
        # (three or more consecutive newlines). This covers every healthy
        # upstream response; everything unusual falls through to the regex.
        if (
            _FAST_PATH
            and not self._boundary_tail
            and not self._parts
            and "\r" not in chunk
            and "\n\n\n" not in chunk
        ):
            pieces = chunk.split("\n\n")
            remainder = pieces[-1]
            if len(pieces) > 1:
                frames = tuple(f"{piece}\n\n" for piece in pieces[:-1])
                if remainder:
                    self._parts.append(remainder)
                self._boundary_tail = remainder[-3:]
                return frames
            if remainder:
                self._parts.append(remainder)
            self._boundary_tail = chunk[-3:]
            return ()

        frames: list[str] = []
        probe = self._boundary_tail + chunk
        prefix_length = len(self._boundary_tail)
        chunk_start = 0
        for match in _EVENT_BOUNDARY.finditer(probe):
            chunk_end = match.end() - prefix_length
            self._parts.append(chunk[chunk_start:chunk_end])
            raw = "".join(self._parts)
            self._parts.clear()
            frames.append(raw)
            chunk_start = chunk_end

        remainder = chunk[chunk_start:]
        if remainder:
            self._parts.append(remainder)
        if chunk_start:
            self._boundary_tail = remainder[-3:]
        else:
            self._boundary_tail = (self._boundary_tail + chunk)[-3:]
        return tuple(frames)

    def finish(self) -> tuple[SSEEvent, ...]:
        """Return a final unterminated event, if one is present."""

        return tuple(
            event
            for raw in self.finish_frames()
            for event in parse_sse_text(raw, event_names=self._event_names)
        )

    def finish_frames(self) -> tuple[str, ...]:
        """Return the original trailing frame and clear framing state."""

        remainder = "".join(self._parts)
        self._parts.clear()
        self._boundary_tail = ""
        if not remainder:
            return ()
        return (remainder,)
