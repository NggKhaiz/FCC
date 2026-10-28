"""Golden fixture for the phase-4a Go port of providers/failure_policy.py.

The generator builds real httpx/openai exception objects exactly like the
Python test suite, runs every retry/classification predicate, and records a
descriptor the Go side can reconstruct into its own typed error model. Full
diagnostics message formatting (redaction, truncation, request ids) is a
separate phase; this fixture locks the decision core and stable wording.
"""

from __future__ import annotations

import json
import ssl
from dataclasses import dataclass
from pathlib import Path

import httpx
import httpx2
import openai

from free_claude_code.core.failures import ExecutionFailure, FailureKind
from free_claude_code.providers import failure_policy as fp
from free_claude_code.providers.failure_policy import (
    ProviderRecoveryExhausted,
    RetryableProviderProtocolError,
    RetryableToolProtocolError,
)

OUT = Path(__file__).resolve().parents[1] / "gocore" / "testdata"


def openai_status_error(error_type, *, status_code, message, body=None, headers=None):
    request = httpx2.Request("POST", "https://provider.test/v1/chat/completions")
    response = httpx2.Response(status_code, request=request, headers=headers)
    return error_type(
        message,
        response=response,
        body=body or {"error": {"message": message}},
    )


def http_status_error(status_code, message, *, body=None):
    request = httpx.Request("POST", "https://provider.test/v1/messages")
    response = httpx.Response(
        status_code,
        request=request,
        json=body or {"error": {"message": message, "api_key": "SECRET"}},
    )
    return httpx.HTTPStatusError(message, request=request, response=response)


class CodedError(RuntimeError):
    def __init__(self, code, message="provider request failed", body=None):
        super().__init__(message)
        self.code = code
        if body is not None:
            self.body = body


class BodyError(RuntimeError):
    """Generic exception carrying duck-typed body/status_code attributes."""

    def __init__(self, message, *, body=None, status_code=None, code=None):
        super().__init__(message)
        if body is not None:
            self.body = body
        if status_code is not None:
            self.status_code = status_code
        if code is not None:
            self.code = code


@dataclass
class Case:
    name: str
    descriptor: dict
    read_timeout_s: float | None


def describe(exc, **extra):
    """Serialize an exception into a reconstructable descriptor."""
    kind = type(exc).__name__
    module = type(exc).__module__
    descriptor = {
        "kind": kind,
        "module": module,
        "message": str(exc),
        **extra,
    }
    status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        descriptor["status_code"] = status
    code = getattr(exc, "code", None)
    if code is not None:
        descriptor["code"] = code
    body = getattr(exc, "body", None)
    if body is not None:
        descriptor["body"] = body
    response = getattr(exc, "response", None)
    if response is not None:
        descriptor["response_status"] = getattr(response, "status_code", None)
        try:
            descriptor["response_text"] = response.text
        except Exception:
            descriptor["response_text"] = ""
    return descriptor


def classify_tuple(exc, read_timeout_s):
    failure = fp._classify_provider_failure(
        exc, read_timeout_s=read_timeout_s
    )
    return {
        "kind": failure.kind.value,
        "status_code": failure.status_code,
        "retryable": failure.retryable,
    }


def evaluate(case: Case):
    exc = case.descriptor.pop("_exc")
    read_timeout_s = case.read_timeout_s
    return {
        "name": case.name,
        "descriptor": case.descriptor,
        "read_timeout_s": read_timeout_s,
        "retryable_provider": fp.is_retryable_provider_error(exc),
        "retryable_stream": fp.is_retryable_stream_error(exc),
        "transient_status": fp.retryable_transient_status(exc),
        "retryable_upstream_status": fp.retryable_upstream_status(exc),
        "transient_overload": fp.is_transient_overload_error(exc),
        "authentication_status": fp.provider_authentication_status(exc),
        "provider_error_message": fp.provider_error_message(
            exc, read_timeout_s=read_timeout_s
        ),
        "classification": classify_tuple(exc, read_timeout_s),
    }


