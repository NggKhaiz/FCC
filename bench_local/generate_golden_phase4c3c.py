"""Generate phase 4c-3c golden fixture: attempt lifecycle and run_call loop.

Drives the real ProviderAdmissionController with zero backoff, a
deterministic virtual monotonic clock and a generous capacity gate, so
every gate visit resolves immediately and the blocking wait machinery
never parks. The Go replay drives the same clock values step by step.
Failures use the same httpx/openai exception builders as phase 4a,
serialized as reconstructable descriptors.
"""

import asyncio
import json
import sys
from pathlib import Path

import openai

sys.path.insert(0, str(Path(__file__).resolve().parent))

from generate_golden_phase4a import (
    describe,
    http_status_error,
    openai_status_error,
)

from free_claude_code.providers import admission
from free_claude_code.providers.admission import (
    ProviderAdmissionController,
    ProviderCorrectionAction,
    ProviderOperationKind,
    ProviderRecoveryExhausted,
)

OUTPUT = Path("gocore/testdata/golden_attempt4c3c.json")

ORIGIN = 1_000.0
TICK = 0.25


class VirtualClock:
    """Deterministic monotonic clock; the Go replay drives the same values."""

    def __init__(self) -> None:
        self._now = ORIGIN

    def now(self) -> float:
        return self._now

    def advance(self, seconds: float = TICK) -> None:
        self._now += seconds


class Recorder:
    def __init__(
        self,
        controller: ProviderAdmissionController,
        clock: VirtualClock,
        prefix: str = "",
    ) -> None:
        self.controller = controller
        self.clock = clock
        self.prefix = prefix
        self.steps: list[dict[str, object]] = []
        self._exec_labels: dict[int, str] = {}
        self._executions: dict[int, object] = {}
        self._seq = 0

    def remember(self, execution) -> None:
        key = id(execution)
        if key not in self._exec_labels:
            self._seq += 1
            self._exec_labels[key] = f"{self.prefix}exec_{self._seq}"
            self._executions[key] = execution

    def label(self, execution) -> str:
        self.remember(execution)
        return self._exec_labels[id(execution)]

    def manifest(self) -> Recorder:
        return [
            {
                "label": self._exec_labels[key],
                "model": (execution.model if execution.model is not None else ""),
                "request_id": execution.request_id,
                "max_attempts": execution.max_attempts,
            }
            for key, execution in self._executions.items()
        ]

    def episode_view(self) -> dict[str, object]:
        result: dict[str, object] = {}
        for model, episode in self.controller._episodes.items():
            result[model if model is not None else ""] = {
                "generation": episode.generation,
                "leader": (
                    self.label(episode.leader) if episode.leader is not None else None
                ),
                "ready_at": round(episode.ready_at - ORIGIN, 6),
                "probe_active": episode.probe_active,
                "terminal_until": (
                    round(episode.terminal_until - ORIGIN, 6)
                    if episode.terminal_until is not None
                    else None
                ),
                "waiters": sorted(self.label(w) for w in episode.waiters),
                "last_error": describe(episode.last_error)
                if episode.last_error is not None
                else None,
            }
        return result

    def snapshot(
        self,
        label: str,
        op: str,
        execution=None,
        *,
        raised: str | None = None,
        raised_type: str | None = None,
        decision: dict[str, object] | None = None,
        correction: str | None = None,
        result: object | None = None,
    ) -> None:
        capacity = self.controller._capacity
        entry: dict[str, object] = {
            "label": label,
            "op": op,
            "now": round(self.clock.now() - ORIGIN, 6),
            "execution": self.label(execution) if execution is not None else None,
            "raised": raised,
            "raised_type": raised_type,
            "decision": decision,
            "correction": correction,
            "result": result,
            "episodes": self.episode_view(),
            "next_generation": self.controller._next_generation,
            "closed": self.controller._closed,
            "capacity": {
                "active_attempts": capacity._active_attempts,
                "admission_count": len(capacity._admission_times),
            },
        }
        if execution is not None:
            entry["execution_state"] = {
                "label": self.label(execution),
                "state": execution.state.value,
                "attempts_started": execution.attempts_started,
                "can_attempt": execution.can_attempt,
                "attempts_remaining": execution.attempts_remaining,
                "active_claim": (
                    execution._active_claim.ordinal
                    if execution._active_claim is not None
                    else None
                ),
                "last_failure": describe(execution.last_failure)
                if execution.last_failure is not None
                else None,
            }
        self.steps.append(entry)


