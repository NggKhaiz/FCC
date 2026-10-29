"""Generate parity fixtures for the protocol-neutral Chat stream lifecycle."""

import json
from pathlib import Path

from free_claude_code.providers.openai_chat.stream_output import (
    ChatStreamOutput,
    ChatStreamUsage,
)

OUTPUT = Path("gocore/testdata/golden_openaichat4c4b_stream_output.json")


class RecordingOutput(ChatStreamOutput):
    def _start_events(self):
        return ["start"]

    def _start_reasoning_block(self, source):
        return [f"reasoning_start:{source}"]

    def _emit_reasoning_delta(self, content, *, summary=False, source=0):
        return f"reasoning_delta:{source}:{int(summary)}:{content}"

    def _emit_opaque_reasoning(self, data):
        return [f"opaque:{data}"]

    def _attach_reasoning_replay(self, data, source):
        return [f"attach:{source}:{data}"]

    def _stop_reasoning_block(self, source):
        return [f"reasoning_stop:{source}"]

    def _start_text_block(self):
        return ["text_start"]

    def _emit_text_delta(self, content):
        return f"text_delta:{content}"

    def _stop_text_block(self):
        return ["text_stop"]

    def _start_tool_block(self, index, state):
        return f"tool_start:{index}:{state.tool_id}:{state.name}"

    def _emit_tool_delta(self, index, state, partial_json):
        return f"tool_delta:{index}:{partial_json}"

    def _stop_tool_block(self, index, state):
        return [f"tool_stop:{index}"]

    def _finish_success(self, *, stop_reason, usage):
        return [f"success:{stop_reason}"]

    def _finish_failure(self, failure):
        return ["failure"]


class RecordingReplay:
    def __init__(self):
        self.active = False

    def before_reasoning(self, output):
        return iter(["before_reasoning"])

    def before_content(self, output):
        return iter(["before_content"])

    def finish(self, output, *, completed=True):
        return iter([f"replay_finish:{int(completed)}"])


def snapshot(output):
    return {
        "text": output.accumulated_text,
        "reasoning": output.accumulated_reasoning,
        "committed": output.committed_output,
        "has_tool": output.has_emitted_tool_block(),
        "stop_reason": output.final_stop_reason("tool_use"),
        "tools": [
            {
                "index": index,
                "id": state.tool_id,
                "name": state.name,
                "extra": state.extra_content,
                "started": state.started,
                "open": state.open,
                "content": state.content,
            }
            for index, state in output.tool_states.items()
        ],
    }


def execute(output, step, replay):
    op = step["op"]
    index = step.get("index", 0)
    value = step.get("value", "")
    if op == "start":
        return output.start_events()
    if op == "reasoning":
        return output.ensure_reasoning_block(source=index)
    if op == "begin":
        return output.begin_reasoning_record(source=index)
    if op == "reasoning_delta":
        return [
            output.emit_reasoning_delta(
                value, summary=step.get("summary", False), source=index
            )
        ]
    if op == "complete":
        return output.complete_reasoning_record(
            list(step.get("opaque", [])), source=index
        )
    if op == "opaque":
        return output.emit_opaque_reasoning(value)
    if op == "defer":
        output.defer_opaque_reasoning(value)
        return []
    if op == "flush":
        return output.flush_reasoning_replay(completed=step.get("completed", True))
    if op == "text":
        return output.ensure_text_block()
    if op == "text_delta":
        return [output.emit_text_delta(value)]
    if op == "close_content":
        return output.close_content_blocks()
    if op == "finish_reasoning":
        return output.finish_reasoning_group()
    if op == "name":
        output.register_tool_name(index, value)
        return []
    if op == "extra":
        output.set_tool_extra_content(index, step.get("extra"))
        return []
    if op == "tool_start":
        return [
            output.start_tool_block(
                index, step["id"], value, extra_content=step.get("extra")
            )
        ]
    if op == "tool_delta":
        return [output.emit_tool_delta(index, value)]
    if op == "tool_stop":
        return output.stop_tool_block(index)
    if op == "close_all":
        return output.close_all_blocks()
    if op == "close_unclosed":
        return output.close_unclosed_blocks()
    if op == "success":
        return output.finish_success(stop_reason=value, usage=ChatStreamUsage(1, 2))
    if op == "failure":
        return output.finish_failure(None)
    if op == "active":
        replay.active = step["active"]
        return []
    raise ValueError(op)


