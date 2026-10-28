"""Generate golden data for the phase-3 Go package (continuation overlap).

Run from the repository root:
    .venv/bin/python bench_local/generate_golden_phase3.py

The Python ContinuationOverlap remains the specification. The generator
reproduces the event shapes of tests/api/test_continuation_overlap.py's
responses_parts helper without importing the heavyweight test harness.
"""

import json
import sys
from copy import deepcopy
from pathlib import Path

sys.path.insert(0, "src")

from free_claude_code.core import continuation_overlap as co_module
from free_claude_code.core.continuation_overlap import (
    ContinuationOverlap,
)
from free_claude_code.core.failures import ExecutionFailure

OUTPUT = Path("gocore/testdata/golden_phase3.json")
DEFAULT_LIMIT = co_module.RECOVERY_RETENTION_BYTES


# ------------------------------------------------------------- event builders
def responses_events(parts, *, separate_items=False, representation="deltas"):
    """Mirror responses_parts() from the API continuation test suite."""
    template_created = {
        "type": "response.created",
        "response": {"id": "resp_golden", "status": "queued", "output": []},
    }
    template_completed = {
        "type": "response.completed",
        "response": {"id": "resp_golden", "status": "completed", "output": []},
    }
    groups = [[part] for part in parts] if separate_items else [parts]
    events = [template_created]
    items = []
    for index, group in enumerate(groups):
        item = {
            "id": f"msg_piece_{index}",
            "type": "message",
            "role": "assistant",
            "status": "completed",
            "content": [
                {"type": "output_text", "text": text, "annotations": []}
                for text in group
            ],
        }
        items.append(item)
        added_item = {
            **item,
            "content": item["content"]
            if representation == "item_added"
            else [],
            "status": "in_progress",
        }
        events.append(
            {"type": "response.output_item.added", "output_index": index, "item": added_item}
        )
        for position, part in enumerate(item["content"]):
            identity = {
                "output_index": index,
                "item_id": item["id"],
                "content_index": position,
            }
            shown = (
                part["text"]
                if representation in {"part_added", "item_added"}
                else part["text"][:1]
                if representation == "mixed"
                else ""
            )
            events.append(
                {
                    "type": "response.content_part.added",
                    **identity,
                    "part": {**part, "text": shown},
                }
            )
            if representation in {"deltas", "mixed"}:
                tail = part["text"][1:] if representation == "mixed" else part["text"]
                events.extend(
                    {
                        "type": "response.output_text.delta",
                        **identity,
                        "delta": character,
                    }
                    for character in tail
                )
            if representation not in {"item_done", "part_done", "terminal"}:
                events.append(
                    {"type": "response.output_text.done", **identity, "text": part["text"]}
                )
            if representation not in {"item_done", "terminal"}:
                events.append(
                    {"type": "response.content_part.done", **identity, "part": part}
                )
        events.append(
            {"type": "response.output_item.done", "output_index": index, "item": item}
        )
    terminal = deepcopy(template_completed)
    terminal["response"]["output"] = items
    events.append(terminal)
    if representation == "terminal":
        events = [events[0], terminal]
    return [
        {**deepcopy(event), "sequence_number": number}
        for number, event in enumerate(events)
    ]


def messages_events(text):
    """Canonical messages-wire replay of one text block."""
    return [
        {
            "type": "content_block_start",
            "index": 0,
            "content_block": {"type": "text", "text": ""},
        },
        *[
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": ch},
            }
            for ch in text
        ],
        {"type": "content_block_stop", "index": 0},
        {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn", "stop_sequence": None},
            "usage": {"output_tokens": 4},
        },
        {"type": "message_stop"},
    ]


def gemini_events(parts, *, thought_parts=()):
    candidates_parts = [
        {"text": text, "thought": True} for text in thought_parts
    ] + [{"text": text} for text in parts]
    return [
        {
            "responseId": "gem_golden",
            "candidates": [
                {"index": 0, "content": {"parts": deepcopy(candidates_parts)}}
            ],
        },
        {
            "responseId": "gem_golden",
            "candidates": [
                {
                    "index": 0,
                    "finishReason": "STOP",
                    "content": {"parts": deepcopy(candidates_parts)},
                }
            ],
        },
    ]


