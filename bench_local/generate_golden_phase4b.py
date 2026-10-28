"""Golden fixture for the phase-4b Go diagnostics port.

Locks credential redaction, body normalization/capping and the full
format_execution_failure_message envelope against the real Python
implementation, including cause chains and the request-id trailer.
"""

import json
from pathlib import Path

import httpx
import openai

from free_claude_code.core import diagnostics
from free_claude_code.providers.failure_policy import classify_provider_failure

OUT = Path(__file__).resolve().parents[1] / "gocore" / "testdata"


def redaction_cases():
    inputs = [
        "authorization: Bearer AUTH_SECRET_VALUE",
        "'authorization': 'Basic YWJjOjEyMw=='",
        '"authorization"="tok_live_1234567890"',
        "api_key: sk-redact-me-please-1234567890",
        "access-token=nvapi-tokenvalue1234567890",
        '"client_secret": "secret_value_here"',
        "password=hunter2hunter2",
        "Authorization: bearer spaced credential",
        "header Bearer abc.def.ghi trailing",
        "bare sk-abcdefghijklmnopqrstuvwxyz0123456789 token",
        "nvapi-abcdefghijklmnopqrstuvwxyz1234567890",
        "hf_abcdefghijklmnopqrstuvwxyz1234567890",
        "gsk_abcdefghijklmnopqrstuvwxyz1234567890",
        "github_pat_abcdefghijklmnopqrstuvwxyz1234",
        "ghp_abcdefghijklmnopqrstuvwxyz1234567890",
        "AIzaabcdefghijklmnopqrstuvwxyz1234567890AB",
        "clean diagnostic text without secrets",
        "mixed: api_key=leaked-key-12345678 and Bearer zzz.token here",
    ]
    return [
        {"input": text, "expected": diagnostics.redact_sensitive_error_text(text)}
        for text in inputs
    ]


def cap_case():
    text = "x" * 20_000
    capped, truncated = diagnostics._cap_text_bytes(text)
    return {
        "input_length": len(text),
        "expected": capped,
        "truncated": truncated,
        "cap_bytes": diagnostics.ERROR_DETAIL_DISPLAY_CAP_BYTES,
    }


def http_error(status_code, message, body):
    request = httpx.Request("POST", "https://provider.test/v1/messages")
    response = httpx.Response(status_code, request=request, json=body)
    return httpx.HTTPStatusError(message, request=request, response=response)


def openai_error(error_type, *, status_code, message, body):
    request = httpx.Request("POST", "https://provider.test/v1/chat/completions")
    response = httpx.Response(status_code, request=request)
    return error_type(message, response=response, body=body)


class CausedError(RuntimeError):
    def __init__(self, message, cause):
        super().__init__(message)
        self.__cause__ = cause


class PlainError(RuntimeError):
    pass


def describe(exc, *, causes=None, attached_body=None, attached_truncated=False):
    """Descriptor rich enough for the Go diagnostics extractor."""
    descriptor = {
        "kind": type(exc).__name__,
        "module": type(exc).__module__,
        "message": str(exc),
    }
    status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        descriptor["status_code"] = status
    body = getattr(exc, "body", None)
    if body is not None:
        descriptor["body"] = body
    response = getattr(exc, "response", None)
    if response is not None:
        descriptor["response_status"] = response.status_code
        descriptor["response_text"] = response.text
    if causes is not None:
        descriptor["causes"] = [
            {"kind": type(cause).__name__, "message": str(cause)} for cause in causes
        ]
    if attached_body is not None:
        descriptor["attached_body"] = attached_body
        descriptor["attached_truncated"] = attached_truncated
    return descriptor


def format_case(
    name, exc, upstream_name, request_id=None, read_timeout_s=None, **describe_kwargs
):
    failure = classify_provider_failure(
        exc,
        provider_name=upstream_name,
        read_timeout_s=read_timeout_s,
        request_id=request_id,
    )
    return {
        "name": name,
        "descriptor": describe(exc, **describe_kwargs),
        "upstream_name": upstream_name,
        "request_id": request_id,
        "read_timeout_s": read_timeout_s,
        "failure": {
            "kind": failure.kind.value,
            "status_code": failure.status_code,
            "retryable": failure.retryable,
            "message": failure.message,
        },
        "expected_message": failure.message,
    }


