"""Golden fixture for the Go ContinuationStream port.

Drives the real Python ContinuationStream against seeded DeliveredResponse
state machines on every wire dialect, with uuid4 replaced by a fixed sequence
so namespace-derived item ids are fully deterministic.
"""

from __future__ import annotations

import json
import uuid
from copy import deepcopy
from pathlib import Path

from free_claude_code.core import continuation_stream as cs
from free_claude_code.core import delivered_response as dr
from free_claude_code.core.delivered_response import DeliveredResponse

# Pin token estimation to the documented fallback shared with Go.
dr.estimate_text_tokens = lambda text: 0 if not text else max(1, len(text) // 4)

OUT = Path(__file__).resolve().parents[1] / "gocore" / "testdata"

# Fixed namespace UUIDs handed out in order wherever the stream calls uuid4.
NAMESPACES = [
    uuid.UUID("11111111-1111-4111-8111-111111111111"),
    uuid.UUID("22222222-2222-4222-8222-222222222222"),
    uuid.UUID("33333333-3333-4333-8333-333333333333"),
    uuid.UUID("44444444-4444-4444-8444-444444444444"),
    uuid.UUID("55555555-5555-4555-8555-555555555555"),
    uuid.UUID("66666666-6666-4666-8666-666666666666"),
]


class FixedUUID4:
    def __init__(self):
        self.consumed = []
        self._it = iter(NAMESPACES)

    def __call__(self):
        value = next(self._it)
        self.consumed.append(str(value))
        return value


def seed_responses_delivered():
    """One closed prefix item ('Hello ') and one open item ('world')."""
    delivered = DeliveredResponse("responses")
    events = [
        {
            "type": "response.created",
            "response": {
                "id": "resp_seed",
                "created_at": 1700000000,
                "status": "in_progress",
                "usage": {"input_tokens": 9, "output_tokens": 0},
            },
        },
        {
            "type": "response.output_item.added",
            "output_index": 0,
            "item": {
                "id": "msg_seed_0",
                "type": "message",
                "status": "completed",
                "role": "assistant",
                "content": [
                    {"type": "output_text", "text": "Hello ", "annotations": []}
                ],
            },
        },
        {
            "type": "response.content_part.added",
            "output_index": 0,
            "content_index": 0,
            "item_id": "msg_seed_0",
            "part": {"type": "output_text", "text": "Hello ", "annotations": []},
        },
        {
            "type": "response.output_text.done",
            "output_index": 0,
            "content_index": 0,
            "item_id": "msg_seed_0",
            "text": "Hello ",
        },
        {
            "type": "response.content_part.done",
            "output_index": 0,
            "content_index": 0,
            "item_id": "msg_seed_0",
            "part": {"type": "output_text", "text": "Hello ", "annotations": []},
        },
        {
            "type": "response.output_item.done",
            "output_index": 0,
            "item": {
                "id": "msg_seed_0",
                "type": "message",
                "status": "completed",
                "role": "assistant",
                "content": [
                    {"type": "output_text", "text": "Hello ", "annotations": []}
                ],
            },
        },
        {
            "type": "response.output_item.added",
            "output_index": 1,
            "item": {
                "id": "msg_seed_1",
                "type": "message",
                "status": "in_progress",
                "role": "assistant",
                "content": [
                    {"type": "output_text", "text": "world", "annotations": []}
                ],
            },
        },
    ]
    for event in events:
        delivered.observe(deepcopy(event))
    return delivered, events


def seed_gemini_delivered():
    delivered = DeliveredResponse("gemini")
    events = [
        {
            "responseId": "gem_seed",
            "modelVersion": "gem-1.5",
            "candidates": [
                {"index": 0, "content": {"parts": [{"text": "Hello "}]}}
            ],
        }
    ]
    for event in events:
        delivered.observe(deepcopy(event))
    return delivered, events


def seed_messages_delivered():
    delivered = DeliveredResponse("messages")
    events = [
        {
            "type": "message_start",
            "message": {"id": "msg_seed", "usage": {"input_tokens": 7}},
        },
        {
            "type": "content_block_start",
            "index": 0,
            "content_block": {"type": "text", "text": "Hello "},
        },
        {"type": "content_block_stop", "index": 0},
    ]
    for event in events:
        delivered.observe(deepcopy(event))
    return delivered, events


def run_stream(name, wire, seed_events, build):
    fixed = FixedUUID4()
    original_uuid4 = cs.uuid4
    cs.uuid4 = fixed
    try:
        delivered = DeliveredResponse(wire)
        for event in seed_events:
            delivered.observe(deepcopy(event))
        stream = cs.ContinuationStream(delivered)
        steps = build(stream, delivered)
        return {
            "name": name,
            "wire": wire,
            "seed_events": seed_events,
            "namespaces": fixed.consumed,
            "steps": steps,
        }
    finally:
        cs.uuid4 = original_uuid4


def step_begin(handoff):
    return {"op": "begin", "handoff": handoff}


def step_restart():
    return {"op": "restart"}


def step_boundary():
    return {"op": "boundary"}


def step_prepare(payload):
    return {"op": "prepare", "payload": payload}


def step_finalize(payload):
    return {"op": "finalize", "payload": payload}


def step_observe(payload):
    return {"op": "observe", "payload": payload}


def execute(stream, delivered, steps):
    # Each record carries both the input payload and the observed output so
    # the Go golden driver can replay without a second event source.
    results = []
    for step in steps:
        op = step["op"]
        if op == "begin":
            stream.begin(handoff=step["handoff"])
            results.append({"op": "begin", "handoff": step["handoff"]})
        elif op == "restart":
            stream.restart_attempt()
            results.append({"op": "restart"})
        elif op == "observe":
            payload = deepcopy(step["payload"])
            delivered.observe(deepcopy(payload))
            results.append({"op": "observe", "payload": payload})
        elif op == "boundary":
            results.append({"op": "boundary", "events": stream.boundary()})
        elif op == "prepare":
            payload = deepcopy(step["payload"])
            try:
                events = stream.prepare(deepcopy(payload))
                results.append(
                    {"op": "prepare", "payload": payload, "events": events}
                )
            except Exception as exc:
                results.append(
                    {"op": "prepare", "payload": payload, "failure": str(exc)}
                )
        elif op == "finalize":
            payload = deepcopy(step["payload"])
            try:
                event = stream.finalize(deepcopy(payload))
                results.append(
                    {"op": "finalize", "payload": payload, "event": event}
                )
            except Exception as exc:
                results.append(
                    {"op": "finalize", "payload": payload, "failure": str(exc)}
                )
    return results


def scenarios():
    out = []

    # S1. Responses overlap: lifecycle suppression, index/id remapping and a
    # finalized terminal that merges prefix items with recomputed usage.
    _, seed = seed_responses_delivered()

    def build_overlap(stream, delivered):
        steps = [
            step_begin(False),
            step_boundary(),
            step_prepare({"type": "response.created"}),
            step_prepare({"type": "response.in_progress"}),
            step_prepare(
                {
                    "type": "response.output_text.delta",
                    "output_index": 0,
                    "content_index": 0,
                    "item_id": "msg_new_0",
                    "delta": "Hello again",
                }
            ),
            step_prepare(
                {
                    "type": "response.output_item.done",
                    "output_index": 0,
                    "item": {
                        "id": "msg_new_0",
                        "type": "message",
                        "status": "completed",
                        "role": "assistant",
                        "content": [
                            {
                                "type": "output_text",
                                "text": "Hello again",
                                "annotations": [],
                            }
                        ],
                    },
                }
            ),
            step_prepare(
                {
                    "type": "response.completed",
                    "response_id": "resp_continuation",
                    "response": {
                        "id": "resp_continuation",
                        "status": "completed",
                        "output": [
                            {
                                "id": "msg_new_0",
                                "type": "message",
                                "content": [
                                    {"type": "output_text", "text": "Hello again"}
                                ],
                            }
                        ],
                    },
                }
            ),
        ]
        return execute(stream, delivered, steps)

    out.append(
        run_stream("responses_overlap_mapping", "responses", seed, build_overlap)
    )

    # S2. Handoff passes frames through untouched and keeps only prefix items.
    def build_handoff(stream, delivered):
        steps = [
            step_begin(True),
            step_boundary(),
            step_prepare(
                {
                    "type": "response.output_text.delta",
                    "output_index": 0,
                    "content_index": 0,
                    "item_id": "msg_raw_0",
                    "delta": "raw",
                }
            ),
            step_finalize(
                {
                    "type": "response.completed",
                    "response": {
                        "id": "resp_handoff",
                        "status": "completed",
                        "output": [
                            {"id": "msg_raw_0", "type": "message",
                             "content": [{"type": "output_text", "text": "raw"}]}
                        ],
                    },
                }
            ),
        ]
        return execute(stream, delivered, steps)

    out.append(run_stream("responses_handoff_passthrough", "responses", seed, build_handoff))

    # S3. No-progress terminal is a non-retryable 502.
    def build_no_progress(stream, delivered):
        steps = [
            step_begin(False),
            step_finalize(
                {
                    "type": "response.completed",
                    "response": {"id": "resp_empty", "output": []},
                }
            ),
        ]
        return execute(stream, delivered, steps)

    out.append(run_stream("responses_no_progress_failure", "responses", seed, build_no_progress))

    # S4. Text constraint raised while preparing a structured-output frame.
    def build_constraint(stream, delivered):
        steps = [
            step_begin(False),
            step_prepare(
                {
                    "type": "response.completed",
                    "response": {
                        "text": {"format": {"type": "json_object"}},
                        "output": [],
                    },
                }
            ),
        ]
        return execute(stream, delivered, steps)

    out.append(run_stream("responses_text_constraint_failure", "responses", seed, build_constraint))

    # S5. Restart swaps the namespace: identical ids map differently while the
    # expected prefix survives for overlap removal.
    def build_restart(stream, delivered):
        steps = [
            step_begin(False),
            step_prepare(
                {
                    "type": "response.output_text.done",
                    "output_index": 0,
                    "content_index": 0,
                    "item_id": "msg_retry_0",
                    "text": "Hello again",
                }
            ),
            step_restart(),
            step_prepare(
                {
                    "type": "response.output_text.done",
                    "output_index": 0,
                    "content_index": 0,
                    "item_id": "msg_retry_0",
                    "text": "Hello again",
                }
            ),
        ]
        return execute(stream, delivered, steps)

    out.append(run_stream("responses_restart_new_namespace", "responses", seed, build_restart))

    # S6. Gemini header passthrough, usageMetadata removal and no-progress gate.
    _, gemini_seed = seed_gemini_delivered()

    def build_gemini(stream, delivered):
        steps = [
            step_begin(False),
            step_boundary(),
            step_prepare(
                {
                    "candidates": [
                        {
                            "index": 0,
                            "content": {
                                "parts": [{"text": "Hello again"}],
                            },
                        }
                    ],
                    "usageMetadata": {"candidatesTokenCount": 2},
                }
            ),
            step_finalize(
                {
                    "candidates": [
                        {"index": 0, "finishReason": "STOP",
                         "content": {"parts": [{"text": "Hello again"}]}}
                    ],
                    "usageMetadata": {"candidatesTokenCount": 2},
                }
            ),
        ]
        return execute(stream, delivered, steps)

    out.append(run_stream("gemini_passthrough", "gemini", gemini_seed, build_gemini))

    def build_gemini_no_progress(stream, delivered):
        steps = [
            step_begin(False),
            step_finalize(
                {
                    "candidates": [
                        {"index": 0, "finishReason": "STOP",
                         "content": {"parts": [{"text": ""}]}}
                    ]
                }
            ),
        ]
        return execute(stream, delivered, steps)

    out.append(run_stream("gemini_no_progress_failure", "gemini", gemini_seed, build_gemini_no_progress))

    # S7. Messages wire: block index offset and message_delta usage override.
    _, messages_seed = seed_messages_delivered()

    def build_messages(stream, delivered):
        steps = [
            step_begin(False),
            step_boundary(),
            step_prepare(
                {
                    "type": "content_block_start",
                    "index": 0,
                    "content_block": {"type": "text", "text": ""},
                }
            ),
            step_prepare(
                {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "text_delta", "text": "again"},
                }
            ),
            step_finalize(
                {
                    "type": "message_delta",
                    "delta": {"stop_reason": "end_turn"},
                    "usage": {"output_tokens": 1},
                }
            ),
        ]
        return execute(stream, delivered, steps)

    out.append(run_stream("messages_offset_and_usage", "messages", messages_seed, build_messages))

    # S8. Delivered progress after begin() clears the no-progress terminal gate.
    def build_progress_clears_gate(stream, delivered):
        steps = [
            step_begin(False),
            step_observe(
                {
                    "type": "response.output_text.delta",
                    "output_index": 1,
                    "content_index": 0,
                    "item_id": "msg_seed_1",
                    "delta": "!",
                }
            ),
            step_finalize(
                {
                    "type": "response.completed",
                    "response": {"id": "resp_later", "output": []},
                }
            ),
        ]
        return execute(stream, delivered, steps)

    out.append(run_stream("responses_progress_clears_gate", "responses", seed, build_progress_clears_gate))

    # S9. Failure frames bypass the text-constraint gate and survive the
    # overlap machinery as plain passthrough (with remapping).
    def build_failure_frame(stream, delivered):
        steps = [
            step_begin(False),
            step_prepare(
                {
                    "type": "response.failed",
                    "response": {
                        "id": "resp_fail",
                        "status": "failed",
                        "output": [],
                    },
                }
            ),
        ]
        return execute(stream, delivered, steps)

    out.append(run_stream("responses_failure_frame_passthrough", "responses", seed, build_failure_frame))

    # S10. Gemini handoff leaves frames untouched while still stamping the
    # retained header, and drops usageMetadata at finalize.
    _, gemini_seed = seed_gemini_delivered()

    def build_gemini_handoff(stream, delivered):
        steps = [
            step_begin(True),
            step_boundary(),
            step_prepare(
                {
                    "candidates": [
                        {
                            "index": 0,
                            "content": {"parts": [{"text": "handed off"}]},
                        }
                    ]
                }
            ),
            step_finalize(
                {
                    "candidates": [
                        {
                            "index": 0,
                            "finishReason": "STOP",
                            "content": {"parts": [{"text": "handed off"}]},
                        }
                    ],
                    "usageMetadata": {"candidatesTokenCount": 3},
                }
            ),
        ]
        return execute(stream, delivered, steps)

    out.append(run_stream("gemini_handoff_passthrough", "gemini", gemini_seed, build_gemini_handoff))

    # S11. Messages handoff keeps raw frames and overrides usage on the
    # terminal message_delta.
    _, messages_seed = seed_messages_delivered()

    def build_messages_handoff(stream, delivered):
        steps = [
            step_begin(True),
            step_boundary(),
            step_prepare(
                {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "text_delta", "text": "raw"},
                }
            ),
            step_finalize(
                {
                    "type": "message_delta",
                    "delta": {"stop_reason": "end_turn"},
                    "usage": {"output_tokens": 9},
                }
            ),
        ]
        return execute(stream, delivered, steps)

    out.append(run_stream("messages_handoff_passthrough", "messages", messages_seed, build_messages_handoff))

    return out


def uuid5_probes():
    return [
        {
            "namespace": str(namespace),
            "name": name,
            "hex": uuid.uuid5(namespace, name).hex,
        }
        for namespace in NAMESPACES[:2]
        for name in ("msg_new_0", "msg_retry_0", "plain")
    ]


def main():
    payload = {
        "namespaces": [str(value) for value in NAMESPACES],
        "uuid5_probes": uuid5_probes(),
        "scenarios": scenarios(),
    }
    path = OUT / "golden_stream3.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {path}: {len(payload['scenarios'])} stream scenarios")


if __name__ == "__main__":
    main()