def main():
    scenarios = [
        (
            "blocks_and_success",
            False,
            [
                {"op": "start"},
                {"op": "reasoning", "index": 4},
                {"op": "reasoning", "index": 4},
                {"op": "reasoning_delta", "index": 4, "value": "why"},
                {"op": "complete", "index": 4, "opaque": ["signature", "redacted", ""]},
                {"op": "text"},
                {"op": "text_delta", "value": "hello"},
                {"op": "text"},
                {"op": "reasoning", "index": 2},
                {"op": "reasoning_delta", "index": 2, "value": "sum", "summary": True},
                {"op": "text"},
                {"op": "success", "value": "tool_use"},
                {"op": "failure"},
                {"op": "success", "value": "end_turn"},
            ],
        ),
        (
            "tools_and_order",
            False,
            [
                {"op": "extra", "index": 9, "extra": {}},
                {"op": "name", "index": 9, "value": "tool"},
                {"op": "name", "index": 9, "value": "tool_read"},
                {"op": "name", "index": 9, "value": "tool"},
                {"op": "extra", "index": 9, "extra": {"provider": "x"}},
                {"op": "tool_start", "index": 9, "id": "a", "value": "tool_read"},
                {"op": "tool_delta", "index": 9, "value": '{"x":'},
                {"op": "tool_delta", "index": 9, "value": "1}"},
                {"op": "tool_start", "index": 2, "id": "b", "value": "second"},
                {"op": "close_all"},
                {"op": "tool_stop", "index": 9},
                {"op": "success", "value": "stop"},
            ],
        ),
        (
            "failure_cleanup",
            False,
            [
                {"op": "begin", "index": 3},
                {"op": "begin", "index": 1},
                {"op": "tool_start", "index": 5, "id": "t", "value": "work"},
                {"op": "defer", "value": "not_emitted"},
                {"op": "failure"},
                {"op": "success", "value": "stop"},
            ],
        ),
        (
            "reasoning_replay",
            True,
            [
                {"op": "active", "active": True},
                {"op": "reasoning", "index": 3},
                {"op": "begin", "index": 1},
                {"op": "text"},
                {"op": "defer", "value": "opaque_a"},
                {"op": "active", "active": False},
                {"op": "close_content"},
                {"op": "flush", "completed": False},
                {"op": "defer", "value": "opaque_b"},
                {"op": "finish_reasoning"},
                {"op": "success", "value": "stop"},
            ],
        ),
        (
            "missing_source_and_empty",
            False,
            [
                {"op": "complete", "index": 5, "opaque": ["one", "", "two"]},
                {"op": "opaque", "value": ""},
                {"op": "defer", "value": ""},
                {"op": "flush"},
                {"op": "close_unclosed"},
            ],
        ),
    ]
    results = []
    for name, use_replay, steps in scenarios:
        output = RecordingOutput(input_tokens=1)
        replay = RecordingReplay() if use_replay else None
        output.reasoning_replay = replay
        results.append(
            {
                "name": name,
                "replay": use_replay,
                "steps": [
                    {
                        "input": step,
                        "events": execute(output, step, replay),
                        "state": snapshot(output),
                    }
                    for step in steps
                ],
            }
        )
    OUTPUT.write_text(
        json.dumps(
            {"schema": "phase4c4b-stream-output-v1", "scenarios": results},
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"wrote {OUTPUT}: {len(results)} scenarios")


if __name__ == "__main__":
    main()
