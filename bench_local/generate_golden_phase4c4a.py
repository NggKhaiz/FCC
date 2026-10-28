"""Generate phase 4c-4a golden fixture: OpenAI-chat adapter leaf utilities.

The first slice of the OpenAI-compatible adapter port covers the
transport-free pieces whose behavior is fully deterministic:

  * ``/v1`` base URL canonicalisation
  * ``extra_body`` canonical/reasoning field guards
  * streamed-usage request body helpers and 400/422 rejection classification
  * usage integer extraction from dict-shaped payloads (the Go port consumes
    decoded JSON maps, so the Python SDK attribute / ``model_extra`` paths
    have no Go counterpart by construction and are intentionally excluded)
  * output-token cap parsing (free text and structured error bodies) and
    request body clamping
  * per-attempt tool-call identity resolution

Every case is executed against the real Python reference so the Go replay
test has an executable spec rather than a hand-written expectation.
"""

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import openai
from httpx2 import Request, Response

from free_claude_code.providers.openai_chat.base_url import openai_v1_base_url
from free_claude_code.providers.openai_chat.extra_body import (
    validate_extra_body_does_not_override_canonical_fields,
    validate_extra_body_does_not_override_reasoning_fields,
)
from free_claude_code.providers.openai_chat.output_cap import (
    clamp_output_tokens,
    parse_output_token_cap,
)
from free_claude_code.providers.openai_chat.tool_call_identity import (
    AmbiguousToolCallIdentityError,
    ToolCallIdentityResolver,
)
from free_claude_code.providers.openai_chat.usage import (
    clone_without_stream_usage,
    is_stream_usage_rejection,
    nested_usage_int,
    request_stream_usage,
    usage_int,
)

OUTPUT = Path("gocore/testdata/golden_openaichat4c4a.json")


class FakeUpstreamError(Exception):
    """Duck-typed stand-in for the openai SDK exception hierarchy."""

    def __init__(self, descriptor: dict[str, Any]) -> None:
        super().__init__(descriptor["message"])
        self.status_code = descriptor.get("status_code")
        self.body = descriptor.get("body")
        response_status = descriptor.get("response_status")
        response_text = descriptor.get("response_text")
        self.response = (
            None
            if response_status is None and response_text is None
            else SimpleNamespace(
                status_code=response_status,
                text=response_text,
            )
        )


def to_python_error(descriptor: dict[str, Any]) -> Exception:
    """Reconstruct the exception the Python reference would inspect."""
    if (
        descriptor["kind"] == "BadRequestError"
        and descriptor.get("status_code") is None
    ):
        response = Response(
            400,
            request=Request("POST", "https://example.test/v1/chat/completions"),
        )
        return openai.BadRequestError(
            descriptor["message"], response=response, body=descriptor.get("body")
        )
    return FakeUpstreamError(descriptor)


def base_url_cases() -> list[dict[str, str]]:
    cases = [
        "https://api.example.com",
        "https://api.example.com/",
        "https://api.example.com/v1",
        "https://api.example.com/v1/",
        "http://localhost:8080/proxy",
        "http://localhost:8080/proxy//",
    ]
    return [{"input": value, "output": openai_v1_base_url(value)} for value in cases]


def extra_body_cases() -> list[dict[str, Any]]:
    extras = [
        ("valid_unrelated", {"customer": "acme-123"}),
        ("valid_empty", {}),
        ("canonical_model", {"model": "m"}),
        ("canonical_two_sorted", {"temperature": 0.2, "stream": True}),
        ("reasoning_effort", {"reasoning_effort": "high"}),
        ("thinking_budget", {"thinking_budget_tokens": 4096}),
    ]

    def guard(validator: Any, extra: dict[str, Any]) -> str | None:
        try:
            validator(extra)
        except ValueError as exc:
            return str(exc)
        return None

    return [
        {
            "name": name,
            "extra": extra,
            "canonical_error": guard(
                validate_extra_body_does_not_override_canonical_fields, extra
            ),
            "reasoning_error": guard(
                validate_extra_body_does_not_override_reasoning_fields, extra
            ),
        }
        for name, extra in extras
    ]