# ------------------------------------------------------------------- runner
def run_feeds(prefix, feeds, *, limit=DEFAULT_LIMIT):
    original = co_module.RECOVERY_RETENTION_BYTES
    co_module.RECOVERY_RETENTION_BYTES = limit
    source_copy = deepcopy(feeds)

    class TracingOverlap(ContinuationOverlap):
        def __init__(self, expected):
            super().__init__(expected)
            self.size_trace = []

        def _check_size(self):
            ids_size = self._size(self._ids)
            parts_size = sum(
                self._size((key, part.order, part.text, part.seen))
                for key, part in self._parts.items()
            )
            total = self._frame_bytes + ids_size + parts_size
            self.size_trace.append(
                {
                    "frame_bytes": self._frame_bytes,
                    "ids_size": ids_size,
                    "parts_size": parts_size,
                    "total": total,
                }
            )
            super()._check_size()

    try:
        overlap = TracingOverlap(prefix)
        steps = []
        for event in feeds:
            try:
                ready = overlap.feed(event)
                steps.append({"ok": True, "events": ready})
            except ExecutionFailure as exc:
                # The failure is non-fatal to the state machine: the policy
                # test keeps feeding frames after the retained-limit error,
                # so the golden trace records every remaining feed as well.
                steps.append({"ok": False, "message": exc.message})
        return {
            "feeds": deepcopy(feeds),
            "steps": steps,
            "size_trace": overlap.size_trace,
            "source_unchanged": source_copy == feeds,
        }
    finally:
        co_module.RECOVERY_RETENTION_BYTES = original


def matrix_scenarios():
    combos = [
        ("Hello ", ["He", "llo world."]),
        ("abcabc", ["", "abc", "", "abcX"]),
        ("abcabd", ["ab", "cX"]),
        ("Hello 世界", ["世", "界!"]),
        ("The answer is", ["answer", " is"]),
        ("Hello ", ["He", "l"]),
    ]
    representations = [
        "deltas",
        "text_done",
        "part_done",
        "item_done",
        "terminal",
        "part_added",
        "item_added",
        "mixed",
    ]
    scenarios = []
    for prefix, parts in combos:
        for representation in representations:
            for separate_items in (False, True):
                feeds = responses_events(
                    parts,
                    separate_items=separate_items,
                    representation=representation,
                )
                scenarios.append(
                    {
                        "name": f"matrix::{prefix!r}::{representation}::separate={separate_items}",
                        "wire": "responses",
                        "prefix": prefix,
                        "limit": DEFAULT_LIMIT,
                        **run_feeds(prefix, feeds),
                    }
                )
    return scenarios


