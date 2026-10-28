"""Generate golden data for the phase-2 Go packages (recovery, delivery, toolbuf).

Run from the repository root:
    .venv/bin/python bench_local/generate_golden_phase2.py

The Python implementation remains the specification; every expected state is
captured from the real classes rather than re-derived for Go.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, "src")

from free_claude_code.api.stream_frames import frame_data
from free_claude_code.api.tool_call_buffer import ToolCallBuffer
from free_claude_code.core import delivered_response as dr_module
from free_claude_code.core.delivered_response import DeliveredResponse
from free_claude_code.core.stream_recovery import (
    DeliveredPrefix,
    SourceRecoverySnapshot,
    assess_recovery,
)

OUTPUT = Path("gocore/testdata/golden_phase2.json")


def frame(event: str, payload: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


# --------------------------------------------------------------------- recovery
def recovery_cases() -> list[dict]:
    prefix = DeliveredPrefix(
        text="hello",
        thinking="reasoning",
        has_calls=False,
        eligible=True,
        can_handoff=True,
        unsafe_reason=None,
        retained_bytes=123,
    )
    calls = DeliveredPrefix(
        text="",
        thinking="",
        has_calls=True,
        eligible=True,
        can_handoff=True,
        unsafe_reason=None,
    )
    unsafe = DeliveredPrefix(
        text="x",
        thinking="",
        has_calls=False,
        eligible=False,
        can_handoff=False,
        unsafe_reason="native_content",
    )
    empty = DeliveredPrefix(
        text="",
        thinking="",
        has_calls=False,
        eligible=True,
        can_handoff=True,
        unsafe_reason=None,
    )
    source_clean = SourceRecoverySnapshot()
    source_stop = SourceRecoverySnapshot(normal_stop_seen=True)
    source_structured = SourceRecoverySnapshot(structured_output=True)
    source_unsafe = SourceRecoverySnapshot(unsafe_reason="opaque_delta")
    source_no_handoff = SourceRecoverySnapshot(can_handoff=False)

    rows = [
        ("normal stop", False, None, source_stop, True),
        ("restart when nothing released", False, None, source_clean, True),
        ("stop on unsafe source before release", False, None, source_unsafe, True),
        ("missing prefix after release", True, None, source_clean, True),
        ("handoff completed calls", True, calls, source_clean, True),
        ("calls but not retryable", True, calls, source_clean, False),
        ("calls but structured output", True, calls, source_structured, True),
        ("calls but source forbids handoff", True, calls, source_no_handoff, True),
        (
            "calls but prefix forbids handoff",
            True,
            DeliveredPrefix("", "", True, True, False, None),
            source_clean,
            True,
        ),
        ("structured output stops", True, prefix, source_structured, True),
        ("source unsafe reason wins", True, prefix, source_unsafe, True),
        ("prefix unsafe reason stops", True, unsafe, source_clean, True),
        ("ineligible prefix stops", True, unsafe, source_clean, True),
        ("empty prefix stops", True, empty, source_clean, True),
        ("portable prefix continues", True, prefix, source_clean, True),
    ]
    cases = []
    for name, released, pref, source, retryable in rows:
        assessment = assess_recovery(
            content_released=released, prefix=pref, source=source, retryable=retryable
        )
        cases.append(
            {
                "name": name,
                "content_released": released,
                "prefix": None
                if pref is None
                else {
                    "text": pref.text,
                    "thinking": pref.thinking,
                    "has_calls": pref.has_calls,
                    "eligible": pref.eligible,
                    "can_handoff": pref.can_handoff,
                    "unsafe_reason": pref.unsafe_reason,
                },
                "source": {
                    "normal_stop_seen": source.normal_stop_seen,
                    "structured_output": source.structured_output,
                    "unsafe_reason": source.unsafe_reason,
                    "can_handoff": source.can_handoff,
                },
                "retryable": retryable,
                "expect": {
                    "action": assessment.action.value,
                    "reason": assessment.reason,
                    "seed": None
                    if assessment.seed is None
                    else {
                        "text": assessment.seed.text,
                        "thinking": assessment.seed.thinking,
                    },
                },
            }
        )
    return cases


# --------------------------------------------------------------------- delivery
def message_start() -> dict:
    return {
        "type": "message_start",
        "message": {
            "id": "msg_golden",
            "type": "message",
            "role": "assistant",
            "model": "claude-sonnet-4",
            "content": [],
            "usage": {"input_tokens": 11, "output_tokens": 1},
        },
    }


def snapshot_state(d: DeliveredResponse) -> dict:
    prefix = d.snapshot()
    return {
        "progress": d.progress,
        "retention_exhausted": d.retention_exhausted,
        "unsafe_reason": d.unsafe_reason,
        "snapshot": {
            "text": prefix.text,
            "thinking": prefix.thinking,
            "has_calls": prefix.has_calls,
            "eligible": prefix.eligible,
            "can_handoff": prefix.can_handoff,
            "unsafe_reason": prefix.unsafe_reason,
            "retained_bytes": prefix.retained_bytes,
        },
        "items_size": len(d.items),
    }


def delivery_healthy() -> dict:
    d = DeliveredResponse("messages")
    events = []

    def step(payload: dict, count: bool = True) -> None:
        d.observe(payload, count_progress=count)
        events.append(
            {
                "payload": payload,
                "count_progress": count,
                "state": snapshot_state(d),
                "closing_events": d.closing_events(),
            }
        )

    step(message_start())
    step({"type": "ping"})
    step(
        {
            "type": "content_block_start",
            "index": 0,
            "content_block": {"type": "text", "text": "go"},
        }
    )
    for chunk in ("alpha ", "beta 中文", " gamma"):
        step(
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": chunk},
            }
        )
    step({"type": "content_block_stop", "index": 0})
    step(
        {
            "type": "content_block_start",
            "index": 1,
            "content_block": {"type": "thinking", "thinking": ""},
        }
    )
    step(
        {
            "type": "content_block_delta",
            "index": 1,
            "delta": {"type": "thinking_delta", "thinking": "reasoning"},
        }
    )
    step(
        {
            "type": "content_block_delta",
            "index": 1,
            "delta": {"type": "signature_delta", "signature": "sig_abc"},
        }
    )
    # Closing events while the thinking block is still open.
    mid_closing = d.closing_events()
    step({"type": "content_block_stop", "index": 1})
    step(
        {
            "type": "content_block_start",
            "index": 2,
            "content_block": {
                "type": "tool_use",
                "id": "toolu_1",
                "name": "Bash",
                "input": {},
            },
        }
    )
    step(
        {
            "type": "content_block_delta",
            "index": 2,
            "delta": {"type": "input_json_delta", "partial_json": '{"command":'},
        }
    )
    step(
        {
            "type": "content_block_delta",
            "index": 2,
            "delta": {"type": "input_json_delta", "partial_json": '"ls"}'},
        }
    )
    step({"type": "content_block_stop", "index": 2})
    step({"type": "message_delta", "delta": {"stop_reason": "tool_use"}})
    step({"type": "message_stop"})
    return {
        "name": "healthy_mixed_stream",
        "wire_api": "messages",
        "limit": dr_module.RECOVERY_RETENTION_BYTES,
        "events": events,
        "mid_closing_events": mid_closing,
        "final_closing_events": d.closing_events(),
        "handoff_events": d.handoff_events(),
        "final": snapshot_state(d),
    }


def delivery_unsafe() -> dict:
    d = DeliveredResponse("messages")
    events = []

    def step(payload: dict) -> None:
        d.observe(payload)
        events.append(
            {
                "payload": payload,
                "count_progress": True,
                "state": snapshot_state(d),
                "closing_events": d.closing_events(),
            }
        )

    step({"type": "message_start", "message": message_start()["message"]})
    # Unknown indexed event.
    step({"type": "mystery_event", "index": 0})
    # Native content block type.
    step(
        {
            "type": "content_block_start",
            "index": 1,
            "content_block": {"type": "server_tool_xyz"},
        }
    )
    # Delta for a block that was never opened.
    step(
        {
            "type": "content_block_delta",
            "index": 9,
            "delta": {"type": "text_delta", "text": "orphan"},
        }
    )
    # Opaque signature on a thinking block.
    step(
        {
            "type": "content_block_start",
            "index": 2,
            "content_block": {"type": "thinking"},
        }
    )
    step(
        {
            "type": "content_block_delta",
            "index": 2,
            "delta": {"type": "signature_delta", "signature": "opaque_sig"},
        }
    )
    step({"type": "content_block_stop", "index": 2})
    # Non-string type field.
    step({"type": 123})
    return {
        "name": "unsafe_classifications",
        "wire_api": "messages",
        "events": events,
        "final_closing_events": d.closing_events(),
        "handoff_events": d.handoff_events(),
        "final": snapshot_state(d),
    }


def delivery_retention_limit() -> dict:
    original = dr_module.RECOVERY_RETENTION_BYTES
    dr_module.RECOVERY_RETENTION_BYTES = 100
    try:
        d = DeliveredResponse("messages")
        events = []
        for _ in range(12):
            d.observe(
                {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "text_delta", "text": "x" * 40},
                }
            )
            events.append(snapshot_state(d))
        return {
            "name": "retention_limit_clears_evidence",
            "wire_api": "messages",
            "limit": 100,
            "states": events,
            "final": snapshot_state(d),
        }
    finally:
        dr_module.RECOVERY_RETENTION_BYTES = original


# ----------------------------------------------------------------------- toolbuf
def toolbuf_messages() -> dict:
    buf = ToolCallBuffer("messages")
    calls = []

    def feed(fr: str) -> None:
        calls.append({"frame": fr, "released": buf.feed(fr)})

    # Text block frames pass straight through before any call begins.
    feed(
        frame(
            "content_block_start",
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "text"},
            },
        )
    )
    # Tool call group is withheld until its stop.
    feed(
        frame(
            "content_block_start",
            {
                "type": "content_block_start",
                "index": 1,
                "content_block": {"type": "tool_use", "id": "toolu_9"},
            },
        )
    )
    feed(
        frame(
            "content_block_delta",
            {
                "type": "content_block_delta",
                "index": 1,
                "delta": {"type": "input_json_delta", "partial_json": "{}"},
            },
        )
    )
    # An unrelated frame arriving mid-group is buffered with the group.
    feed(frame("ping", {"type": "ping"}))
    feed(frame("content_block_stop", {"type": "content_block_stop", "index": 1}))
    # After the flush, ordinary frames pass through again.
    feed(frame("message_stop", {"type": "message_stop"}))

    # A second group that never completes is discarded by message_stop.
    feed(
        frame(
            "content_block_start",
            {
                "type": "content_block_start",
                "index": 2,
                "content_block": {"type": "tool_use", "id": "toolu_10"},
            },
        )
    )
    feed(
        frame(
            "content_block_delta",
            {
                "type": "content_block_delta",
                "index": 2,
                "delta": {"type": "input_json_delta", "partial_json": "{"},
            },
        )
    )
    feed(frame("message_stop", {"type": "message_stop"}))
    feed(frame("ping", {"type": "ping"}))
    return {"name": "messages_holdback", "wire_api": "messages", "calls": calls}


def toolbuf_responses() -> dict:
    buf = ToolCallBuffer("responses")
    calls = []

    def feed(fr: str) -> list:
        released = buf.feed(fr)
        parsed = []
        for item in released:
            data = frame_data(item)
            parsed.append({"frame": item, "payload": data})
        calls.append({"frame": fr, "released": parsed})
        return released

    completed_item = {
        "id": "fc_1",
        "type": "function_call",
        "status": "completed",
        "name": "go",
        "arguments": "{}",
    }
    unfinished_item = {
        "id": "fc_2",
        "type": "function_call",
        "status": "in_progress",
        "name": "go",
        "arguments": "",
    }
    feed(
        frame(
            "response.output_item.done",
            {
                "type": "response.output_item.done",
                "output_index": 0,
                "item": completed_item,
            },
        )
    )
    feed(
        frame(
            "response.function_call_arguments.delta",
            {
                "type": "response.function_call_arguments.delta",
                "item_id": "fc_2",
                "output_index": 1,
                "delta": "{}",
            },
        )
    )
    feed(
        frame(
            "response.output_item.done",
            {
                "type": "response.output_item.done",
                "output_index": 1,
                "item": unfinished_item,
            },
        )
    )
    # Terminal snapshot carries both calls; the unfinished one must be filtered.
    terminal = frame(
        "response.completed",
        {
            "type": "response.completed",
            "response": {
                "id": "resp_1",
                "status": "completed",
                "output": [completed_item, unfinished_item],
            },
        },
    )
    feed(terminal)
    # A failure frame discards whatever group is open.
    feed(
        frame(
            "response.failed",
            {
                "type": "response.failed",
                "response": {
                    "id": "resp_2",
                    "output": [unfinished_item],
                    "status": "failed",
                    "error": {"code": "rate_limit"},
                },
            },
        )
    )
    return {
        "name": "responses_snapshot_filter",
        "wire_api": "responses",
        "calls": calls,
    }


def main() -> None:
    golden = {
        "recovery": recovery_cases(),
        "delivery": [
            delivery_healthy(),
            delivery_unsafe(),
            delivery_retention_limit(),
        ],
        "toolbuf": [toolbuf_messages(), toolbuf_responses()],
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(golden, ensure_ascii=False, indent=2))
    print(
        f"wrote {OUTPUT}: {len(golden['recovery'])} recovery, "
        f"{len(golden['delivery'])} delivery, {len(golden['toolbuf'])} toolbuf scenarios"
    )


if __name__ == "__main__":
    main()
