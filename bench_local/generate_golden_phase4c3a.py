"""Generate phase 4c-3a golden fixture: ProviderExecution budget state machine.

Drives the real ProviderExecution with a recording controller stub (the
only controller surface the execution touches is ``_execution_terminated``).
Every state-changing call is recorded as an op plus a full snapshot so the
Go port can replay step by step without any async machinery.
"""

import json
import re
import uuid
from pathlib import Path

from free_claude_code.providers.admission import (
    ProviderExecution,
    ProviderExecutionState,
    ProviderOperationKind,
)

OUTPUT = Path("gocore/testdata/golden_execution4c3a.json")

UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)


class RecordingController:
    """Minimal controller: record termination callbacks, nothing else."""

    def __init__(self) -> None:
        self.termination_calls: list[str] = []

    def _execution_terminated(self, execution: ProviderExecution) -> None:
        self.termination_calls.append(execution.state.value)


def make_error(name: str, message: str) -> Exception:
    return type(name, (Exception,), {})(message)


def error_view(error: Exception | None) -> dict[str, str] | None:
    if error is None:
        return None
    return {"kind": type(error).__name__, "message": str(error)}


class Harness:
    def __init__(
        self,
        max_attempts: int,
        *,
        request_id: str | None = None,
        model: str | None = None,
    ) -> None:
        self.controller = RecordingController()
        self.execution = ProviderExecution(
            self.controller,
            max_attempts=max_attempts,
            request_id=request_id,
            model=model,
        )
        self.claims: dict[int, object] = {}
        self.steps: list[dict[str, object]] = []

    def snapshot(
        self,
        label: str,
        op: str,
        *,
        error: str | None = None,
        ordinal: int | None = None,
        error_input: dict[str, str] | None = None,
        stale_ordinal: int | None = None,
    ) -> None:
        execution = self.execution
        active = execution._active_claim
        self.steps.append(
            {
                "label": label,
                "op": op,
                "error": error,
                "returned_ordinal": ordinal,
                "error_input": error_input,
                "stale_ordinal": stale_ordinal,
                "state": execution.state.value,
                "attempts_started": execution.attempts_started,
                "can_attempt": execution.can_attempt,
                "attempts_remaining": execution.attempts_remaining,
                "active_claim": None if active is None else active.ordinal,
                "last_failure": error_view(execution.last_failure),
                "terminal_failure": error_view(execution._terminal_failure()),
                "termination_calls": list(self.controller.termination_calls),
            }
        )

    def claim(self, label: str, kind: ProviderOperationKind) -> None:
        try:
            claim = self.execution._claim_attempt(kind)
        except RuntimeError as exc:
            self.snapshot(label, "claim", error=str(exc))
            self.steps[-1]["operation_kind"] = kind.value
            return
        self.claims[claim.ordinal] = claim
        self.snapshot(label, "claim", ordinal=claim.ordinal)
        self.steps[-1]["operation_kind"] = kind.value

    def close(self, label: str, ordinal: int | None = None) -> None:
        if ordinal is None:
            active = self.execution._active_claim
            ordinal = active.ordinal if active is not None else -1
        claim = self.claims.get(ordinal)
        self.execution._close_attempt(claim)
        self.snapshot(label, "close")

    def succeed(self, label: str) -> None:
        self.execution.succeed()
        self.snapshot(label, "succeed")

    def fail(self, label: str, error: Exception) -> None:
        self.execution.fail(error)
        self.snapshot(label, "fail", error_input=error_view(error))

    def abandon(self, label: str) -> None:
        self.execution.abandon()
        self.snapshot(label, "abandon")

    def record_failure(self, label: str, error: Exception) -> None:
        self.execution._record_failure(error)
        self.snapshot(label, "record_failure", error_input=error_view(error))


def scenario_happy_success() -> list[dict[str, object]]:
    h = Harness(3, request_id="req-happy", model="model-a")
    h.claim("first_claim", ProviderOperationKind.GENERATION)
    h.close("first_close")
    h.claim("second_claim", ProviderOperationKind.CONTINUATION)
    h.succeed("succeed_while_open")
    h.close("close_after_succeed")
    # Terminal transitions are idempotent once succeeded.
    h.fail("fail_after_terminal", make_error("LateError", "late"))
    h.abandon("abandon_after_terminal")
    h.succeed("succeed_again")
    return h.steps


def scenario_fail_after_close() -> list[dict[str, object]]:
    h = Harness(2, request_id="req-fail", model="model-b")
    h.claim("open_attempt", ProviderOperationKind.GENERATION)
    h.record_failure("record_transient", make_error("ConnectError", "reset"))
    h.close("close_while_active")
    h.fail("fail_execution", make_error("RateLimitError", "429"))
    h.close("close_without_claim")
    h.succeed("succeed_after_fail_noop")
    return h.steps