def edge_scenarios():
    scenarios = []

    # 1. Empty deltas stay invisible when there is no prior answer text.
    for wire, event in (
        (
            "messages",
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": ""},
            },
        ),
        (
            "responses",
            {
                "type": "response.output_text.delta",
                "output_index": 0,
                "content_index": 0,
                "item_id": "msg_text",
                "delta": "",
            },
        ),
    ):
        scenarios.append(
            {
                "name": f"empty_delta_invisible::{wire}",
                "wire": wire,
                "prefix": "",
                "limit": DEFAULT_LIMIT,
                **run_feeds("", [event]),
            }
        )

    # 2. Repeated snapshots are idempotent; ping keeps lifecycle order.
    source = responses_events(["He", "llo world."], representation="text_done")
    source.insert(4, deepcopy(source[3]))
    source.insert(5, {"type": "ping", "marker": "between repeated snapshots"})
    scenarios.append(
        {
            "name": "pending_snapshot_idempotent",
            "wire": "responses",
            "prefix": "Hello ",
            "limit": DEFAULT_LIMIT,
            **run_feeds("Hello ", source),
        }
    )

    # 3. Interleaved parts wait for the earlier open part.
    base = responses_events(["He", "llo world."], representation="text_done")
    interleaved = [*base[:3], *base[5:8], *base[3:5], *base[8:]]
    scenarios.append(
        {
            "name": "interleaved_parts_wait",
            "wire": "responses",
            "prefix": "Hello ",
            "limit": DEFAULT_LIMIT,
            **run_feeds("Hello ", interleaved),
        }
    )

    # 4. Late snapshot cannot rewrite a decided prefix.
    for change in ("prefix", "order", "insert"):
        source = responses_events(
            ["He", "llo world."], representation="text_done", separate_items=True
        )
        overlap = ContinuationOverlap("Hello ")
        for event in source[:-1]:
            overlap.feed(event)
        terminal = source[-1]
        items = terminal["response"]["output"]
        if change == "prefix":
            items[0]["content"][0]["text"] = "Ha"
        elif change == "order":
            items.reverse()
        else:
            extra = deepcopy(items[0])
            extra["id"] = "msg_late"
            items.insert(0, extra)
        scenarios.append(
            {
                "name": f"late_snapshot_{change}",
                "wire": "responses",
                "prefix": "Hello ",
                "limit": DEFAULT_LIMIT,
                **run_feeds("Hello ", source),
            }
        )

    # 5. A terminal that omits pending source text is inconsistent.
    source = responses_events(["He"], representation="text_done")
    source[-1]["response"]["output"] = []
    scenarios.append(
        {
            "name": "terminal_omits_pending_text",
            "wire": "responses",
            "prefix": "Hello ",
            "limit": DEFAULT_LIMIT,
            **run_feeds("Hello ", source),
        }
    )

    # 6. Unresolved frames are bounded; a failure frame discards the buffer.
    source = responses_events(["He"], representation="text_done")
    padded = [
        *source[:4],
        *({"type": "ping", "padding": "x" * 60} for _ in range(30)),
        {"type": "response.failed", "response": {"output": []}},
    ]
    scenarios.append(
        {
            "name": "bounded_buffer_then_failure_discard",
            "wire": "responses",
            "prefix": "Hello ",
            "limit": 1000,
            **run_feeds("Hello ", padded, limit=1000),
        }
    )
    failure = {"type": "response.failed", "response": {"output": []}}
    scenarios.append(
        {
            "name": "failure_frame_discards_pending",
            "wire": "responses",
            "prefix": "Hello ",
            "limit": DEFAULT_LIMIT,
            **run_feeds("Hello ", [*source[:4], failure]),
        }
    )

    # 7. A large immediately resolvable snapshot never fills the buffer.
    large = responses_events(["Hello " + "x" * 1000], representation="terminal")
    scenarios.append(
        {
            "name": "large_snapshot_resolves_immediately",
            "wire": "responses",
            "prefix": "Hello ",
            "limit": 300,
            **run_feeds("Hello ", large, limit=300),
        }
    )

    # 8. After resolution, unaffected later items pass through untouched.
    source = responses_events(["Hello world."], representation="text_done")
    tail = [
        {
            "type": "response.output_item.done",
            "output_index": index,
            "item": {
                "id": f"msg_{index}",
                "type": "message",
                "content": [{"type": "output_text", "text": "More."}],
            },
        }
        for index in range(1, 60)
    ]
    scenarios.append(
        {
            "name": "resolved_later_items_passthrough",
            "wire": "responses",
            "prefix": "Hello ",
            "limit": 400,
            **run_feeds("Hello ", [*source[:-1], *tail], limit=400),
        }
    )

    # 9. Messages wire: eager text in content_block_start splits into delta.
    eager = [
        {
            "type": "content_block_start",
            "index": 0,
            "content_block": {"type": "text", "text": "Hello world."},
        },
        {"type": "content_block_stop", "index": 0},
        {"type": "message_stop"},
    ]
    scenarios.append(
        {
            "name": "messages_eager_start_splits",
            "wire": "messages",
            "prefix": "Hello ",
            "limit": DEFAULT_LIMIT,
            **run_feeds("Hello ", eager),
        }
    )
    scenarios.append(
        {
            "name": "messages_delta_stream",
            "wire": "messages",
            "prefix": "Hello ",
            "limit": DEFAULT_LIMIT,
            **run_feeds("Hello ", messages_events("Hello world.")),
        }
    )

    # 10. Gemini: thoughts are ignored by the overlap text scan.
    scenarios.append(
        {
            "name": "gemini_thoughts_excluded",
            "wire": "gemini",
            "prefix": "Hello ",
            "limit": DEFAULT_LIMIT,
            **run_feeds("Hello ", gemini_events(["Hello world."], thought_parts=["inner"])),
        }
    )

    # 11. Every error surface clears pending frames.
    error_events = (
        {"type": "error", "error": {"type": "api_error"}},
        {"type": "response.error", "error": {"message": "boom"}},
        {"type": "response.failed", "response": {"error": {"code": "rate_limit"}}},
        {"type": "response.output_text.delta", "item_id": "x", "error": "embedded"},
    )
    scenarios.extend(
        {
            "name": f"error_clears::{error_event['type']}",
            "wire": "responses",
            "prefix": "Hello ",
            "limit": DEFAULT_LIMIT,
            **run_feeds("Hello ", [*source[:4], error_event]),
        }
        for error_event in error_events
    )

    return scenarios


def size_probes():
    """Lock the simplejson-compatible byte sizing used by the bound checks."""
    import simplejson

    values = [
        {"type": "ping", "padding": "x" * 60},
        {"a": 1, "b": [True, None, "text 中文"]},
        [],
        {},
        ["He", "llo world."],
        "plain string",
        0,
    ]
    return [
        {
            "value": value,
            "size": len(
                simplejson.dumps(
                    value, use_decimal=True, ensure_ascii=False
                ).encode("utf-8")
            ),
        }
        for value in values
    ]


def main() -> None:
    golden = {
        "default_limit": DEFAULT_LIMIT,
        "size_probes": size_probes(),
        "scenarios": [*matrix_scenarios(), *edge_scenarios()],
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(golden, ensure_ascii=False, indent=2))
    print(
        f"wrote {OUTPUT}: {len(golden['scenarios'])} overlap scenarios, "
        f"{len(golden['size_probes'])} size probes"
    )


if __name__ == "__main__":
    main()