def build_cases():
    cases: list[Case] = []

    def add(name, exc, *, read_timeout_s=None, **extra):
        descriptor = describe(exc, **extra)
        descriptor["_exc"] = exc
        cases.append(Case(name=name, descriptor=descriptor, read_timeout_s=read_timeout_s))

    # 1. httpx HTTP status errors across the meaningful status surface.
    for status in (400, 401, 402, 403, 404, 405, 408, 409, 413, 422, 429, 500, 502, 503, 504, 529):
        add(f"http_status_{status}", http_status_error(status, f"status {status} body"))

    # 2. OpenAI typed status errors.
    add(
        "openai_rate_limit",
        openai_status_error(openai.RateLimitError, status_code=429, message="Rate limit reached"),
    )
    add(
        "openai_authentication",
        openai_status_error(openai.AuthenticationError, status_code=401, message="Bad key"),
    )
    add(
        "openai_permission_denied",
        openai_status_error(openai.PermissionDeniedError, status_code=403, message="Forbidden"),
    )
    add(
        "openai_bad_request",
        openai_status_error(openai.BadRequestError, status_code=400, message="Bad input"),
    )
    add(
        "openai_billing_402",
        openai_status_error(openai.APIStatusError, status_code=402, message="Payment required"),
    )
    add(
        "openai_internal_500",
        openai_status_error(openai.InternalServerError, status_code=500, message="boom"),
    )
    add(
        "openai_internal_overloaded_503",
        openai_status_error(
            openai.InternalServerError,
            status_code=503,
            message="model overloaded",
            body={"error": {"message": "capacity exhausted"}},
        ),
    )

    # 3. OpenAI transport errors (statusless).
    request = httpx2.Request("POST", "https://provider.test/v1/chat/completions")
    add("openai_timeout", openai.APITimeoutError(request=request))
    add("openai_connection", openai.APIConnectionError(request=request))
    add(
        "openai_statusless_api_error",
        openai.APIError("generic failure", request=request, body=None),
    )

    # 4. httpx timeout / network family, both httpx and the vendored httpx2.
    for sdk, module in ((httpx, "httpx"), (httpx2, "httpx2")):
        add(f"{module}_read_timeout", sdk.ReadTimeout("read timed out"), read_timeout_s=45.0)
        add(f"{module}_read_timeout_no_hint", sdk.ReadTimeout("read timed out"))
        add(f"{module}_connect_timeout", sdk.ConnectTimeout("connect timed out"))
        add(f"{module}_connect_error", sdk.ConnectError("connection refused"))
        add(f"{module}_remote_protocol", sdk.RemoteProtocolError("server disconnected"))
        add(f"{module}_network_error", sdk.NetworkError("network down"))

    add("builtin_timeout", TimeoutError("generic timeout"), read_timeout_s=12.5)
    add("ssl_want_read", ssl.SSLWantReadError())

    # 5. Context-window signals in every accepted shape.
    add("coded_context_length", CodedError("context_length_exceeded"))
    add(
        "body_context_length",
        BodyError(
            "bad request",
            status_code=400,
            body={"error": {"code": "context_length_exceeded", "message": "too long"}},
        ),
    )
    add(
        "body_context_type_field",
        BodyError("bad request", body={"error": {"type": "context_length_exceeded"}}),
    )
    add(
        "ambiguous_large_not_context",
        BodyError(
            "request too large",
            status_code=413,
            body={"error": {"code": "request_too_large", "message": "payload big"}},
        ),
    )

    # 6. Auth discriminators on otherwise generic exceptions.
    add("coded_invalid_api_key", CodedError("invalid_api_key"))
    add("coded_unauthorized", CodedError("unauthorized"))
    add("coded_permission_denied", CodedError("permission_denied"))
    add("coded_forbidden", CodedError("forbidden"))
    add(
        "anthropic_auth_type",
        BodyError("auth", body={"error": {"type": "authentication_error"}}),
    )
    add(
        "anthropic_permission_type",
        BodyError("perm", body={"error": {"type": "permission_error"}}),
    )

    # 7. Body- and prose-driven status inference.
    add(
        "body_status_field",
        BodyError("upstream", body={"status": 503, "error": {"message": "down"}}),
    )
    add(
        "body_nested_status_code",
        BodyError("upstream", body={"error": {"status_code": "500"}}),
    )
    add(
        "type_marker_rate_limit",
        BodyError("upstream", body={"error": {"type": "rate_limit_error"}}),
    )
    add(
        "type_marker_overloaded",
        BodyError("upstream", body={"error": {"type": "overloaded_error"}}),
    )
    add(
        "type_marker_internal",
        BodyError("upstream", body={"error": {"type": "internal_server_error"}}),
    )
    add("prose_rate_limit", BodyError("Provider returned: too many requests, slow down"))
    add("prose_resource_exhausted", BodyError("grpc ResourceExhausted backing off"))
    add("prose_internal_server", BodyError("internal server error in edge"))
    add(
        "http_413_beats_rate_marker",
        http_status_error(
            413,
            "too many requests but rejected",
            body={"error": {"message": "rate limit"}},
        ),
    )
    add(
        "nonstandard_capacity_529",
        http_status_error(529, "capacity", body={"error": {"message": "overloaded"}}),
    )

    # 8. Protocol-level retryables and recovery wrapper semantics.
    protocol = RetryableProviderProtocolError("malformed sse frame")
    add("retryable_protocol", protocol)
    add("retryable_tool_protocol", RetryableToolProtocolError("tool frame malformed"))
    add(
        "recovery_exhausted",
        ProviderRecoveryExhausted(protocol),
        last_error=describe(protocol),
    )
    inner_rate = openai_status_error(
        openai.RateLimitError, status_code=429, message="limit"
    )
    add(
        "recovery_exhausted_keeps_last",
        ProviderRecoveryExhausted(inner_rate),
        last_error=describe(inner_rate),
    )

    # 9. Already-canonical ExecutionFailure passes through the classifier.
    add(
        "canonical_execution_failure",
        ExecutionFailure(
            kind=FailureKind.UPSTREAM,
            status_code=502,
            message="The continuation replay exceeded the retained-data limit.",
            retryable=False,
        ),
        failure_kind=FailureKind.UPSTREAM.value,
        retryable=False,
    )
    add(
        "canonical_retryable_failure",
        ExecutionFailure(
            kind=FailureKind.OVERLOADED,
            status_code=529,
            message="Provider is currently overloaded. Please retry.",
            retryable=True,
        ),
        failure_kind=FailureKind.OVERLOADED.value,
        retryable=True,
    )

    return cases