def start(recorder, controller, **kwargs):
    execution = controller.start_execution(**kwargs)
    recorder.remember(execution)
    return execution


def build_controller(
    clock: VirtualClock, *, max_attempts: int = 3
) -> ProviderAdmissionController:
    admission.random.uniform = lambda low, high: 0.0
    admission.time.monotonic = clock.now
    return ProviderAdmissionController(
        provider_name="golden",
        rate_limit=40,
        rate_window=60.0,
        max_concurrency=5,
        max_attempts=max_attempts,
        base_delay=0.0,
        max_delay=0.0,
        jitter=0.0,
    )


def retryable_503():
    return openai_status_error(
        openai.InternalServerError,
        status_code=503,
        message="upstream unavailable",
    )


def retryable_429():
    return openai_status_error(
        openai.RateLimitError,
        status_code=429,
        message="rate limited",
    )


def fatal_400():
    return openai_status_error(
        openai.BadRequestError,
        status_code=400,
        message="bad request",
    )


def fatal_401():
    return openai_status_error(
        openai.AuthenticationError,
        status_code=401,
        message="unauthorized",
    )


def http_500():
    return http_status_error(500, "internal failure")


async def scenario_attempt_decisions() -> Recorder:
    clock = VirtualClock()
    controller = build_controller(clock)
    recorder = Recorder(controller, clock)
    execution = start(recorder, controller, request_id="req-1", model="model-a")
    recorder.label(execution)

    # Retryable failure opens an episode and keeps the budget alive.
    clock.advance()
    attempt = await controller._open_attempt(
        execution, ProviderOperationKind.GENERATION
    )
    recorder.snapshot(
        "first_attempt_opened",
        "open_attempt",
        execution,
        decision={
            "permit": {
                "generation": attempt._permit.generation,
                "probe": attempt._permit.probe,
            }
        },
    )
    error = retryable_503()
    clock.advance()
    decision = await attempt.fail(error)
    recorder.snapshot(
        "retryable_failure",
        "attempt_fail",
        execution,
        decision={
            "retryable": decision.retryable,
            "retry_allowed": decision.retry_allowed,
            "error_descriptor": describe(error),
        },
    )
    clock.advance()
    await attempt.aclose()
    recorder.snapshot("close_after_retryable", "attempt_close", execution)

    # Gate returns a probe permit on the next visit; accept closes episode.
    clock.advance()
    probe = await controller._open_attempt(execution, ProviderOperationKind.GENERATION)
    recorder.snapshot(
        "probe_attempt_opened",
        "open_attempt",
        execution,
        decision={
            "permit": {
                "generation": probe._permit.generation,
                "probe": probe._permit.probe,
            }
        },
    )
    clock.advance()
    await probe.accept()
    recorder.snapshot("probe_accepted", "attempt_accept", execution)
    clock.advance()
    await probe.aclose()
    recorder.snapshot("close_after_accept", "attempt_close", execution)

    # A second double-close is a no-op (capacity released exactly once).
    clock.advance()
    await probe.aclose()
    recorder.snapshot("double_close_noop", "attempt_close", execution)
    return recorder


