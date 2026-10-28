"""Golden fixtures for the phase-3b Go port: delivered observation on all
three wires and the ContinuationStream mapping layer.

The generator drives the real Python state machines and snapshots every
observable the Go port must reproduce. Token estimation is pinned to the
documented len//4 fallback so the Go tokenizer interface produces identical
usage blocks without a tiktoken dependency. Usage-bearing payloads stay ASCII
and avoid the HTML characters Go's encoding/json escapes differently.
"""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

from free_claude_code.core import delivered_response as dr
from free_claude_code.core.delivered_response import DeliveredResponse


# Pin the estimator to the fallback contract shared with Go's
# DefaultTokenEstimator: 0 for empty text, otherwise max(1, len//4).
def _fallback_estimate(text: str) -> int:
    if not text:
        return 0
    return max(1, len(text) // 4)


dr.estimate_text_tokens = _fallback_estimate

OUT = Path(__file__).resolve().parents[1] / "gocore" / "testdata"
DEFAULT_LIMIT = dr.RECOVERY_RETENTION_BYTES


def observe(wire, events, *, limit=DEFAULT_LIMIT):
    original = dr.RECOVERY_RETENTION_BYTES
    dr.RECOVERY_RETENTION_BYTES = limit
    try:
        delivered = DeliveredResponse(wire)
        trace = []
        for event in events:
            delivered.observe(deepcopy(event))
            trace.append(
                {
                    "progress": delivered.progress,
                    "unsafe": delivered.unsafe_reason or delivered._text_constraint,
                    "retained": delivered._bytes,
                    "limited": delivered._limited,
                }
            )
        items = {
            str(index): delivered.items[index]
            for index in sorted(delivered.items)
        }
        return {
            "wire": wire,
            "limit": limit,
            "events": events,
            "trace": trace,
            "items": items,
            "closed": sorted(delivered.closed),
            "text_closed": sorted(map(list, delivered._text_closed)),
            "part_closed": sorted(map(list, delivered._part_closed)),
            "header": delivered.header,
            "sequence": delivered.sequence,
            "text_constraint": delivered._text_constraint,
            "snapshot": {
                "text": delivered.snapshot().text,
                "thinking": delivered.snapshot().thinking,
                "has_calls": delivered.snapshot().has_calls,
                "eligible": delivered.snapshot().eligible,
                "can_handoff": delivered.snapshot().can_handoff,
                "unsafe_reason": delivered.snapshot().unsafe_reason,
            },
            "closing_events": delivered.closing_events(),
            "usage": delivered.usage(),
            "output_tokens": delivered.output_tokens(),
            "can_handoff": delivered.can_handoff,
        }
    finally:
        dr.RECOVERY_RETENTION_BYTES = original


def responses_header():
    return {
        "type": "response.created",
        "sequence_number": 0,
        "response": {
            "id": "resp_prefix",
            "created_at": 1700000000,
            "status": "in_progress",
            "usage": {"input_tokens": 11, "output_tokens": 0},
        },
    }


def message_item_events(index, item_id, parts_text, *, done=True, start=True):
    """Full responses lifecycle for one message item with output_text."""
    events = []
    content = [
        {"type": "output_text", "text": "", "annotations": []}
        for _ in parts_text
    ]
    if start:
        events.append(
            {
                "type": "response.output_item.added",
                "output_index": index,
                "sequence_number": index * 10 + 1,
                "item": {
                    "id": item_id,
                    "type": "message",
                    "status": "in_progress",
                    "role": "assistant",
                    "content": deepcopy(content),
                },
            }
        )
    for position, text in enumerate(parts_text):
        events.append(
            {
                "type": "response.content_part.added",
                "output_index": index,
                "content_index": position,
                "item_id": item_id,
                "part": {"type": "output_text", "text": "", "annotations": []},
            }
        )
        half = max(1, len(text) // 2)
        events.append(
            {
                "type": "response.output_text.delta",
                "output_index": index,
                "content_index": position,
                "item_id": item_id,
                "delta": text[:half],
            }
        )
        events.append(
            {
                "type": "response.output_text.delta",
                "output_index": index,
                "content_index": position,
                "item_id": item_id,
                "delta": text[half:],
            }
        )
        events.append(
            {
                "type": "response.output_text.done",
                "output_index": index,
                "content_index": position,
                "item_id": item_id,
                "text": text,
            }
        )
        events.append(
            {
                "type": "response.content_part.done",
                "output_index": index,
                "content_index": position,
                "item_id": item_id,
                "part": {
                    "type": "output_text",
                    "text": text,
                    "annotations": [],
                },
            }
        )
        content[position]["text"] = text
    if done:
        events.append(
            {
                "type": "response.output_item.done",
                "output_index": index,
                "sequence_number": index * 10 + 9,
                "item": {
                    "id": item_id,
                    "type": "message",
                    "status": "completed",
                    "role": "assistant",
                    "content": deepcopy(content),
                },
            }
        )
    return events


def delivery_scenarios():
    scenarios = []

    # A1. Two message items: index 0 fully closed, index 1 left open so the
    # synthesized closing boundary is exercised end to end.
    events = [responses_header()]
    events += message_item_events(0, "msg_0", ["Hello "])
    events += message_item_events(1, "msg_1", ["world"], done=False)
    scenarios.append(("responses_two_items", observe("responses", events)))

    # A2. Reasoning item with summary and reasoning parts.
    events = [
        responses_header(),
        {
            "type": "response.output_item.added",
            "output_index": 0,
            "item": {
                "id": "rs_0",
                "type": "reasoning",
                "summary": [],
                "content": [],
            },
        },
        {
            "type": "response.reasoning_summary_part.added",
            "output_index": 0,
            "summary_index": 0,
            "item_id": "rs_0",
            "part": {"type": "summary_text", "text": ""},
        },
        {
            "type": "response.reasoning_summary_text.delta",
            "output_index": 0,
            "summary_index": 0,
            "item_id": "rs_0",
            "delta": "think",
        },
        {
            "type": "response.reasoning_summary_text.done",
            "output_index": 0,
            "summary_index": 0,
            "item_id": "rs_0",
            "text": "think",
        },
        {
            "type": "response.reasoning_summary_part.done",
            "output_index": 0,
            "summary_index": 0,
            "item_id": "rs_0",
            "part": {"type": "summary_text", "text": "think"},
        },
        {
            "type": "response.content_part.added",
            "output_index": 0,
            "content_index": 0,
            "item_id": "rs_0",
            "part": {"type": "reasoning_text", "text": ""},
        },
        {
            "type": "response.reasoning_text.done",
            "output_index": 0,
            "content_index": 0,
            "item_id": "rs_0",
            "text": "reason",
        },
        {
            "type": "response.output_item.done",
            "output_index": 0,
            "item": {
                "id": "rs_0",
                "type": "reasoning",
                "summary": [{"type": "summary_text", "text": "think"}],
                "content": [{"type": "reasoning_text", "text": "reason"}],
            },
        },
    ]
    scenarios.append(("responses_reasoning", observe("responses", events)))

    # A3. Refusal content marks the response unsafe.
    events = [
        responses_header(),
        {
            "type": "response.output_item.added",
            "output_index": 0,
            "item": {
                "id": "rf_0",
                "type": "message",
                "content": [{"type": "refusal", "refusal": ""}],
            },
        },
        {
            "type": "response.refusal.done",
            "output_index": 0,
            "content_index": 0,
            "item_id": "rf_0",
            "refusal": "no",
        },
        {
            "type": "response.output_item.done",
            "output_index": 0,
            "item": {
                "id": "rf_0",
                "type": "message",
                "content": [{"type": "refusal", "refusal": "no"}],
            },
        },
    ]
    scenarios.append(("responses_refusal", observe("responses", events)))

    # A4. Unknown and native-delta events flag unsafe reasons; exclusion
    # lists keep known argument streams quiet.
    events = [
        responses_header(),
        {"type": "response.something_unexpected", "output_index": 0},
        {
            "type": "response.function_call_arguments.delta",
            "output_index": 0,
            "delta": "{}",
        },
        {
            "type": "response.web_search.delta",
            "output_index": 0,
            "delta": "x",
        },
    ]
    scenarios.append(("responses_unsafe_kinds", observe("responses", events)))

    # A5. Client tool calls become snapshot has_calls once finalized.
    events = [
        responses_header(),
        {
            "type": "response.output_item.added",
            "output_index": 0,
            "item": {
                "id": "fc_0",
                "type": "function_call",
                "name": "shell",
                "arguments": "{}",
            },
        },
        {
            "type": "response.output_item.done",
            "output_index": 0,
            "item": {
                "id": "fc_0",
                "type": "function_call",
                "name": "shell",
                "arguments": "{}",
            },
        },
        {
            "type": "response.output_item.added",
            "output_index": 1,
            "item": {
                "id": "ts_0",
                "type": "tool_search_call",
                "execution": "client",
            },
        },
    ]
    scenarios.append(("responses_client_calls", observe("responses", events)))

    # A6. Text constraints reject synthetic continuations.
    structured = [
        {
            "type": "response.completed",
            "response": {
                "text": {"format": {"type": "json_schema", "name": "x"}},
                "output": [],
            },
        }
    ]
    scenarios.append(
        ("responses_structured_constraint", observe("responses", structured))
    )
    logprobs = [
        {
            "type": "response.content_part.added",
            "output_index": 0,
            "content_index": 0,
            "item_id": "lp_0",
            "part": {"type": "output_text", "text": "", "logprobs": []},
        }
    ]
    scenarios.append(("responses_logprobs_constraint", observe("responses", logprobs)))

    # A7. Retention exhaustion clears retained state and freezes tokens.
    big = message_item_events(0, "big_0", ["x" * 2000])
    scenarios.append(
        ("responses_retention_limit", observe("responses", big, limit=400))
    )

    # A8. Gemini: header capture, text accumulation, thoughts, calls and the
    # opaque/unsupported unsafe reasons.
    gemini_events = [
        {"responseId": "gem-1", "modelVersion": "gem-1.5",
         "candidates": [{"index": 0, "content": {"parts": [
             {"text": "Hel"}, {"text": "lo"}, {"text": "secret", "thought": True},
             {"functionCall": {"name": "shell", "args": {}}},
             {"text": "opaque", "thoughtSignature": "sig"},
             {"text": "weird", "exoticKey": 1},
         ]}}]},
        {"responseId": "gem-1", "modelVersion": "gem-1.5",
         "candidates": [{"index": 0, "finishReason": "STOP",
                         "content": {"parts": [{"text": ""}]}}]},
    ]
    scenarios.append(("gemini_mixed_parts", observe("gemini", gemini_events)))

    # A9. Messages smoke test for the shared Observe entry point.
    messages_events = [
        {"type": "message_start", "message": {"id": "msg_start", "usage": {}}},
        {"type": "content_block_start", "index": 0,
         "content_block": {"type": "text", "text": ""}},
        {"type": "content_block_delta", "index": 0,
         "delta": {"type": "text_delta", "text": "hi"}},
        {"type": "content_block_stop", "index": 0},
        {"type": "message_delta", "delta": {"stop_reason": "end_turn"},
         "usage": {"output_tokens": 1}},
        {"type": "message_stop"},
    ]
    scenarios.append(("messages_smoke", observe("messages", messages_events)))

    return [{"name": name, **result} for name, result in scenarios]


def main():
    delivery = delivery_scenarios()
    payload = {"default_limit": DEFAULT_LIMIT, "scenarios": delivery}
    path = OUT / "golden_delivery3.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {path}: {len(delivery)} delivery scenarios")


if __name__ == "__main__":
    main()
