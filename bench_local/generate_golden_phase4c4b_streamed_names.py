"""Generate streamed tool-name decoding fixtures from the Python assembler."""

import json
from pathlib import Path

from free_claude_code.core.openai_tool_names import OpenAIToolNameCodec
from free_claude_code.providers.openai_chat.tool_calls import (
    _decode_streamed_tool_name,
    _merge_tool_name,
)

OUTPUT = Path("gocore/testdata/golden_openaichat4c4b_streamed_names.json")


def main() -> None:
    long_name = "mcp.server/" + "x" * 70
    second_name = "工具名称"
    names = [long_name, second_name, "normal"]
    codec = OpenAIToolNameCodec.from_names(names)
    alias = codec.encode(long_name)
    second = codec.encode(second_name)
    sequences = [
        ("fragments", names, True, [(0, alias[:5]), (0, alias[5:15]), (0, alias[15:])]),
        ("cumulative", names, True, [(1, alias[:5]), (1, alias[:15]), (1, alias)]),
        (
            "repeat_shorter",
            names,
            True,
            [(1, alias[:15]), (1, alias[:5]), (1, alias[15:])],
        ),
        (
            "interleaved",
            names,
            True,
            [(0, alias[:8]), (1, second[:4]), (1, second[4:]), (0, alias[8:])],
        ),
        ("prefix_diverges", names, True, [(0, alias[:5]), (0, "unexpected")]),
        ("unknown_and_empty", names, True, [(0, ""), (1, "normal"), (2, "unknown")]),
        ("no_buffers", names, False, [(0, alias[:5]), (0, alias)]),
        ("no_aliases", ["normal"], True, [(0, "nor"), (0, "mal")]),
        ("no_codec", None, True, [(0, "nor"), (0, "mal")]),
    ]
    cases = []
    for label, case_names, use_buffers, steps in sequences:
        active_codec = (
            OpenAIToolNameCodec.from_names(case_names)
            if case_names is not None
            else None
        )
        buffers = {} if use_buffers else None
        observed = []
        for index, fragment in steps:
            value = _decode_streamed_tool_name(
                fragment, tool_index=index, tool_names=active_codec, buffers=buffers
            )
            observed.append(
                {
                    "index": index,
                    "fragment": fragment,
                    "result": value,
                    "buffers": dict(buffers or {}),
                }
            )
        cases.append(
            {
                "label": label,
                "names": case_names,
                "use_buffers": use_buffers,
                "steps": observed,
            }
        )
    pairs = [
        ("", "x"),
        ("foo", "foobar"),
        ("foobar", "foo"),
        ("foo", "bar"),
        ("foo", ""),
        ("", ""),
    ]
    fixture = {
        "schema": "phase4c4b-streamed-names-v1",
        "merge": [
            {"existing": a, "incoming": b, "result": _merge_tool_name(a, b)}
            for a, b in pairs
        ],
        "sequences": cases,
    }
    OUTPUT.write_text(
        json.dumps(fixture, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"wrote {OUTPUT}: {len(pairs)} merge and {len(cases)} sequence cases")


if __name__ == "__main__":
    main()
