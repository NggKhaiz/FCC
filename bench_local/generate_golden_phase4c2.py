"""Generate phase 4c-2 golden fixture: retry timing and failure labels.

Covers ProviderAdmissionController retry configuration validation,
``_retry_delay`` backoff math (with a scripted jitter source),
``_retry_after_seconds`` header parsing, ``_exception_status`` and
``_failure_label``.
"""

import json
from datetime import UTC, datetime, timedelta, tzinfo
from email.utils import format_datetime
from pathlib import Path

from free_claude_code.providers import admission
from free_claude_code.providers.admission import (
    DEFAULT_UPSTREAM_BASE_DELAY,
    DEFAULT_UPSTREAM_JITTER,
    DEFAULT_UPSTREAM_MAX_DELAY,
    UPSTREAM_TRANSIENT_TOTAL_ATTEMPTS,
    ProviderAdmissionController,
)

OUTPUT = Path("gocore/testdata/golden_retry4c2.json")

FIXED_NOW = datetime(2026, 10, 28, 8, 0, 0, tzinfo=UTC)


class FrozenDateTime(datetime):
    """datetime stand-in whose now() always returns FIXED_NOW."""

    @classmethod
    def now(cls, tz: tzinfo | None = None) -> datetime:
        return FIXED_NOW


class FakeResponse:
    def __init__(self, status_code=None, headers=None):
        self.status_code = status_code
        self.headers = headers


def make_error(name: str, *, status_code=None, response=None):
    cls = type(name, (Exception,), {})
    error = cls()
    if status_code is not None:
        error.status_code = status_code
    if response is not None:
        error.response = response
    return error


def config_cases() -> list[dict[str, object]]:
    cases: list[dict[str, object]] = []
    invalid = [
        ("max_attempts_zero", {"max_attempts": 0}, "max_attempts must be > 0"),
        ("max_attempts_negative", {"max_attempts": -4}, "max_attempts must be > 0"),
        ("negative_base_delay", {"base_delay": -0.1}, "base_delay must be >= 0"),
        (
            "max_below_base",
            {"base_delay": 5.0, "max_delay": 4.9},
            "max_delay must be >= base_delay",
        ),
        ("negative_jitter", {"jitter": -1.0}, "jitter must be >= 0"),
    ]
    for label, kwargs, expected in invalid:
        try:
            ProviderAdmissionController(provider_name="golden", **kwargs)
        except ValueError as exc:
            cases.append({"label": label, "error": str(exc), "expected": expected})
        else:  # pragma: no cover
            raise AssertionError(f"{label} should have been rejected")
    controller = ProviderAdmissionController(provider_name="golden")
    cases.append(
        {
            "label": "defaults",
            "max_attempts": UPSTREAM_TRANSIENT_TOTAL_ATTEMPTS,
            "base_delay": DEFAULT_UPSTREAM_BASE_DELAY,
            "max_delay": DEFAULT_UPSTREAM_MAX_DELAY,
            "jitter": DEFAULT_UPSTREAM_JITTER,
            "observed": {
                "max_attempts": controller._max_attempts,
                "base_delay": controller._base_delay,
                "max_delay": controller._max_delay,
                "jitter": controller._jitter,
            },
        }
    )
    return cases


def retry_after_cases() -> list[dict[str, object]]:
    admission.datetime = FrozenDateTime
    date_future = format_datetime(FIXED_NOW + timedelta(seconds=30), usegmt=True)
    date_past = format_datetime(FIXED_NOW - timedelta(seconds=90), usegmt=True)
    naive_date = (FIXED_NOW + timedelta(seconds=12)).replace(tzinfo=None)
    cases = [
        ("no_response", make_error("PlainError"), None),
        (
            "response_without_headers",
            make_error("PlainError", response=FakeResponse(status_code=503)),
            None,
        ),
        (
            "header_absent",
            make_error("APIError", response=FakeResponse(headers={"other": "x"})),
            None,
        ),
        (
            "blank_header",
            make_error(
                "APIError", response=FakeResponse(headers={"retry-after": "   "})
            ),
            None,
        ),
        (
            "integer_seconds",
            make_error(
                "RateLimitError", response=FakeResponse(headers={"retry-after": "12"})
            ),
            12.0,
        ),
        (
            "float_seconds",
            make_error(
                "RateLimitError", response=FakeResponse(headers={"retry-after": "2.5"})
            ),
            2.5,
        ),
        (
            "negative_floored",
            make_error(
                "RateLimitError", response=FakeResponse(headers={"retry-after": "-7"})
            ),
            0.0,
        ),
        (
            "infinite_rejected",
            make_error(
                "RateLimitError", response=FakeResponse(headers={"retry-after": "inf"})
            ),
            None,
        ),
        (
            "nan_rejected",
            make_error(
                "RateLimitError", response=FakeResponse(headers={"retry-after": "nan"})
            ),
            None,
        ),
        (
            "http_date_future",
            make_error(
                "RateLimitError",
                response=FakeResponse(headers={"retry-after": date_future}),
            ),
            30.0,
        ),
        (
            "http_date_past_floored",
            make_error(
                "RateLimitError",
                response=FakeResponse(headers={"retry-after": date_past}),
            ),
            0.0,
        ),
        (
            "unparseable",
            make_error(
                "RateLimitError",
                response=FakeResponse(headers={"retry-after": "soon please"}),
            ),
            None,
        ),
        # "-0000" parses to a naive datetime which the reference then treats
        # as UTC; the Go port mirrors it with an RFC1123Z tolerant fallback.
        (
            "naive_http_date",
            make_error(
                "RateLimitError",
                response=FakeResponse(
                    headers={"retry-after": format_datetime(naive_date, usegmt=False)}
                ),
            ),
            12.0,
        ),
    ]
    result = []
    for label, error, expected in cases:
        observed = admission._retry_after_seconds(error)
        response = getattr(error, "response", None)
        headers = getattr(response, "headers", None)
        header_value = headers.get("retry-after") if headers else None
        result.append(
            {
                "label": label,
                "response_present": response is not None,
                "retry_after_header": header_value,
                "observed": observed,
                "expected": expected,
            }
        )
    return result