def stream_usage_cases() -> dict[str, list[dict[str, Any]]]:
    request_cases: list[dict[str, Any]] = []
    for name, body in [
        ("absent_option", {"model": "m"}),
        ("merges_sibling", {"model": "m", "stream_options": {"foo": "bar"}}),
        ("non_dict_option_untouched", {"model": "m", "stream_options": ["x"]}),
    ]:
        request_stream_usage(body)
        request_cases.append({"name": name, "body": body})

    clone_cases: list[dict[str, Any]] = []
    for name, body in [
        (
            "removes_only_include_usage",
            {
                "model": "m",
                "stream_options": {"foo": "bar", "include_usage": True},
            },
        ),
        (
            "drops_empty_option",
            {"model": "m", "stream_options": {"include_usage": True}},
        ),
        ("absent_option", {"model": "m"}),
        ("non_dict_option", {"model": "m", "stream_options": ["x"]}),
        ("empty_option", {"model": "m", "stream_options": {}}),
    ]:
        original = json.loads(json.dumps(body))
        clone_cases.append(
            {
                "name": name,
                "input": original,
                "output": clone_without_stream_usage(body),
                "input_unchanged": body == original,
            }
        )
    return {"request": request_cases, "clone": clone_cases}


def usage_extraction_cases() -> dict[str, list[dict[str, Any]]]:
    usage_int_matrix = [
        ("plain_int", {"prompt_tokens": 11}, "prompt_tokens"),
        ("zero", {"completion_tokens": 0}, "completion_tokens"),
        ("negative", {"prompt_tokens": -1}, "prompt_tokens"),
        ("bool_rejected", {"prompt_tokens": True}, "prompt_tokens"),
        ("float_rejected", {"prompt_tokens": 22.0}, "prompt_tokens"),
        ("string_rejected", {"prompt_tokens": "22"}, "prompt_tokens"),
        ("none_value", {"prompt_tokens": None}, "prompt_tokens"),
        ("missing_key", {"completion_tokens": 4}, "prompt_tokens"),
        ("empty_mapping", {}, "prompt_tokens"),
    ]
    nested_matrix = [
        (
            "plain_nested",
            {"prompt_tokens_details": {"cache_write_tokens": 5}},
            "cache_write_tokens",
        ),
        (
            "nested_zero",
            {"prompt_tokens_details": {"cached_tokens": 0}},
            "cached_tokens",
        ),
        (
            "nested_negative",
            {"prompt_tokens_details": {"cached_tokens": -1}},
            "cached_tokens",
        ),
        (
            "nested_bool",
            {"prompt_tokens_details": {"cached_tokens": True}},
            "cached_tokens",
        ),
        (
            "nested_float",
            {"prompt_tokens_details": {"cached_tokens": 15.0}},
            "cached_tokens",
        ),
        (
            "nested_string",
            {"prompt_tokens_details": {"cached_tokens": "15"}},
            "cached_tokens",
        ),
        ("parent_none", {"prompt_tokens_details": None}, "cached_tokens"),
        ("parent_non_mapping", {"prompt_tokens_details": "invalid"}, "cached_tokens"),
        ("parent_missing", {"prompt_tokens": 22}, "cached_tokens"),
        ("empty_mapping", {}, "cached_tokens"),
    ]
    return {
        "usage_int": [
            {"name": name, "usage": usage, "key": key, "output": usage_int(usage, key)}
            for name, usage, key in usage_int_matrix
        ],
        "nested_usage_int": [
            {
                "name": name,
                "usage": usage,
                "parent": "prompt_tokens_details",
                "key": key,
                "output": nested_usage_int(usage, "prompt_tokens_details", key),
            }
            for name, usage, key in nested_matrix
        ],
    }


