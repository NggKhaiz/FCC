"""Generate tool-name codec parity cases from the Python reference.

Run with the project's Python environment and PYTHONPATH=src. The fixture
covers name construction and Chat body rewriting; request model traversal is
intentionally deferred until the Go MessagesRequest foundation exists.
"""

import copy
import json
from pathlib import Path

from free_claude_code.core.openai_tool_names import (
    OpenAIToolNameCodec,
    encode_openai_chat_tool_names,
)

OUTPUT = Path("gocore/testdata/golden_openaichat4c4b_toolnames.json")


def codec_case(label: str, names: list[str]) -> dict:
    codec = OpenAIToolNameCodec.from_names(names)
    queries = {"", "unknown_tool", "tool", "_", "a"}
    for name in names:
        alias = codec.encode(name)
        queries.update((name, alias, alias + "x"))
        if len(alias) > 1:
            queries.update((alias[:1], alias[: len(alias) // 2], alias[:-1]))
    return {
        "label": label,
        "names": names,
        "has_aliases": codec.has_aliases,
        "queries": [
            {
                "value": value,
                "encoded": codec.encode(value),
                "decoded": codec.decode(value),
                "alias": codec.is_alias(value),
                "unchanged": codec.is_unchanged_name(value),
                "prefix": codec.is_alias_prefix(value),
            }
            for value in sorted(queries)
        ],
    }


def body_case(label: str, names: list[str], body: dict) -> dict:
    codec = OpenAIToolNameCodec.from_names(names)
    output = copy.deepcopy(body)
    old_choice = output.get("tool_choice")
    old_function = old_choice.get("function") if isinstance(old_choice, dict) else None
    encode_openai_chat_tool_names(output, codec)
    new_choice = output.get("tool_choice")
    return {
        "label": label,
        "names": names,
        "input": body,
        "output": output,
        "choice_replaced": old_choice is not new_choice,
        "function_replaced": (
            isinstance(new_choice, dict)
            and old_function is not new_choice.get("function")
        ),
    }


def main() -> None:
    long_name = "server_" + "x" * 70
    collision = OpenAIToolNameCodec.from_names([long_name]).encode(long_name)
    first_retry = OpenAIToolNameCodec.from_names([long_name, collision]).encode(
        long_name
    )
    names = [long_name, "mcp.server/tool name", "工具名称", "normal", ""]
    cases = [
        codec_case("empty", []),
        codec_case("portable_boundaries", ["", "x", "A" * 64, "read_file-2"]),
        codec_case("invalid_boundaries", ["A" * 65, "-" * 65, "_" * 65]),
        codec_case("unicode_punctuation", ["工具名称", "é-工具", "...", "  a  b !"]),
        codec_case("same_prefix", ["p" * 80 + "a", "p" * 80 + "b"]),
        codec_case("duplicates", names + names),
        codec_case("permuted", list(reversed(names))),
        codec_case("reserved_collisions", [long_name, collision, first_retry]),
        codec_case("trimmed_readable", ["_--x" + "-" * 43 + "a!", "_--x---!"]),
    ]
    cases.extend(
        codec_case(f"character_{i}", [f"prefix{char}suffix", char])
        for i, char in enumerate(
            ["/", " ", ".", "\n", "\x00", "😀", "漢", "é", "\\", "$"]
        )
    )
    bodies = [
        body_case(
            "all_locations",
            names,
            {
                "tools": [
                    {"type": "function", "function": {"name": long_name, "other": 1}},
                    {"function": {"name": "normal"}},
                    {"function": {"name": 3}},
                    None,
                ],
                "messages": [
                    {
                        "role": "assistant",
                        "tool_calls": [{"function": {"name": names[1]}}, None],
                    },
                    {"tool_calls": "invalid"},
                    False,
                ],
                "tool_choice": {
                    "type": "function",
                    "function": {"name": names[2], "extra": True},
                },
                "untouched": {"name": long_name},
            },
        ),
        body_case(
            "unchanged_choice", names, {"tool_choice": {"function": {"name": "normal"}}}
        ),
        body_case(
            "unknown_choice", names, {"tool_choice": {"function": {"name": "other"}}}
        ),
        body_case(
            "invalid_fields",
            names,
            {
                "tools": {},
                "messages": [None, {"tool_calls": [1, {"function": None}]}],
                "tool_choice": {"function": []},
            },
        ),
        body_case(
            "no_aliases",
            ["normal"],
            {
                "tools": [{"function": {"name": long_name}}],
                "tool_choice": {"function": {"name": long_name}},
            },
        ),
        body_case("missing_choice", names, {"messages": []}),
    ]
    OUTPUT.write_text(
        json.dumps(
            {"schema": "phase4c4b-toolnames-v1", "codecs": cases, "bodies": bodies},
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"wrote {OUTPUT}: {len(cases)} codec and {len(bodies)} body cases")


if __name__ == "__main__":
    main()
