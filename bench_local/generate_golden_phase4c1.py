"""Generate phase 4c-1 golden fixture: admission limits and capacity limiter.

The generator drives the real ``ProviderCapacityLimiter`` from the Python
reference under a virtual monotonic clock and a no-op ``asyncio.timeout``
stand-in. Every wait is resumed explicitly by the harness (clock advance
plus ``wake_waiters``, or ``release``), so output is deterministic and
contains no wall-clock timing.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from free_claude_code.providers import admission_capacity as ac
from free_claude_code.providers.admission_policy import ProviderAdmissionLimits

OUTPUT = Path("gocore/testdata/golden_admission4c1.json")


class FakeTimeout:
    """Stand-in for asyncio.timeout: waits are resumed explicitly."""

    def __init__(self, delay: float | None) -> None:
        self.delay = delay

    async def __aenter__(self) -> FakeTimeout:
        return self

    async def __aexit__(self, *exc_info: object) -> bool:
        return False


class VirtualClock:
    def __init__(self) -> None:
        self.current = 1_000.0  # nonzero origin so relative math stays obvious

    def now(self) -> float:
        return self.current

    def advance(self, seconds: float) -> None:
        self.current += seconds


def make_limiter(
    clock: VirtualClock, rate_limit: int, rate_window: float, max_concurrency: int
) -> ac.ProviderCapacityLimiter:
    limiter = ac.ProviderCapacityLimiter(
        ProviderAdmissionLimits(rate_limit, rate_window, max_concurrency)
    )
    ac.time.monotonic = clock.now  # type: ignore[attr-defined]
    ac.asyncio.timeout = FakeTimeout  # type: ignore[attr-defined]
    return limiter


def snapshot(
    limiter: ac.ProviderCapacityLimiter,
    clock: VirtualClock,
    label: str,
    outcome: str | None,
    op: str,
    limits: dict[str, object] | None = None,
) -> dict[str, object]:
    origin = 1_000.0
    return {
        "label": label,
        "op": op,
        "now": round(clock.current - origin, 6),
        "limits": limits,
        "admission_times": [round(t - origin, 6) for t in limiter._admission_times],
        "last_discarded_at": (
            round(limiter._last_discarded_at - origin, 6)
            if limiter._last_discarded_at is not None
            else None
        ),
        "active_attempts": limiter._active_attempts,
        "outcome": outcome,
    }


def ready_at(limiter: ac.ProviderCapacityLimiter, now: float) -> float:
    """Mirror the readiness expression inside ProviderCapacityLimiter.acquire."""
    limits = limiter._limits
    ready = now
    if limiter._last_discarded_at is not None:
        ready = max(ready, limiter._last_discarded_at + limits.rate_window)
    if len(limiter._admission_times) >= limits.rate_limit:
        ready = max(
            ready,
            limiter._admission_times[-limits.rate_limit] + limits.rate_window,
        )
    return ready


async def settle(task: asyncio.Task[bool]) -> str | None:
    for _ in range(8):
        await asyncio.sleep(0)
        if task.done():
            return "admitted" if task.result() else "returned_false"
    return None


async def run_rate_window(clock: VirtualClock) -> list[dict[str, object]]:
    """Rate limit of 3 in a 10s window, concurrency 5: rate binds first."""
    limiter = make_limiter(clock, rate_limit=3, rate_window=10.0, max_concurrency=5)
    steps: list[dict[str, object]] = []
    permit_valid = True
    for index in range(3):
        task = asyncio.create_task(limiter.acquire(lambda: permit_valid))
        outcome = await settle(task)
        steps.append(snapshot(
            limiter, clock, f"initial_admit_{index}", outcome, "admit"))

    # Fourth admission must block: rate bucket is full.
    blocked = asyncio.create_task(limiter.acquire(lambda: permit_valid))
    assert await settle(blocked) is None
    steps.append(snapshot(limiter, clock, "rate_blocked", None, "expect_blocked"))

    # Advancing to the earliest slot's readiness wakes and admits the waiter.
    clock.advance(ready_at(limiter, clock.current) - clock.current)
    limiter.wake_waiters()
    outcome = await settle(blocked)
    steps.append(snapshot(
        limiter, clock, "window_slide_admit", outcome, "advance_wake_admit"))

    for index in range(4):
        limiter.release()
        steps.append(snapshot(
            limiter, clock, f"release_{index}", None, "release"))
    return steps


async def run_concurrency_cap(clock: VirtualClock) -> list[dict[str, object]]:
    """Rate limit generous, concurrency 2: occupancy binds."""
    limiter = make_limiter(clock, rate_limit=10, rate_window=30.0, max_concurrency=2)
    steps: list[dict[str, object]] = []
    permit_valid = True
    for index in range(2):
        task = asyncio.create_task(limiter.acquire(lambda: permit_valid))
        steps.append(snapshot(
            limiter, clock, f"concurrency_admit_{index}", await settle(task),
            "admit"))

    blocked = asyncio.create_task(limiter.acquire(lambda: permit_valid))
    assert await settle(blocked) is None
    steps.append(snapshot(
        limiter, clock, "concurrency_blocked", None, "expect_blocked"))

    limiter.release()
    steps.append(snapshot(
        limiter, clock, "release_unblocks", await settle(blocked),
        "release_then_admit"))
    limiter.release()
    steps.append(snapshot(limiter, clock, "final_release", None, "release"))
    return steps


async def run_reconfigure(clock: VirtualClock) -> list[dict[str, object]]:
    """Reconfiguration keeps occupancy/history but applies new limits."""
    limiter = make_limiter(clock, rate_limit=2, rate_window=10.0, max_concurrency=1)
    steps: list[dict[str, object]] = []
    permit_valid = True
    task = asyncio.create_task(limiter.acquire(lambda: permit_valid))
    steps.append(snapshot(
        limiter, clock, "admit_under_old_limits", await settle(task), "admit"))

    blocked = asyncio.create_task(limiter.acquire(lambda: permit_valid))
    assert await settle(blocked) is None
    steps.append(snapshot(
        limiter, clock, "blocked_under_old_limits", None, "expect_blocked"))

    new_limits = ProviderAdmissionLimits(5, 20.0, 3)
    limiter.reconfigure(new_limits)
    steps.append(snapshot(
        limiter, clock, "reconfigured", await settle(blocked), "reconfigure_admit",
        limits={
            "rate_limit": new_limits.rate_limit,
            "rate_window": new_limits.rate_window,
            "max_concurrency": new_limits.max_concurrency,
        },
    ))
    limiter.release()
    steps.append(snapshot(limiter, clock, "release_after_reconfigure", None, "release"))
    return steps


async def run_permit_invalidation(clock: VirtualClock) -> list[dict[str, object]]:
    """An invalid recovery permit makes acquire leave without charging."""
    limiter = make_limiter(clock, rate_limit=10, rate_window=30.0, max_concurrency=1)
    steps: list[dict[str, object]] = []
    permit_valid = True
    holder = asyncio.create_task(limiter.acquire(lambda: permit_valid))
    steps.append(snapshot(
        limiter, clock, "holder_admit", await settle(holder), "admit"))

    waiter = asyncio.create_task(limiter.acquire(lambda: permit_valid))
    assert await settle(waiter) is None
    steps.append(snapshot(
        limiter, clock, "waiter_blocked", None, "expect_blocked"))
    permit_valid = False
    limiter.wake_waiters()
    steps.append(snapshot(
        limiter, clock, "permit_invalidated", await settle(waiter),
        "permit_invalidated"))
    limiter.release()
    steps.append(snapshot(limiter, clock, "holder_releases", None, "release"))
    return steps


async def run_window_eviction(clock: VirtualClock) -> list[dict[str, object]]:
    """Long-running slide: stale timestamps are discarded in admission order."""
    limiter = make_limiter(clock, rate_limit=2, rate_window=5.0, max_concurrency=4)
    steps: list[dict[str, object]] = []
    permit_valid = True
    for index in range(6):
        task = asyncio.create_task(limiter.acquire(lambda: permit_valid))
        if await settle(task) is None:
            clock.advance(ready_at(limiter, clock.current) - clock.current)
            limiter.wake_waiters()
            outcome = await settle(task)
        else:
            outcome = "admitted"
        op = "admit" if outcome == "admitted" else "advance_wake_admit"
        steps.append(snapshot(
            limiter, clock, f"cycled_admit_{index}", outcome, op))
        limiter.release()
        steps.append(snapshot(
            limiter, clock, f"cycled_release_{index}", None, "release"))
    return steps


def validation_cases() -> list[dict[str, object]]:
    cases: list[dict[str, object]] = []
    invalid: list[tuple[str, object, object, object]] = [
        ("zero_rate_limit", 0, 1.0, 1),
        ("negative_rate_limit", -2, 1.0, 1),
        ("bool_rate_limit", True, 1.0, 1),
        ("float_rate_limit", 1.5, 1.0, 1),
        ("zero_concurrency", 1, 1.0, 0),
        ("negative_concurrency", 1, 1.0, -3),
        ("bool_concurrency", 1, 1.0, False),
        ("float_concurrency", 1, 1.0, 2.5),
        ("zero_window", 1, 0.0, 1),
        ("negative_window", 1, -1.0, 1),
        ("nan_window", 1, float("nan"), 1),
        ("inf_window", 1, float("inf"), 1),
    ]
    for label, rate_limit, rate_window, max_concurrency in invalid:
        try:
            ProviderAdmissionLimits(rate_limit, rate_window, max_concurrency)  # type: ignore[arg-type]
        except (ValueError, TypeError) as exc:
            cases.append({"label": label, "error": str(exc)})
        else:  # pragma: no cover - defensive, generator must see every failure
            raise AssertionError(f"{label} should have been rejected")
    good = ProviderAdmissionLimits(4, 2.5, 7)
    cases.append({
        "label": "valid_limits",
        "rate_limit": good.rate_limit,
        "rate_window": good.rate_window,
        "max_concurrency": good.max_concurrency,
    })
    return cases


async def main() -> None:
    clock = VirtualClock()
    fixture = {
        "schema": "phase4c1-admission-v1",
        "validation_cases": validation_cases(),
        "scenarios": {
            "rate_window": {
                "limits": {"rate_limit": 3, "rate_window": 10.0,
                           "max_concurrency": 5},
                "steps": await run_rate_window(clock),
            },
            "concurrency_cap": {
                "limits": {"rate_limit": 10, "rate_window": 30.0,
                           "max_concurrency": 2},
                "steps": await run_concurrency_cap(clock),
            },
            "reconfigure": {
                "limits": {"rate_limit": 2, "rate_window": 10.0,
                           "max_concurrency": 1},
                "steps": await run_reconfigure(clock),
            },
            "permit_invalidation": {
                "limits": {"rate_limit": 10, "rate_window": 30.0,
                           "max_concurrency": 1},
                "steps": await run_permit_invalidation(clock),
            },
            "window_eviction": {
                "limits": {"rate_limit": 2, "rate_window": 5.0,
                           "max_concurrency": 4},
                "steps": await run_window_eviction(clock),
            },
        },
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(fixture, indent=2) + "\n")
    print(f"wrote {OUTPUT} with {len(fixture['validation_cases'])} validation "
          f"cases and {len(fixture['scenarios'])} scenarios")


if __name__ == "__main__":
    asyncio.run(main())