def format_cases():
    cases = []

    # 1. HTTP 400 whose body and prose carry credentials.
    secret_body = {
        "error": {
            "type": "invalid_request_error",
            "message": "unsupported model format authorization: Bearer AUTH_SECRET",
            "api_key": "SECRET",
        }
    }
    cases.append(
        format_case(
            "http_400_redacted_body",
            http_error(
                400,
                "unsupported model format authorization: Bearer AUTH_SECRET",
                secret_body,
            ),
            "LOCAL",
            request_id="req_body",
        )
    )

    # 2. OpenAI auth failure preserves the model diagnostic.
    model_body = {
        "type": "error",
        "error": {
            "type": "ModelError",
            "message": "Model qwen3.7-max is not supported for format oa-compat",
        },
    }
    cases.append(
        format_case(
            "openai_401_model_error",
            openai_error(
                openai.AuthenticationError,
                status_code=401,
                message="Unauthorized",
                body=model_body,
            ),
            "OPENCODE_GO",
            request_id="req_model",
        )
    )

    # 3. Permission failure with a structured status/title body.
    permission_body = {
        "status": 403,
        "title": "Forbidden",
        "detail": "model access requires an entitlement",
    }
    cases.append(
        format_case(
            "openai_403_permission",
            openai_error(
                openai.PermissionDeniedError,
                status_code=403,
                message="Forbidden",
                body=permission_body,
            ),
            "TEST_PROVIDER",
            request_id="req_perm",
        )
    )

    # 4. HTTP 405 uses the dedicated method/endpoint wording.
    cases.append(
        format_case(
            "http_405_endpoint",
            http_error(405, "not allowed", {"error": {"message": "method nope"}}),
            "ROUTER",
        )
    )

    # 5. Statusless API error: the "returned an error" branch.
    request = httpx.Request("POST", "https://provider.test/v1/chat/completions")
    statusless = openai.APIError(
        "edge failure",
        request=request,
        body={"error": {"type": "internal_server_error", "message": "edge failure"}},
    )
    cases.append(format_case("statusless_api_error", statusless, "EDGE"))

    # 6. Generic exception with no upstream detail: exception text branch.
    cases.append(
        format_case(
            "generic_exception_no_detail",
            PlainError("something unexpected happened locally"),
            "LOCAL",
        )
    )

    # 7. Cause chain is rendered redacted and capped.
    root = PlainError("socket closed")
    middle = CausedError("read failed", root)
    top = CausedError("authorization: Bearer CHAIN_SECRET", middle)
    cases.append(
        format_case(
            "cause_chain_redacted",
            top,
            "LOCAL",
            causes=[middle, root],
        )
    )

    # 8. Empty upstream body is reported explicitly.
    cases.append(
        format_case(
            "empty_upstream_body",
            http_error(502, "bad gateway", {"error": {"message": ""}}),
            "GATEWAY",
        )
    )

    # 9. Attached streamed body that is already truncated by the transport.
    exc = http_error(503, "overloaded", {"error": {"message": "capacity"}})
    diagnostics.attach_upstream_error_body(
        exc, "streamed chunk with api_key=ATTACHED_SECRET data", truncated=True
    )
    cases.append(
        format_case(
            "attached_truncated_body",
            exc,
            "STREAM",
            attached_body="streamed chunk with api_key=ATTACHED_SECRET data",
            attached_truncated=True,
        )
    )

    # 10. Long body crosses the display cap and gains a truncation marker.
    big_body = {"error": {"message": "x" * 20_000}}
    cases.append(
        format_case(
            "long_body_truncation_marker",
            http_error(500, "large", big_body),
            "BIG",
        )
    )

    # 11. Model/unsupported prose yields the upstream_model_error hint.
    cases.append(
        format_case(
            "model_unsupported_hint",
            http_error(
                400,
                "bad",
                {"error": {"message": "the model is unsupported on this endpoint"}},
            ),
            "HINT",
        )
    )

    return cases


def main():
    payload = {
        "display_cap_bytes": diagnostics.ERROR_DETAIL_DISPLAY_CAP_BYTES,
        "max_cause_chain_depth": diagnostics._MAX_CAUSE_CHAIN_DEPTH,
        "redaction_cases": redaction_cases(),
        "cap_case": cap_case(),
        "format_cases": format_cases(),
    }
    path = OUT / "golden_diagnostics4b.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        f"wrote {path}: {len(payload['redaction_cases'])} redaction, "
        f"{len(payload['format_cases'])} format cases"
    )


if __name__ == "__main__":
    main()