def context_window_events():
    """reports_context_window_incomplete over response.incomplete payloads."""
    base = {
        "response": {
            "incomplete_details": {
                "reason": "model_context_window_exceeded",
            }
        }
    }
    cases = []
    cases.append(
        {
            "name": "incomplete_context_window",
            "event_type": "response.incomplete",
            "payload": base,
            "expected": True,
        }
    )
    other = json.loads(json.dumps(base))
    other["response"]["incomplete_details"]["reason"] = "max_output_tokens"
    cases.append(
        {
            "name": "incomplete_other_reason",
            "event_type": "response.incomplete",
            "payload": other,
            "expected": False,
        }
    )
    completed = json.loads(json.dumps(base))
    cases.append(
        {
            "name": "wrong_event_type",
            "event_type": "response.completed",
            "payload": completed,
            "expected": False,
        }
    )
    results = []
    for case in cases:
        got = fp.reports_context_window_incomplete(case["event_type"], case["payload"])
        results.append({**case, "got": got})
    return results


def main():
    cases = build_cases()
    records = [evaluate(case) for case in cases]
    payload = {
        "canonical_messages": {
            "authentication": fp._AUTHENTICATION_MESSAGE,
            "permission": fp._PERMISSION_MESSAGE,
            "billing": fp._BILLING_MESSAGE,
            "rate_limit": fp._RATE_LIMIT_MESSAGE,
            "invalid_request": fp._INVALID_REQUEST_MESSAGE,
            "request_too_large": fp._REQUEST_TOO_LARGE_MESSAGE,
            "context_window": fp._CONTEXT_WINDOW_EXCEEDED_MESSAGE,
            "overloaded": fp._OVERLOADED_MESSAGE,
            "unavailable_502_503_504": "Provider is temporarily unavailable. Please retry.",
            "generic_upstream": "Provider API request failed.",
        },
        "failure_kinds": sorted(k.value for k in FailureKind),
        "cases": records,
        "context_window_events": context_window_events(),
    }
    path = OUT / "golden_failure4a.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {path}: {len(records)} failure cases")


if __name__ == "__main__":
    main()
