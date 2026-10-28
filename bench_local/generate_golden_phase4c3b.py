"""Generate phase 4c-3b golden fixture: recovery episode decision core.

Drives the real ProviderAdmissionController episode lifecycle with a
virtual monotonic clock and scripted jitter, bypassing the capacity gate
(covered in 4c-1) and blocking waits (a later transport slice). Every
decision call is followed by a full episode-map snapshot so the Go port
replays the state machine without async machinery.
"""

import asyncio
import json
from pathlib import Path

import httpx2

from free_claude_code.providers import admission
from free_claude_code.providers.admission import (
    ProviderAdmissionController,
    ProviderExecution,
    ProviderOperationKind,
    ProviderRecoveryExhausted,
    _GatePermit,
)

OUTPUT = Path("gocore/testdata/golden_recovery4c3b.json")
ORIGIN = 1_000.0
JITTER_DRAWS = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8]


class VirtualClock:
    def __init__(self) -> None:
        self.current = ORIGIN

    def now(self) -> float:
        return self.current

    def advance(self, seconds: float) -> None:
        self.current += seconds


def make_error(name: str, message: str, *, status_code: int) -> Exception:
    request = httpx2.Request("POST", "https://provider.test/v1/chat/completions")
    response = httpx2.Response(
        status_code,
        request=request,
        headers={"retry-after": "0"},
    )
    cls = type(name, (Exception,), {})
    error = cls(message)
    error.status_code = status_code
    error.response = response
    return error


def transient_503(name: str = "InternalServerError") -> Exception:
    return make_error(name, "upstream unavailable", status_code=503)


def transient_429() -> Exception:
    return make_error("RateLimitError", "rate limited", status_code=429)


def fatal_400() -> Exception:
    return make_error("BadRequestError", "bad request", status_code=400)


class Recorder:
    def __init__(
        self,
        controller: ProviderAdmissionController,
        clock: VirtualClock,
        max_attempts: int,
    ) -> None:
        self.controller = controller
        self.clock = clock
        self.max_attempts = max_attempts
        self.labels: dict[int, str] = {}
        self.executions: list[ProviderExecution] = []
        self.claims: dict[int, int] = {}
        self.open_claims: dict[int, bool] = {}
        self.steps: list[dict[str, object]] = []
        self._seq = 0

    def label_for(self, execution: ProviderExecution) -> str:
        key = id(execution)
        if key not in self.labels:
            self._seq += 1
            self.labels[key] = f"exec_{self._seq}"
            self.executions.append(execution)
            self.claims[key] = 0
            self.open_claims[key] = False
        return self.labels[key]

    def record_claim(self, execution: ProviderExecution, *, closed: bool) -> None:
        key = id(execution)
        self.claims[key] += 1
        self.open_claims[key] = not closed

    def manifest(self) -> list[dict[str, object]]:
        return [
            {
                "label": self.label_for(execution),
                "model": execution.model if execution.model is not None else "",
                "claims": self.claims[id(execution)],
                "open_claim": self.open_claims[id(execution)],
            }
            for execution in self.executions
        ]

    def error_view(self, error: Exception | None) -> dict[str, str] | None:
        if error is None:
            return None
        return {"kind": type(error).__name__, "message": str(error)}

    def episode_view(self) -> dict[str, object]:
        result: dict[str, object] = {}
        for model, episode in self.controller._episodes.items():
            # None (model discovery) and Go's empty string share the "" key.
            result[model if model is not None else ""] = {
                "generation": episode.generation,
                "leader": (
                    self.label_for(episode.leader)
                    if episode.leader is not None
                    else None
                ),
                "ready_at": round(episode.ready_at - ORIGIN, 6),
                "probe_active": episode.probe_active,
                "terminal_until": (
                    round(episode.terminal_until - ORIGIN, 6)
                    if episode.terminal_until is not None
                    else None
                ),
                "waiters": sorted(self.label_for(w) for w in episode.waiters),
                "last_error": self.error_view(episode.last_error),
            }
        return result

    def snapshot(
        self,
        label: str,
        op: str,
        *,
        raised: str | None = None,
        permit: dict[str, object] | None = None,
        error_input: dict[str, object] | None = None,
        status: int | None = None,
        target: str | None = None,
    ) -> None:
        self.steps.append(
            {
                "label": label,
                "op": op,
                "now": round(self.clock.now() - ORIGIN, 6),
                "target": target,
                "raised": raised,
                "permit": permit,
                "error_input": error_input,
                "status": status,
                "episodes": self.episode_view(),
                "next_generation": self.controller._next_generation,
                "closed": self.controller._closed,
                "execution_states": self.execution_states(),
            }
        )

    def execution_states(self) -> dict[str, str]:
        return {
            self.label_for(execution): execution.state.value
            for execution in self.executions
        }