async def scenario_non_retryable() -> Recorder:
    clock = VirtualClock()
    controller = build_controller(clock)
    recorder = Recorder(controller, clock)
    execution = start(recorder, controller, request_id="req-2", model="model-b")
    clock.advance()
    attempt = await controller._open_attempt(
        execution, ProviderOperationKind.GENERATION
    )
    recorder.snapshot(
        "fatal_attempt_opened",
        "open_attempt",
        execution,
        decision={
            "permit": {
                "generation": attempt._permit.generation,
                "probe": attempt._permit.probe,
            }
        },
    )
    error = fatal_400()
    clock.advance()
    decision = await attempt.fail(error)
    recorder.snapshot(
        "non_retryable_failure",
        "attempt_fail",
        execution,
        decision={
            "retryable": decision.retryable,
            "retry_allowed": decision.retry_allowed,
            "error_descriptor": describe(error),
        },
    )
    clock.advance()
    await attempt.aclose()
    recorder.snapshot("close_after_fatal", "attempt_close", execution)

    raised = None
    raised_type = None
    clock.advance()
    try:
        await controller._open_attempt(execution, ProviderOperationKind.GENERATION)
    except ProviderRecoveryExhausted as exc:
        raised = str(exc)
        raised_type = type(exc).__name__
    recorder.snapshot(
        "terminal_execution_gate",
        "open_attempt",
        execution,
        raised=raised,
        raised_type=raised_type,
    )
    return recorder


async def scenario_correction() -> Recorder:
    clock = VirtualClock()
    controller = build_controller(clock)
    recorder = Recorder(controller, clock)
    execution = start(recorder, controller, request_id="req-3", model="model-c")

    clock.advance()
    attempt = await controller._open_attempt(
        execution, ProviderOperationKind.TOOL_REPAIR
    )
    recorder.snapshot(
        "repair_attempt_opened",
        "open_attempt",
        execution,
        decision={
            "permit": {
                "generation": attempt._permit.generation,
                "probe": attempt._permit.probe,
            }
        },
    )
    first_error = retryable_503()
    clock.advance()
    decision = await attempt.fail(first_error)
    recorder.snapshot(
        "repair_failure",
        "attempt_fail",
        execution,
        decision={
            "retryable": decision.retryable,
            "retry_allowed": decision.retry_allowed,
            "error_descriptor": describe(first_error),
        },
    )
    clock.advance()
    await attempt.aclose()
    recorder.snapshot("close_after_repair_failure", "attempt_close", execution)

    clock.advance()
    probe = await controller._open_attempt(execution, ProviderOperationKind.TOOL_REPAIR)
    recorder.snapshot(
        "repair_probe_opened",
        "open_attempt",
        execution,
        decision={
            "permit": {
                "generation": probe._permit.generation,
                "probe": probe._permit.probe,
            }
        },
    )
    correction_error = http_500()
    clock.advance()
    action = await probe.correct(correction_error)
    recorder.snapshot(
        "correction_with_budget",
        "attempt_correct",
        execution,
        correction=action.value,
        decision={"error_descriptor": describe(correction_error)},
    )
    clock.advance()
    await probe.aclose()
    recorder.snapshot("close_after_correction", "attempt_close", execution)

    # A two-attempt controller: once the budget is spent, correction on the
    # final attempt turns FINAL and fails the execution.
    tight = build_controller(clock, max_attempts=2)
    tight_recorder = Recorder(tight, clock, prefix="tight_")
    exhausted = start(tight_recorder, tight, request_id="req-4", model="model-d")
    clock.advance()
    first = await tight._open_attempt(exhausted, ProviderOperationKind.GENERATION)
    tight_recorder.snapshot(
        "tight_first_opened",
        "open_attempt",
        exhausted,
        decision={
            "permit": {
                "generation": first._permit.generation,
                "probe": first._permit.probe,
            }
        },
    )
    rate_error = retryable_429()
    clock.advance()
    tight_decision = await first.fail(rate_error)
    tight_recorder.snapshot(
        "tight_failure",
        "attempt_fail",
        exhausted,
        decision={
            "retryable": tight_decision.retryable,
            "retry_allowed": tight_decision.retry_allowed,
            "error_descriptor": describe(rate_error),
        },
    )
    clock.advance()
    await first.aclose()
    tight_recorder.snapshot("tight_close_after_failure", "attempt_close", exhausted)
    clock.advance()
    terminal = await tight._open_attempt(exhausted, ProviderOperationKind.GENERATION)
    tight_recorder.snapshot(
        "tight_probe_opened",
        "open_attempt",
        exhausted,
        decision={
            "permit": {
                "generation": terminal._permit.generation,
                "probe": terminal._permit.probe,
            }
        },
    )
    final_error = retryable_503()
    clock.advance()
    action = await terminal.correct(final_error)
    tight_recorder.snapshot(
        "correction_when_exhausted",
        "attempt_correct",
        exhausted,
        correction=action.value,
        decision={"error_descriptor": describe(final_error)},
    )
    clock.advance()
    await terminal.aclose()
    tight_recorder.snapshot("close_after_final_correction", "attempt_close", exhausted)
    return [recorder, tight_recorder]