def usage_rejection_cases() -> list[dict[str, Any]]:
    descriptors = [
        (
            "usage_option_400",
            {
                "kind": "BadRequestError",
                "message": "Unrecognized request argument supplied: stream_options",
                "status_code": 400,
                "body": {"error": {"message": "stream_options is unsupported"}},
            },
            True,
        ),
        (
            "unrelated_400",
            {
                "kind": "BadRequestError",
                "message": "messages: invalid role",
                "status_code": 400,
                "body": {"error": {"message": "messages contains invalid role"}},
            },
            False,
        ),
        (
            "usage_option_422",
            {
                "kind": "APIStatusError",
                "message": "unknown stream_options parameter",
                "status_code": 422,
                "body": None,
            },
            True,
        ),
        (
            "server_error_ignored",
            {
                "kind": "InternalServerError",
                "message": "stream_options is unsupported",
                "status_code": 500,
                "body": None,
            },
            False,
        ),
        (
            "message_only_400",
            {
                "kind": "BadRequestError",
                "message": "stream_options not permitted",
                "status_code": 400,
                "body": None,
            },
            True,
        ),
        (
            "include_usage_wording",
            {
                "kind": "BadRequestError",
                "message": "include_usage is forbidden",
                "status_code": 400,
                "body": None,
            },
            True,
        ),
        (
            "option_without_rejection_word",
            {
                "kind": "BadRequestError",
                "message": "stream_options present in request",
                "status_code": 400,
                "body": None,
            },
            False,
        ),
        (
            "response_status_fallback",
            {
                "kind": "APIStatusError",
                "message": "upstream rejected the request",
                "status_code": None,
                "body": None,
                "response_status": 400,
                "response_text": "stream_options invalid for this provider",
            },
            True,
        ),
        (
            "string_body_400",
            {
                "kind": "BadRequestError",
                "message": "bad request",
                "status_code": 400,
                "body": "stream_options is unsupported by this deployment",
            },
            True,
        ),
        (
            "kind_without_status",
            {
                "kind": "BadRequestError",
                "message": "stream_options is unsupported",
                "status_code": None,
                "body": None,
            },
            True,
        ),
    ]
    return [
        {
            "name": name,
            "error": descriptor,
            "expected": expected,
            "observed": is_stream_usage_rejection(to_python_error(descriptor)),
        }
        for name, descriptor, expected in descriptors
    ]