def normal_permit() -> _GatePermit:
    return _GatePermit(generation=None, probe=False)


def build_controller(
    clock: VirtualClock, *, max_attempts: int = 3
) -> ProviderAdmissionController:
    draws = list(JITTER_DRAWS)

    def fake_uniform(low: float, high: float) -> float:
        if high == 0.0:
            return 0.0
        return draws.pop(0)

    admission.random.uniform = fake_uniform
    admission.time.monotonic = clock.now
    return ProviderAdmissionController(
        provider_name="golden",
        rate_limit=40,
        rate_window=60.0,
        max_concurrency=5,
        max_attempts=max_attempts,
    )


def start(
    controller: ProviderAdmissionController, recorder: Recorder, model: str | None
) -> ProviderExecution:
    execution = controller.start_execution(request_id=None, model=model)
    recorder.label_for(execution)
    return execution


def claim(
    recorder: Recorder, execution: ProviderExecution, *, closed: bool = False
) -> object:
    opened = execution._claim_attempt(ProviderOperationKind.GENERATION)
    recorder.record_claim(execution, closed=closed)
    if closed:
        execution._close_attempt(opened)
    return opened


def error_input(error: Exception) -> dict[str, object]:
    return {"kind": type(error).__name__, "message": str(error)}


def arm(
    recorder: Recorder, label: str, execution: ProviderExecution, episode: object
) -> None:
    """Record the probe reservation a real gate visit would perform."""
    episode.probe_active = True
    recorder.snapshot(
        label,
        "arm_probe",
        target=recorder.label_for(execution),
        permit={"generation": episode.generation, "probe": True},
    )


async def scenario_open_probe_accept(clock: VirtualClock) -> Recorder:
    controller = build_controller(clock)
    recorder = Recorder(controller, clock, 3)
    leader = start(controller, recorder, "model-x")
    waiter = start(controller, recorder, "model-x")
    claim(recorder, leader)
    claim(recorder, waiter)

    first = transient_503()
    await controller._attempt_failed(leader, normal_permit(), error=first, status=503)
    recorder.snapshot(
        "leader_opens_episode",
        "attempt_failed",
        target=recorder.label_for(leader),
        error_input=error_input(first),
        status=503,
    )

    second = transient_429()
    await controller._attempt_failed(waiter, normal_permit(), error=second, status=429)
    recorder.snapshot(
        "waiter_coalesces",
        "attempt_failed",
        target=recorder.label_for(waiter),
        error_input=error_input(second),
        status=429,
    )

    episode = controller._episodes["model-x"]
    arm(recorder, "arm_leader_probe", leader, episode)
    probe = _GatePermit(generation=episode.generation, probe=True)
    clock.advance(2.5)
    third = transient_503()
    await controller._attempt_failed(leader, probe, error=third, status=503)
    recorder.snapshot(
        "probe_failure_reschedules",
        "attempt_failed",
        target=recorder.label_for(leader),
        error_input=error_input(third),
        status=503,
        permit={"generation": episode.generation, "probe": True},
    )

    clock.advance(4.0)
    arm(recorder, "rearm_leader_probe", leader, episode)
    await controller._attempt_accepted(leader, probe)
    recorder.snapshot(
        "probe_accept_closes_episode",
        "attempt_accepted",
        target=recorder.label_for(leader),
        permit={"generation": episode.generation, "probe": True},
    )
    return recorder


async def scenario_probe_rejected(clock: VirtualClock) -> Recorder:
    controller = build_controller(clock)
    recorder = Recorder(controller, clock, 3)
    leader = start(controller, recorder, "model-r")
    claim(recorder, leader)
    error = transient_503()
    await controller._attempt_failed(leader, normal_permit(), error=error, status=503)
    recorder.snapshot(
        "failure_opens_episode",
        "attempt_failed",
        target=recorder.label_for(leader),
        error_input=error_input(error),
        status=503,
    )
    episode = controller._episodes["model-r"]
    arm(recorder, "arm_probe", leader, episode)
    probe = _GatePermit(generation=episode.generation, probe=True)
    await controller._attempt_rejected(leader, probe)
    recorder.snapshot(
        "probe_rejection_closes_episode",
        "attempt_rejected",
        target=recorder.label_for(leader),
        permit={"generation": episode.generation, "probe": True},
    )
    return recorder