def delay_cases() -> list[dict[str, object]]:
    # Scripted jitter draws and patched uniform so outputs are deterministic.
    draws = [0.0, 0.25, 1.0, 0.5, 0.75, 0.1]
    uniform_calls: list[tuple[float, float]] = []

    def fake_uniform(low, high):
        uniform_calls.append((low, high))
        if high == 0.0:
            return 0.0
        return draws[(len(uniform_calls) - 1) % len(draws)]

    admission.random.uniform = fake_uniform
    controller = ProviderAdmissionController(
        provider_name="golden", base_delay=2.0, max_delay=20.0, jitter=1.0
    )
    plain = make_error("ConnectError")
    rate = make_error(
        "RateLimitError", response=FakeResponse(headers={"retry-after": "15"})
    )

    cases: list[dict[str, object]] = [
        {
            "label": f"backoff_attempt_{attempt}",
            "attempt": attempt,
            "retry_after": None,
            "jitter_draw": draws[attempt - 1],
            "observed": controller._retry_delay(plain, attempt),
        }
        for attempt in range(1, 7)
    ]
    # Retry-After dominates the computed backoff.
    cases.append(
        {
            "label": "retry_after_dominates",
            "attempt": 2,
            "retry_after": 15.0,
            "jitter_draw": draws[6 % len(draws)],
            "observed": controller._retry_delay(rate, 2),
        }
    )
    main_controller_calls = len(uniform_calls)
    # Zero jitter and zero base delay collapse the wait to the header value.
    zero = ProviderAdmissionController(
        provider_name="golden", base_delay=0.0, max_delay=0.0, jitter=0.0
    )
    cases.append(
        {
            "label": "zero_base_no_header",
            "attempt": 4,
            "retry_after": None,
            "jitter_draw": 0.0,
            "observed": zero._retry_delay(plain, 4),
        }
    )
    cases.append(
        {
            "label": "uniform_bounds",
            "observed_low_high": uniform_calls[0],
            "all_bounds_match": all(
                call == (0.0, 1.0) for call in uniform_calls[:main_controller_calls]
            ),
        }
    )
    return cases


def status_and_label_cases() -> list[dict[str, object]]:
    cases: list[dict[str, object]] = []
    error_variants = [
        ("status_attr", make_error("APIStatusError", status_code=502), 502),
        (
            "response_status",
            make_error("HTTPStatusError", response=FakeResponse(status_code=503)),
            503,
        ),
        (
            "status_attr_wins",
            make_error(
                "APIStatusError",
                status_code=429,
                response=FakeResponse(status_code=500),
            ),
            429,
        ),
        ("neither", make_error("ConnectError"), None),
        (
            "none_like",
            make_error(
                "APIError", status_code=None, response=FakeResponse(status_code=None)
            ),
            None,
        ),
    ]
    for label, error, expected_status in error_variants:
        observed_status = admission._exception_status(error)
        response = getattr(error, "response", None)
        response_status = getattr(response, "status_code", None)
        raw_status = getattr(error, "status_code", None)
        cases.append(
            {
                "label": label,
                "status_code": raw_status,
                "response_status": response_status,
                "observed_status": observed_status,
                "expected_status": expected_status,
                "failure_label": admission.ProviderAdmissionController._failure_label(
                    observed_status, error
                ),
                "error_kind": type(error).__name__,
            }
        )
    # Explicit label branches for the full wording matrix.
    label_matrix = [
        (429, "RateLimitError", "Rate limited (429)"),
        (500, "InternalServerError", "Upstream server error (500)"),
        (503, "APIError", "Upstream server error (503)"),
        (None, "ConnectError", "Provider transient error (ConnectError)"),
    ]
    for status, kind, expected in label_matrix:
        observed = admission.ProviderAdmissionController._failure_label(
            status, make_error(kind)
        )
        cases.append(
            {
                "label": f"label_{kind}_{status}",
                "status_code": status,
                "response_status": None,
                "error_kind": kind,
                "observed_label": observed,
                "expected_label": expected,
            }
        )
    return cases


def main() -> None:
    fixture = {
        "schema": "phase4c2-retry-v1",
        "constants": {
            "upstream_transient_total_attempts": UPSTREAM_TRANSIENT_TOTAL_ATTEMPTS,
            "default_base_delay": DEFAULT_UPSTREAM_BASE_DELAY,
            "default_max_delay": DEFAULT_UPSTREAM_MAX_DELAY,
            "default_jitter": DEFAULT_UPSTREAM_JITTER,
        },
        "config_cases": config_cases(),
        "retry_after_cases": retry_after_cases(),
        "delay_cases": delay_cases(),
        "status_and_label_cases": status_and_label_cases(),
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(fixture, indent=2) + "\n")
    print(f"wrote {OUTPUT}")


if __name__ == "__main__":
    main()
