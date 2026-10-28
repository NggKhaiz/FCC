"""Component-level Python benchmark for the hot path, paired with gocore/cmd/bench.

Both implementations read gocore/testdata/stream.txt and run the same pipeline:
frame splitting, event naming, cached payload parsing, failure scanning, and
retention byte accounting for every frame. Run:

    .venv/bin/python bench_local/bench_golden.py
"""

import sys
import time
import tracemalloc

sys.path.insert(0, "src")

from free_claude_code.api.stream_frames import FramePayloadCache
from free_claude_code.api.tool_call_buffer import ToolCallBuffer
from free_claude_code.core.anthropic.stream_contracts import (
    failure_reason_from_frame,
    sse_event_name,
)
from free_claude_code.core.anthropic.streaming.decoder import (
    AnthropicSSEDecoder,
)
from free_claude_code.core.delivered_response import (
    DeliveredResponse,
    _json_byte_size,
)

FAILURE_EVENTS = frozenset({"error", "response.error", "response.failed"})
ITERATIONS = 100


def run_pipeline(stream: str) -> int:
    decoder = AnthropicSSEDecoder()
    frames = list(decoder.feed_frames(stream))
    frames.extend(decoder.finish_frames())
    cache = FramePayloadCache()
    retained = 0
    delivered = DeliveredResponse("messages")
    tools = ToolCallBuffer("messages", payload_cache=cache)
    for frame in frames:
        sse_event_name(frame)
        payload = cache.get(frame)
        if payload is not None:
            retained += _json_byte_size(payload)
            delivered.observe(payload)
        failure_reason_from_frame(frame, FAILURE_EVENTS)
        tools.feed(frame)
    delivered.snapshot()
    delivered.handoff_events()
    return len(frames)


def main() -> None:
    with open("gocore/testdata/stream.txt", encoding="utf-8") as handle:
        stream = handle.read()
    for _ in range(5):
        run_pipeline(stream)

    tracemalloc.start()
    start = time.perf_counter()
    frames = 0
    for _ in range(ITERATIONS):
        frames = run_pipeline(stream)
    elapsed = time.perf_counter() - start
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    per_request = elapsed / ITERATIONS
    print(f"frames/request:   {frames}")
    print(f"total:            {elapsed:.3f}s over {ITERATIONS} requests")
    print(f"per-request:      {per_request * 1000:.2f} ms")
    print(f"throughput:       {ITERATIONS / elapsed:.1f} req/s")
    print(f"peak python alloc: {peak / 1024:.1f} KiB")


if __name__ == "__main__":
    main()