async def scenario_abandon_and_leadership_transfer(
    clock: VirtualClock,
) -> Recorder:
    controller = build_controller(clock)
    recorder = Recorder(controller, clock, 3)
    first = start(controller, recorder, "model-t")
    second = start(controller, recorder, "model-t")
    claim(recorder, first)
    claim(recorder, second)
    error = transient_503()
    await controller._attempt_failed(first, normal_permit(), error=error, status=503)
    recorder.snapshot(
        "first_opens_episode",
        "attempt_failed",
        target=recorder.label_for(first),
        error_input=error_input(error),
        status=503,
    )
    episode = controller._episodes["model-t"]
    # A live waiter keeps the abandoned episode eligible for leadership.
    episode.waiters.add(second)
    recorder.snapshot(
        "register_waiter", "add_waiter", target=recorder.label_for(second)
    )
    arm(recorder, "arm_first_probe", first, episode)
    probe = _GatePermit(generation=episode.generation, probe=True)
    await controller._attempt_abandoned(first, probe)
    recorder.snapshot(
        "probe_abandoned_leader_steps_down",
        "attempt_abandoned",
        target=recorder.label_for(first),
        permit={"generation": episode.generation, "probe": True},
    )

    transferred = await controller._wait_for_gate(second)
    recorder.snapshot(
        "second_takes_leadership",
        "wait_for_gate",
        target=recorder.label_for(second),
        permit={"generation": transferred.generation, "probe": transferred.probe},
    )
    return recorder


async def scenario_exhaustion(clock: VirtualClock) -> Recorder:
    controller = build_controller(clock, max_attempts=1)
    recorder = Recorder(controller, clock, 1)
    only = start(controller, recorder, "model-e")
    claim(recorder, only)
    error = transient_503()
    await controller._attempt_failed(only, normal_permit(), error=error, status=503)
    recorder.snapshot(
        "single_attempt_exhausts_episode",
        "attempt_failed",
        target=recorder.label_for(only),
        error_input=error_input(error),
        status=503,
    )

    other = start(controller, recorder, "model-e")
    raised = None
    try:
        await controller._wait_for_gate(other)
    except ProviderRecoveryExhausted as exc:
        raised = str(exc)
    recorder.snapshot(
        "gate_raises_when_terminal",
        "wait_for_gate",
        target=recorder.label_for(other),
        raised=raised,
    )
    return recorder


async def scenario_idle_expiry(clock: VirtualClock) -> Recorder:
    controller = build_controller(clock)
    recorder = Recorder(controller, clock, 3)

    # An abandoned probe with no waiters is collectable immediately because
    # its ready_at resets to now.
    lone = start(controller, recorder, "model-i")
    claim(recorder, lone)
    lone_error = transient_503()
    await controller._attempt_failed(
        lone, normal_permit(), error=lone_error, status=503
    )
    recorder.snapshot(
        "lone_failure_opens_episode",
        "attempt_failed",
        target=recorder.label_for(lone),
        error_input=error_input(lone_error),
        status=503,
    )
    lone_episode = controller._episodes["model-i"]
    arm(recorder, "arm_lone_probe", lone, lone_episode)
    lone_probe = _GatePermit(generation=lone_episode.generation, probe=True)
    await controller._attempt_abandoned(lone, lone_probe)
    recorder.snapshot(
        "abandoned_without_waiters_expires_at_once",
        "attempt_abandoned",
        target=recorder.label_for(lone),
        permit={"generation": lone_episode.generation, "probe": True},
    )

    # A terminal episode (exhausted budget) is retained until terminal_until.
    exhausted = start(controller, recorder, "model-term")
    for _ in range(3):
        claim(recorder, exhausted, closed=True)
    terminal_error = transient_503()
    await controller._attempt_failed(
        exhausted, normal_permit(), error=terminal_error, status=503
    )
    terminal = controller._episodes["model-term"]
    terminal_until = terminal.terminal_until
    assert terminal_until is not None
    recorder.snapshot(
        "terminal_episode_opened",
        "attempt_failed",
        target=recorder.label_for(exhausted),
        error_input=error_input(terminal_error),
        status=503,
    )

    clock.current = terminal_until - 1.0
    controller._maintain_idle_episodes()
    recorder.snapshot("maintain_before_terminal_until_keeps_episode", "maintain_idle")

    clock.current = terminal_until
    controller._maintain_idle_episodes()
    recorder.snapshot("maintain_at_terminal_until_expires_episode", "maintain_idle")
    return recorder


async def scenario_corrected(clock: VirtualClock) -> Recorder:
    controller = build_controller(clock)
    recorder = Recorder(controller, clock, 3)
    leader = start(controller, recorder, "model-c")
    claim(recorder, leader)
    error = transient_503()
    await controller._attempt_failed(leader, normal_permit(), error=error, status=503)
    recorder.snapshot(
        "failure_opens_episode",
        "attempt_failed",
        target=recorder.label_for(leader),
        error_input=error_input(error),
        status=503,
    )
    episode = controller._episodes["model-c"]
    arm(recorder, "arm_probe", leader, episode)
    probe = _GatePermit(generation=episode.generation, probe=True)
    clock.advance(1.0)
    await controller._attempt_corrected(leader, probe)
    recorder.snapshot(
        "correction_resets_probe",
        "attempt_corrected",
        target=recorder.label_for(leader),
        permit={"generation": episode.generation, "probe": True},
    )
    return recorder