def output_cap_parse_cases() -> list[dict[str, Any]]:
    cases = [
        (
            "groq_long_message",
            {
                "kind": "BadRequestError",
                "message": (
                    "`max_completion_tokens` must be less than or equal to `16384`, "
                    "the maximum value for `max_completion_tokens` is less than the "
                    "`context_window` for this model"
                ),
                "status_code": 400,
                "body": None,
            },
            16384,
        ),
        (
            "colon_maximum_value",
            {
                "kind": "BadRequestError",
                "message": "max_tokens: maximum value is 8192",
                "status_code": 400,
                "body": None,
            },
            8192,
        ),
        (
            "must_not_exceed",
            {
                "kind": "BadRequestError",
                "message": "max_tokens must not exceed 16000",
                "status_code": 400,
                "body": None,
            },
            16000,
        ),
        (
            "lte_comparator",
            {
                "kind": "BadRequestError",
                "message": "`max_completion_tokens` <= 4096 required",
                "status_code": 400,
                "body": None,
            },
            4096,
        ),
        (
            "at_most",
            {
                "kind": "BadRequestError",
                "message": "max_tokens at most 2048 allowed",
                "status_code": 400,
                "body": None,
            },
            2048,
        ),
        (
            "maximum_of_for",
            {
                "kind": "BadRequestError",
                "message": "maximum allowed value of 32768 for max_tokens",
                "status_code": 400,
                "body": None,
            },
            32768,
        ),
        (
            "structured_comparator_only",
            {
                "kind": "BadRequestError",
                "message": "invalid request",
                "status_code": 400,
                "body": {
                    "error": {
                        "param": "max_completion_tokens",
                        "message": "<= 12000",
                    }
                },
            },
            12000,
        ),
        (
            "inclusive_range",
            {
                "kind": "BadRequestError",
                "message": "Range of max_tokens should be [1, 32768]",
                "status_code": 400,
                "body": None,
            },
            32768,
        ),
        (
            "inclusive_range_quoted",
            {
                "kind": "BadRequestError",
                "message": "range of max_completion_tokens should be [ 1 , `8192` ]",
                "status_code": 400,
                "body": None,
            },
            8192,
        ),
        (
            "range_wrong_field",
            {
                "kind": "BadRequestError",
                "message": "Range of temperature should be [0, 2] for max_tokens requests",
                "status_code": 400,
                "body": None,
            },
            None,
        ),
        (
            "range_starts_at_zero",
            {
                "kind": "BadRequestError",
                "message": "Range of max_tokens should be [0, 4096]",
                "status_code": 400,
                "body": None,
            },
            None,
        ),
        (
            "range_unlimited",
            {
                "kind": "BadRequestError",
                "message": "Range of max_tokens should be [1, unlimited]",
                "status_code": 400,
                "body": None,
            },
            None,
        ),
        (
            "range_missing_should_be",
            {
                "kind": "BadRequestError",
                "message": "Range of max_tokens is [1, 4096]",
                "status_code": 400,
                "body": None,
            },
            None,
        ),
        (
            "non_400_status",
            {
                "kind": "InternalServerError",
                "message": "max_tokens must be less than or equal to 40960",
                "status_code": 500,
                "body": None,
            },
            None,
        ),
        (
            "unrelated_400",
            {
                "kind": "BadRequestError",
                "message": "temperature must be <= 2",
                "status_code": 400,
                "body": None,
            },
            None,
        ),
        (
            "unrelated_comparator_text_first",
            {
                "kind": "BadRequestError",
                "message": "temperature must be <= 2; max_tokens is invalid",
                "status_code": 400,
                "body": None,
            },
            None,
        ),
        (
            "unrelated_comparator_text_second",
            {
                "kind": "BadRequestError",
                "message": "max_completion_tokens is invalid; temperature at most 2",
                "status_code": 400,
                "body": None,
            },
            None,
        ),
        (
            "unrelated_structured_value",
            {
                "kind": "BadRequestError",
                "message": "max_tokens is invalid",
                "status_code": 400,
                "body": {
                    "errors": [
                        {"param": "temperature", "message": "must not exceed 2"},
                        {"param": "top_p", "message": "<= 1"},
                    ]
                },
            },
            None,
        ),
        (
            "structured_non_output_parameter",
            {
                "kind": "BadRequestError",
                "message": "Error code: 400 - {'param': 'temperature', 'message': 'max_tokens must not exceed 2'}",
                "status_code": 400,
                "body": {
                    "param": "temperature",
                    "message": "max_tokens must not exceed 2",
                },
            },
            None,
        ),
        (
            "structured_scope_does_not_escape",
            {
                "kind": "BadRequestError",
                "message": "invalid request",
                "status_code": 400,
                "body": {
                    "param": "temperature",
                    "details": {"message": "max_tokens must not exceed 2"},
                },
            },
            None,
        ),
        (
            "text_outside_structured_schema",
            {
                "kind": "BadRequestError",
                "message": "invalid request",
                "status_code": 400,
                "body": {"request": {"message": "max_tokens must not exceed 2"}},
            },
            None,
        ),
        (
            "unscoped_structured_message",
            {
                "kind": "BadRequestError",
                "message": "invalid request",
                "status_code": 400,
                "body": {"message": "max_tokens must not exceed 8192"},
            },
            8192,
        ),
        (
            "structured_list_selects_output_parameter",
            {
                "kind": "BadRequestError",
                "message": "invalid request",
                "status_code": 400,
                "body": {
                    "errors": [
                        {
                            "param": "temperature",
                            "message": "max_tokens must not exceed 2",
                        },
                        {"param": "max_tokens", "message": "<= 8192"},
                    ]
                },
            },
            8192,
        ),
        (
            "no_number_in_message",
            {
                "kind": "BadRequestError",
                "message": "max_tokens is larger than allowed",
                "status_code": 400,
                "body": None,
            },
            None,
        ),
        (
            "string_body",
            {
                "kind": "BadRequestError",
                "message": "invalid request",
                "status_code": 400,
                "body": "max_tokens at most 2048 allowed",
            },
            2048,
        ),
        (
            "list_body",
            {
                "kind": "BadRequestError",
                "message": "invalid request",
                "status_code": 400,
                "body": ["max_tokens <= 512"],
            },
            512,
        ),
        (
            "kind_without_status",
            {
                "kind": "BadRequestError",
                "message": "max_tokens must not exceed 40960",
                "status_code": None,
                "body": None,
            },
            40960,
        ),
        (
            "glued_prefix_not_a_field",
            {
                "kind": "BadRequestError",
                "message": "xmax_tokens must not exceed 100",
                "status_code": 400,
                "body": None,
            },
            None,
        ),
        (
            "glued_suffix_not_a_field",
            {
                "kind": "BadRequestError",
                "message": "max_tokensx must not exceed 100",
                "status_code": 400,
                "body": None,
            },
            None,
        ),
        (
            "glued_quoted_prefix_not_a_field",
            {
                "kind": "BadRequestError",
                "message": "x`max_completion_tokens` at most 5",
                "status_code": 400,
                "body": None,
            },
            None,
        ),
        (
            "field_at_message_start",
            {
                "kind": "BadRequestError",
                "message": "max_tokens must not exceed 256",
                "status_code": 400,
                "body": None,
            },
            256,
        ),
        (
            "field_at_message_end",
            {
                "kind": "BadRequestError",
                "message": "maximum allowed value of 512 for max_completion_tokens",
                "status_code": 400,
                "body": None,
            },
            512,
        ),
    ]
    results = []
    for name, descriptor, expected in cases:
        observed = parse_output_token_cap(to_python_error(descriptor))
        results.append(
            {
                "name": name,
                "error": descriptor,
                "expected": expected,
                "observed": observed,
            }
        )
    return results