async def scenario_abandoned() -> Recorder:
    clock = VirtualClock()
    controller = build_controller(clock)
    recorder = Recorder(controller, clock)
    execution = start(recorder, controller, request_id="req-5", model="model-e")
    clock.advance()
    attempt = await controller._open_attempt(
        execution, ProviderOperationKind.GENERATION
    )
    recorder.snapshot(
        "open_for_abandon",
        "open_attempt",
        execution,
        decision={
            "permit": {
                "generation": attempt._permit.generation,
                "probe": attempt._permit.probe,
            }
        },
    )
    first_error = retryable_503()
    clock.advance()
    await controller._attempt_failed(
        execution, attempt._permit, error=first_error, status=503
    )
    recorder.snapshot(
        "controller_marks_failure",
        "controller_attempt_failed",
        execution,
        decision={"error_descriptor": describe(first_error)},
    )
    clock.advance()
    await attempt.aclose()
    recorder.snapshot(
        "unresolved_close_abandons",
        "attempt_close",
        execution,
        decision={"error_descriptor": describe(first_error)},
    )

    # Resolved attempts closing again must not touch recovery or capacity.
    clock.advance()
    second = await controller._open_attempt(execution, ProviderOperationKind.GENERATION)
    recorder.snapshot(
        "second_attempt_opened",
        "open_attempt",
        execution,
        decision={
            "permit": {
                "generation": second._permit.generation,
                "probe": second._permit.probe,
            }
        },
    )
    clock.advance()
    await second.accept()
    recorder.snapshot("resolved_before_close", "attempt_accept", execution)
    clock.advance()
    await second.aclose()
    recorder.snapshot("first_close_releases_slot", "attempt_close", execution)
    capacity_before = controller._capacity._active_attempts
    clock.advance()
    await second.aclose()
    assert controller._capacity._active_attempts == capacity_before
    recorder.snapshot("second_close_is_noop", "attempt_close", execution)
    return recorder


async def scenario_run_call(
    controller: ProviderAdmissionController,
    recorder: Recorder,
    clock: VirtualClock,
    label: str,
    model: str,
    script: list,
    *,
    max_attempts: int = 3,
    request_id: str = "req-run",
) -> Recorder:
    execution = start(recorder, controller, request_id=request_id, model=model)
    recorder.label(execution)
    calls = {"n": 0}

    async def fn():
        index = calls["n"]
        calls["n"] += 1
        item = script[index]
        if isinstance(item, Exception):
            raise item
        return item

    raised = None
    raised_type = None
    result = None
    clock.advance()
    try:
        result = await execution.run_call(
            fn, operation_kind=ProviderOperationKind.GENERATION
        )
    except Exception as exc:
        raised = str(exc)
        raised_type = type(exc).__name__
    script_view = [
        {"descriptor": describe(item)}
        if isinstance(item, Exception)
        else {"result": item}
        for item in script
    ]
    recorder.snapshot(
        label,
        "run_call",
        execution,
        raised=raised,
        raised_type=raised_type,
        result=result,
        decision={
            "calls": calls["n"],
            "max_attempts": max_attempts,
            "script": script_view,
        },
    )
    return recorder


