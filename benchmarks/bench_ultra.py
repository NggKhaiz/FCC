#!/usr/bin/env python3
"""Micro-benchmarks for FCC native ultra-core (no pytest required)."""

from __future__ import annotations

import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from free_claude_code.native import (  # noqa: E402
    BloomFilter,
    SlidingWindow,
    backend,
    estimate_tokens_fast,
    fnv1a64,
    normalize_path_key,
    validate_model_ref_fast,
    validate_provider_id_fast,
)


def _bench(name: str, fn, rounds: int = 50_000) -> dict:
    # warmup
    for _ in range(1000):
        fn()
    times: list[float] = []
    for _ in range(rounds):
        t0 = time.perf_counter_ns()
        fn()
        times.append(time.perf_counter_ns() - t0)
    times.sort()
    avg = statistics.fmean(times)
    p50 = times[len(times) // 2]
    p99 = times[int(len(times) * 0.99)]
    return {
        "name": name,
        "rounds": rounds,
        "avg_ns": avg,
        "p50_ns": p50,
        "p99_ns": p99,
        "ops_per_sec": 1e9 / avg if avg else 0,
    }


def main() -> int:
    bloom = BloomFilter(10_000, 0.01)
    for i in range(1000):
        bloom.add(f"k{i}")
    window = SlidingWindow()

    jobs = [
        ("fnv1a64", lambda: fnv1a64("provider/model-name")),
        ("validate_provider", lambda: validate_provider_id_fast("openrouter")),
        ("validate_model", lambda: validate_model_ref_fast("groq/llama-3.1-70b")),
        ("normalize_path", lambda: normalize_path_key("/v1/messages")),
        ("estimate_tokens_64", lambda: estimate_tokens_fast("x" * 64)),
        ("bloom_contains", lambda: ("k42" in bloom)),
        (
            "sliding_allow",
            lambda: window.allow(
                "bench", max_requests=10_000_000, window_seconds=60, block_seconds=0
            ),
        ),
    ]

    print(f"backend={backend()}")
    print(f"{'name':22} {'avg_ns':>10} {'p50_ns':>10} {'p99_ns':>10} {'ops/s':>14}")
    results = []
    for name, fn in jobs:
        r = _bench(name, fn)
        results.append(r)
        print(
            f"{r['name']:22} {r['avg_ns']:10.1f} {r['p50_ns']:10.0f} "
            f"{r['p99_ns']:10.0f} {r['ops_per_sec']:14,.0f}"
        )

    # Write markdown summary
    out = ROOT / "benchmarks" / "RESULTS.md"
    lines = [
        "# FCC Ultra Core Benchmark Results",
        "",
        f"- backend: `{backend()}`",
        f"- generated: {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}",
        "",
        "| op | avg ns | p50 ns | p99 ns | ops/sec |",
        "|----|-------:|-------:|-------:|--------:|",
    ]
    for r in results:
        lines.append(
            f"| `{r['name']}` | {r['avg_ns']:.1f} | {r['p50_ns']:.0f} | "
            f"{r['p99_ns']:.0f} | {r['ops_per_sec']:,.0f} |"
        )
    lines.append("")
    lines.append(
        "Target: validators & bloom negative checks typically well under a few µs "
        "on CPython; Rust wheel (when built) should be faster still."
    )
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