def output_cap_clamp_cases() -> list[dict[str, Any]]:
    cases = [
        ("clamps_completion_field", {"max_completion_tokens": 64000}, 40960),
        ("clamps_tokens_field", {"max_tokens": 100000}, 8192),
        ("within_cap_returns_none", {"max_completion_tokens": 1000}, 40960),
        ("does_not_mutate_input", {"max_tokens": 99999, "model": "m"}, 8192),
        ("bool_value_ignored", {"max_tokens": True}, 8192),
        ("float_value_ignored", {"max_tokens": 4096.5}, 8192),
        (
            "both_fields_clamped",
            {"max_tokens": 99999, "max_completion_tokens": 88888, "model": "m"},
            4096,
        ),
    ]
    results = []
    for name, body, cap in cases:
        original = json.loads(json.dumps(body))
        results.append(
            {
                "name": name,
                "input": original,
                "cap": cap,
                "output": clamp_output_tokens(body, cap),
                "input_unchanged": body == original,
            }
        )
    return results


def identity_scenarios() -> list[dict[str, Any]]:
    scenarios: list[tuple[str, list[tuple[Any, Any]]]] = [
        (
            "two_id_only_calls",
            [(None, "call_a"), (None, "call_b")],
        ),
        (
            "learn_index_then_id",
            [(0, None), (0, "call_a")],
        ),
        (
            "learn_id_then_index",
            [(None, "call_a"), (4, "call_a")],
        ),
        (
            "index_then_fragment_without_id",
            [(2, "call_x"), (2, None)],
        ),
        (
            "disjoint_partials_variant_a",
            [(None, "call_a"), (1, None), (1, "call_b")],
        ),
        (
            "disjoint_partials_variant_b",
            [(0, None), (None, "call_b"), (0, "call_a")],
        ),
        (
            "new_index_does_not_attach_to_unindexed",
            [(None, "call_a"), (None, "call_b"), (3, None)],
        ),
        (
            "sparse_numeric_order",
            [(8, "call_a"), (2, "call_b")],
        ),
        (
            "repeated_index_allocates_new_slot",
            [(0, "call_a"), (0, "call_b")],
        ),
        (
            "unidentified_without_existing",
            [(None, None)],
        ),
        (
            "unidentified_negative_index_blank_id",
            [(0, "call_a"), (-1, "")],
        ),
        (
            "unidentified_string_index_whitespace_id",
            [(0, "call_a"), ("missing", "  ")],
        ),
        (
            "unidentified_bool_index",
            [(0, "call_a"), (False, None)],
        ),
        (
            "ambiguous_shared_index",
            [(0, "call_a"), (0, "call_b"), (0, None)],
        ),
        (
            "ambiguous_shared_id",
            [(0, "same"), (1, "same"), (None, "same")],
        ),
        (
            "both_missing_after_index_only_calls",
            [(0, None), (1, None), (None, None)],
        ),
        (
            "ambiguous_unindexed_neighbour",
            [(0, "call_a"), (None, "call_b"), (0, None)],
        ),
        (
            "ambiguous_id_with_indexed_neighbour",
            [(0, "call_a"), (1, None), (None, "call_a")],
        ),
        (
            "ambiguous_two_linked_calls",
            [(0, None), (None, "call_a"), (0, "call_a")],
        ),
        (
            "allocate_then_unknown_identifier",
            [("__allocate__", None), ("__allocate__", None), (5, "z")],
        ),
        (
            "order_key_missing_index_uses_zero",
            [(None, "call_a"), (0, "call_b")],
        ),
        (
            "whitespace_id_treated_as_missing",
            [(1, "  ")],
        ),
        (
            "bool_index_falls_back_to_id",
            [(False, "call_a")],
        ),
        (
            "float_index_falls_back_to_id",
            [(1.5, "call_a")],
        ),
    ]

    rendered = []
    for name, fragments in scenarios:
        resolver = ToolCallIdentityResolver()
        steps = []
        for raw_index, raw_id in fragments:
            if raw_index == "__allocate__":
                slot = resolver.allocate()
                steps.append(
                    {
                        "op": "allocate",
                        "index": None,
                        "id": None,
                        "slot": slot,
                        "error": None,
                    }
                )
                continue
            try:
                slot = resolver.resolve(raw_index, raw_id)
            except AmbiguousToolCallIdentityError as exc:
                steps.append(
                    {
                        "op": "resolve",
                        "index": raw_index,
                        "id": raw_id,
                        "slot": None,
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
                continue
            steps.append(
                {
                    "op": "resolve",
                    "index": raw_index,
                    "id": raw_id,
                    "slot": slot,
                    "error": None,
                    "tool_id": resolver.tool_id(slot),
                    "order_key": list(resolver.order_key(slot)),
                }
            )
        final_calls = []
        for slot in range(len(resolver._calls)):
            call = resolver._calls[slot]
            final_calls.append(
                {
                    "slot": slot,
                    "index": call.index,
                    "tool_id": call.tool_id,
                    "order_key": list(resolver.order_key(slot)),
                }
            )
        rendered.append({"name": name, "steps": steps, "final_calls": final_calls})
    return rendered


def main() -> None:
    fixture = {
        "schema": "phase4c4a-openaichat-leaf-v1",
        "base_url": base_url_cases(),
        "extra_body": extra_body_cases(),
        "stream_usage": stream_usage_cases(),
        "usage_extraction": usage_extraction_cases(),
        "usage_rejection": usage_rejection_cases(),
        "output_cap_parse": output_cap_parse_cases(),
        "output_cap_clamp": output_cap_clamp_cases(),
        "identity": identity_scenarios(),
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(fixture, indent=2) + "\n")
    print(
        f"wrote {OUTPUT}: {len(fixture['base_url'])} base-url, "
        f"{len(fixture['extra_body'])} extra-body, "
        f"{len(fixture['usage_rejection'])} usage-rejection, "
        f"{len(fixture['output_cap_parse'])} cap-parse, "
        f"{len(fixture['output_cap_clamp'])} clamp, "
        f"{len(fixture['identity'])} identity scenarios"
    )


if __name__ == "__main__":
    main()