async def scenario_run_call_sequences() -> Recorder:
    clock = VirtualClock()
    controller = build_controller(clock)
    recorder = Recorder(controller, clock)
    await scenario_run_call(
        controller,
        recorder,
        clock,
        "retry_then_succeed",
        "model-r",
        [retryable_503(), retryable_429(), "ok"],
    )
    await scenario_run_call(
        controller,
        recorder,
        clock,
        "non_retryable_propagates",
        "model-f",
        [fatal_401()],
    )
    return recorder


async def scenario_run_call_exhausted() -> Recorder:
    clock = VirtualClock()
    controller = build_controller(clock, max_attempts=2)
    recorder = Recorder(controller, clock)
    await scenario_run_call(
        controller,
        recorder,
        clock,
        "retries_exhausted",
        "model-x",
        [retryable_503(), retryable_503()],
        max_attempts=2,
    )
    return recorder


async def scenario_guards() -> Recorder:
    clock = VirtualClock()
    controller = build_controller(clock)
    recorder = Recorder(controller, clock)
    execution = start(recorder, controller, request_id="req-6", model="model-g")

    # An active claim blocks a second open.
    clock.advance()
    first = await controller._open_attempt(execution, ProviderOperationKind.GENERATION)
    recorder.snapshot(
        "guard_first_opened",
        "open_attempt",
        execution,
        decision={
            "permit": {
                "generation": first._permit.generation,
                "probe": first._permit.probe,
            }
        },
    )
    raised = None
    clock.advance()
    try:
        await controller._open_attempt(execution, ProviderOperationKind.GENERATION)
    except RuntimeError as exc:
        raised = str(exc)
    recorder.snapshot(
        "double_open_rejected",
        "open_attempt",
        execution,
        raised=raised,
        raised_type="RuntimeError",
    )
    clock.advance()
    await first.aclose()
    recorder.snapshot("guard_close_releases_slot", "attempt_close", execution)

    # A closed controller rejects every gate visit.
    clock.advance()
    controller.close()
    recorder.snapshot("controller_closed", "controller_close", execution)
    raised = None
    raised_type = None
    clock.advance()
    try:
        await controller._open_attempt(execution, ProviderOperationKind.GENERATION)
    except RuntimeError as exc:
        raised = str(exc)
        raised_type = type(exc).__name__
    recorder.snapshot(
        "closed_controller_rejects",
        "open_attempt",
        execution,
        raised=raised,
        raised_type=raised_type,
    )
    return recorder


async def main() -> None:
    scenarios = {
        "attempt_decisions": await scenario_attempt_decisions(),
        "non_retryable": await scenario_non_retryable(),
        "correction": await scenario_correction(),
        "abandoned": await scenario_abandoned(),
        "run_call_sequences": await scenario_run_call_sequences(),
        "run_call_exhausted": await scenario_run_call_exhausted(),
        "guards": await scenario_guards(),
    }
    normalized: dict[str, dict[str, object]] = {}
    for name, value in scenarios.items():
        recorders = value if isinstance(value, list) else [value]
        executions: list[dict[str, object]] = []
        steps: list[dict[str, object]] = []
        for index, recorder in enumerate(recorders):
            for entry in recorder.manifest():
                entry["controller"] = index
                executions.append(entry)
            for step in recorder.steps:
                step["controller"] = index
                steps.append(step)
        normalized[name] = {"executions": executions, "steps": steps}
    fixture = {
        "schema": "phase4c3c-attempt-v1",
        "correction_actions": [a.value for a in ProviderCorrectionAction],
        "exhausted_message": "Provider recovery retry budget was exhausted.",
        "scenarios": normalized,
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(fixture, indent=2) + "\n")
    total = sum(len(entry["steps"]) for entry in normalized.values())
    print(f"wrote {OUTPUT} with {len(normalized)} scenarios, {total} steps")


if __name__ == "__main__":
    asyncio.run(main())