async def scenario_terminated_leader_stepdown(
    clock: VirtualClock,
) -> Recorder:
    controller = build_controller(clock)
    recorder = Recorder(controller, clock, 3)
    leader = start(controller, recorder, "model-s")
    waiter = start(controller, recorder, "model-s")
    claim(recorder, leader)
    claim(recorder, waiter)
    leader_error = transient_503()
    await controller._attempt_failed(
        leader, normal_permit(), error=leader_error, status=503
    )
    recorder.snapshot(
        "leader_opens_episode",
        "attempt_failed",
        target=recorder.label_for(leader),
        error_input=error_input(leader_error),
        status=503,
    )
    waiter_error = transient_429()
    await controller._attempt_failed(
        waiter, normal_permit(), error=waiter_error, status=429
    )
    recorder.snapshot(
        "waiter_coalesces",
        "attempt_failed",
        target=recorder.label_for(waiter),
        error_input=error_input(waiter_error),
        status=429,
    )
    leader.succeed()
    recorder.snapshot(
        "leader_success_steps_down_keeps_waiter",
        "succeed",
        target=recorder.label_for(leader),
    )
    return recorder


async def scenario_discovery_none_model(clock: VirtualClock) -> Recorder:
    controller = build_controller(clock)
    recorder = Recorder(controller, clock, 3)
    discovery = start(controller, recorder, None)
    claim(recorder, discovery)
    error = transient_503()
    await controller._attempt_failed(
        discovery, normal_permit(), error=error, status=503
    )
    recorder.snapshot(
        "discovery_episode_under_none_model",
        "attempt_failed",
        target=recorder.label_for(discovery),
        error_input=error_input(error),
        status=503,
    )
    return recorder


async def scenario_close(clock: VirtualClock) -> Recorder:
    controller = build_controller(clock)
    recorder = Recorder(controller, clock, 3)
    execution = start(controller, recorder, "model-z")
    claim(recorder, execution)
    error = transient_503()
    await controller._attempt_failed(
        execution, normal_permit(), error=error, status=503
    )
    recorder.snapshot(
        "failure_opens_episode",
        "attempt_failed",
        target=recorder.label_for(execution),
        error_input=error_input(error),
        status=503,
    )
    controller.close()
    recorder.snapshot("close_clears_episodes", "close")

    raised = None
    try:
        await controller._wait_for_gate(execution)
    except RuntimeError as exc:
        raised = str(exc)
    recorder.snapshot(
        "gate_raises_when_closed",
        "wait_for_gate",
        target=recorder.label_for(execution),
        raised=raised,
    )
    return recorder


async def main() -> None:
    clock = VirtualClock()
    scenarios = {
        "open_probe_accept": await scenario_open_probe_accept(clock),
        "probe_rejected": await scenario_probe_rejected(clock),
        "abandon_leadership_transfer": await scenario_abandon_and_leadership_transfer(
            clock
        ),
        "exhaustion": await scenario_exhaustion(clock),
        "idle_expiry": await scenario_idle_expiry(clock),
        "corrected": await scenario_corrected(clock),
        "terminated_leader_stepdown": await scenario_terminated_leader_stepdown(clock),
        "discovery_none_model": await scenario_discovery_none_model(clock),
        "close": await scenario_close(clock),
    }
    fixture = {
        "schema": "phase4c3b-recovery-v1",
        "clock_origin": ORIGIN,
        "jitter_draws": JITTER_DRAWS,
        "retry_config": {
            "base_delay": admission.DEFAULT_UPSTREAM_BASE_DELAY,
            "max_delay": admission.DEFAULT_UPSTREAM_MAX_DELAY,
            "jitter": admission.DEFAULT_UPSTREAM_JITTER,
        },
        "exhausted_message": "Provider recovery retry budget was exhausted.",
        "closed_message": "provider admission controller is closed",
        "scenarios": {
            name: {
                "max_attempts": recorder.max_attempts,
                "executions": recorder.manifest(),
                "steps": recorder.steps,
            }
            for name, recorder in scenarios.items()
        },
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(fixture, indent=2) + "\n")
    total = sum(len(entry["steps"]) for entry in fixture["scenarios"].values())
    print(f"wrote {OUTPUT} with {len(scenarios)} scenarios, {total} steps")


if __name__ == "__main__":
    asyncio.run(main())