def scenario_exhaustion() -> list[dict[str, object]]:
    h = Harness(2, request_id="req-exhaust", model="model-c")
    h.claim("attempt_one", ProviderOperationKind.GENERATION)
    h.close("release_one")
    h.claim("attempt_two", ProviderOperationKind.GENERATION)
    h.close("release_two")
    h.claim("third_claim_rejected", ProviderOperationKind.GENERATION)
    h.fail("fail_when_exhausted", make_error("InternalServerError", "500"))
    return h.steps


def scenario_double_claim() -> list[dict[str, object]]:
    h = Harness(2, model="model-d")
    h.claim("first_claim", ProviderOperationKind.TOOL_REPAIR)
    h.claim("second_claim_rejected", ProviderOperationKind.TOOL_REPAIR)
    h.close("release")
    return h.steps


def scenario_abandon_open() -> list[dict[str, object]]:
    h = Harness(2, request_id="req-abandon", model="model-e")
    h.claim("open_attempt", ProviderOperationKind.MODEL_DISCOVERY)
    h.abandon("abandon_while_open")
    h.close("close_after_abandon")
    h.fail("fail_after_abandon_noop", make_error("LateError", "late"))
    return h.steps


def scenario_record_failure_after_terminal() -> list[dict[str, object]]:
    h = Harness(2, model="model-f")
    first = make_error("ConnectError", "dns")
    later = make_error("SSLWantReadError", "read")
    h.claim("open_attempt", ProviderOperationKind.GENERATION)
    h.fail("fail_first", first)
    h.record_failure("record_after_terminal_ignored", later)
    h.close("close")
    return h.steps


def scenario_claim_without_attempts() -> list[dict[str, object]]:
    h = Harness(1, model="model-g")
    h.succeed("succeed_before_any_claim")
    h.claim("claim_after_succeed_rejected", ProviderOperationKind.GENERATION)
    return h.steps


def scenario_ordinal_progress() -> list[dict[str, object]]:
    h = Harness(4, model="model-h")
    for ordinal in range(1, 5):
        h.claim(f"claim_{ordinal}", ProviderOperationKind.GENERATION)
        h.close(f"close_{ordinal}")
    h.claim("fifth_claim_rejected", ProviderOperationKind.GENERATION)
    return h.steps


def scenario_stale_claim_close() -> list[dict[str, object]]:
    h = Harness(3, model="model-i")
    h.claim("open_claim", ProviderOperationKind.GENERATION)
    # A stale claim object must not release the currently active claim.
    stale = type(h.execution._active_claim)(
        execution_id=h.execution.execution_id,
        ordinal=99,
        operation_kind=ProviderOperationKind.GENERATION,
    )
    h.execution._close_attempt(stale)
    h.snapshot("stale_close_ignored", "close_stale", stale_ordinal=99)
    h.close("real_close")
    return h.steps


def identity_contract() -> dict[str, object]:
    ids = {
        ProviderExecution(
            RecordingController(), max_attempts=1, request_id=None, model=None
        ).execution_id
        for _ in range(8)
    }
    return {
        "unique_ids": len(ids) == 8,
        "all_uuid4": all(UUID_RE.match(value) for value in ids),
        "sample_id": next(iter(ids)),
        "python_uuid_version": uuid.uuid4().version,
    }


def main() -> None:
    scenario_steps = {
        "happy_success": (3, scenario_happy_success()),
        "fail_after_close": (2, scenario_fail_after_close()),
        "exhaustion": (2, scenario_exhaustion()),
        "double_claim": (2, scenario_double_claim()),
        "abandon_open": (2, scenario_abandon_open()),
        "record_failure_after_terminal": (2, scenario_record_failure_after_terminal()),
        "claim_without_attempts": (1, scenario_claim_without_attempts()),
        "ordinal_progress": (4, scenario_ordinal_progress()),
        "stale_claim_close": (3, scenario_stale_claim_close()),
    }
    fixture = {
        "schema": "phase4c3a-execution-v1",
        "enums": {
            "operation_kinds": [kind.value for kind in ProviderOperationKind],
            "execution_states": [state.value for state in ProviderExecutionState],
        },
        "identity_contract": identity_contract(),
        "scenarios": {
            name: {"max_attempts": max_attempts, "steps": steps}
            for name, (max_attempts, steps) in scenario_steps.items()
        },
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(fixture, indent=2) + "\n")
    total = sum(len(entry["steps"]) for entry in fixture["scenarios"].values())
    print(f"wrote {OUTPUT} with {len(fixture['scenarios'])} scenarios, {total} steps")


if __name__ == "__main__":
    main()
