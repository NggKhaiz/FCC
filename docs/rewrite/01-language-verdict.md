# Language verdict: Go wins for FCC; Rust and Zig compared on evidence

## Decision

The rewrite proceeds in **Go**. The hot-path core in `gocore/` already runs
there and is proven byte-for-byte against golden data produced by the Python
reference. This document compares all three candidates the team raised —
Go, Rust, and Zig — against the actual shape of the product and the actual
build environment, and states the measured cost of each trade-off.

## What was actually measured

Both existing implementations run the identical pipeline on the identical
fixture: `gocore/testdata/stream.txt`, a 2,005-frame Anthropic SSE response
with text, thinking, tool-call deltas, and multi-byte content. Per frame
each implementation performs incremental frame splitting, event-name
extraction, a cached payload parse, failure scanning, and retention byte
accounting. 100 iterations per process, warm-up discarded, same two-core
machine.

| Metric | Python 3.14.10 (optimized) | Go 1.22.12 |
| --- | --- | --- |
| Latency per request | 83.1 ms | 5.4 ms |
| Throughput | 12.0 req/s | 186 req/s |
| Relative speed | 1.0x | **15.4x** |
| Live memory footprint | 1,743 KiB peak (tracemalloc) | 348 KiB live heap after GC |
| Allocation churn | reference | 3.0 MiB/request, 40,135 allocs |

Reproduce with `.venv/bin/python bench_local/bench_golden.py` and
`go run ./cmd/bench` from `gocore/`. Go is measured against the *fastest*
Python (after the full profiling-led optimization pass), not a strawman.
For scale, the end-to-end HTTP benchmark moved from 272.20 ms to 118.94 ms
per request through pure-Python work before any Go was written.

## Candidate comparison

| Dimension | Go 1.22 | Rust (stable) | Zig 0.14 |
| --- | --- | --- | --- |
| Hot-path speed vs optimized Python | **15.4x measured** | est. 25-40x (serde/simd-json, no GC) | est. 10-30x; LLVM backend, manual memory |
| Tail latency | sub-ms GC pauses, irrelevant vs multi-second model streams | none (no GC) | none (no GC) |
| Concurrent SSE pumps | goroutines + `context`: direct fit | async tokio + `Arc<Mutex<_>>` state machines | async ecosystem thin; roll your own events |
| Learning/review cost for a 75k-LOC port | low: one idiom, stdlib batteries | high: borrow checker, lifetimes, trait gymnastics | medium-high: small language, but every dependency is bespoke |
| Compile/test loop | seconds, scales to large module graphs | slow at this size; adversarial to rapid strangler migration | fast compiler, but toolchain still pre-1.0 with breaking releases |
| Distribution | one static binary, trivial cross-compile, pure-Go SQLite | static binary; cross-compile needs target sysroots/ring C for TLS | static binary; cross-compile good, TLS needs bearssl/ring choices |
| Ecosystem fit for 61 providers + Telegram/Discord/SQLite | mature, boring libraries everywhere | mature, but async fragmentation and version churn | thin: expect to build/adapt HTTP, DB, and bridge client layers |
| Decimal-correct JSON | `json.Number` wire-preserving | serde arbitrary_precision / custom deserializer | std.json is weak for this; custom parser likely |
| Installable in this build environment | **yes, bootstrapped from source** | no (see below) | effectively no (see below) |

### Why not Rust

Rust is the theoretical performance winner and would likely beat the Go
number by another 1.5-2.5x. That ceiling is real but not load-bearing here:

1. The measured 15x already exceeds the requirement. Wall clock is dominated
   by upstream model streams emitting tens of tokens per second; no language
   reclaims network and model time.
2. The workload is concurrent I/O with cancellation and fan-out, not number
   crunching. The stream recovery and tool-buffer state machines are exactly
   where `Arc<Mutex<...>>` and pin/lifetime reasoning tax the team hardest.
3. The surface is 75,000 lines across 423 modules and 61 providers. Rust's
   type system earns its cost on core engines; it taxes broad glue code.
4. Rust's strongest deployment case — tens of thousands of concurrent
   streams per node under a hard memory envelope — describes an edge
   gateway, not a developer-laptop/local-VM proxy.

The Go core is deliberately stdlib-only with language-agnostic golden
fixtures, so individual packages can be re-ported to Rust later if the
deployment shape changes, without re-deriving the semantics.

### Why not Zig

Zig's pitch — no runtime, no GC, LLVM-grade codegen, trivial cross-compile —
is attractive for a small hot-path binary, and its comptime would suit the
SSE state tables. Three things rule it out for the whole product:

1. **Ecosystem depth.** FCC needs HTTP/2 SSE clients for sixty-one provider
   dialects, SQLite, two chat bridges, desktop integration, and OAuth
   flows. Go and Rust have boring, proven libraries for all of it; Zig would
   make us write or maintain bindings for a large share.
2. **Toolchain stability.** Zig has not reached 1.0; standard library and
   build-system changes across releases make a multi-year migration risky
   for a project that already pins an exact Python runtime for reproducible
   installs.
3. **JSON and decimal fidelity.** The codebase carries exact wire numbers
   (ledger hashing, canonical JSON). Go's `json.Number` preserves them with
   no extra design; Zig's `std.json` would need a custom number-preserving
   parser from day one.

Zig remains a credible choice for a later micro-optimized native extension
behind the Go binary (for example, a SIMD frame tokenizer), invoked through
cgo-free IPC; the golden fixtures make that substitution contract-safe.

## Environment feasibility (factual, not preference)

The sandbox network allowlist only reaches `github.com` (including
`codeload`), `pypi.org`, and `files.pythonhosted.org`.

- **Go: built.** No prebuilt binary host was reachable, so the toolchain was
  bootstrapped entirely from source through the historical chain
  1.4.3 → 1.12.17 → 1.16.15 → 1.17.13 → 1.20.14 → 1.22.12, each stage
  compiling the next. The exact recipe is reproducible in
  `bootstrap.sh`-style steps.
- **Rust: blocked.** `rustup` and every release asset redirect to
  `static.rust-lang.org` or `objects.githubusercontent.com`, both outside
  the allowlist. A source build of `rustc` itself downloads stage snapshots
  from the same blocked hosts. No compiler could be produced, so Rust figures
  above are labeled engineering estimates, not measurements.
- **Zig: effectively blocked.** Modern Zig self-hosts and requires a
  prebuilt Zig compiler (fetched from `ziglang.org`, blocked) to build from
  source; the last C++-bootstrappable releases predate the self-hosted
  linker and std.json features this project would rely on, and building
  their bundled LLVM on two cores is a multi-hour, memory-heavy detour for
  a toolchain that still cannot be kept current inside the allowlist.

Choosing Go is therefore both the architectural decision and the only
decision that could be validated end to end in this environment.

## Go risks and how they are contained

- **No sum types.** Event state machines get explicit kinds plus
  default-to-unsafe handling: anything unrecognized sets an unsafe reason
  rather than being silently accepted, mirroring the Python design.
- **Decimal precision.** Every decode uses `json.Number`; the parity fixture
  includes a fractional payload that fails if the wire form is float-rounded.
- **GC tail latency.** Sub-millisecond against multi-second model responses.
  The benchmark reports both churn and live heap so regressions stay visible.
- **Unicode line separators.** Only CR, LF, and CRLF delimit SSE frames.
  U+2028/U+2029 inside JSON are ordinary characters; randomized golden
  streams pin this, and a generator bug that violated it was caught by the
  Go tests during development.
- **Semantic drift.** Python stays the specification. Golden data is
  regenerated from Python and committed; Go cannot drift silently.

## State of the Python implementation

The rewrite does not start from a slow Python baseline. The profiling-led
pass on the current tree, covered by the existing 9,794-test suite
(9,794 passed, 10 skipped after the changes), landed:

- module-level compiled SSE regexes instead of per-call pattern strings;
- a bounded FIFO payload cache cutting per-frame JSON parses from three to
  one, with deep-copy-before-mutation preserved at every call site;
- framing-only outcome middleware that performs zero JSON decoding on
  healthy streams, with a C-level substring rejection before any splitting;
- an LF-only split fast path in the SSE decoder, checked byte for byte
  against the general scanner by randomized property tests;
- a monotonic byte-size estimator replacing per-frame `simplejson`
  serialization in retention accounting;
- hoisted frozenset constants replacing per-frame literal allocation in the
  delivery state machine.

These changes keep the Python product correct and fast while the Go core
matures, and they define the exact semantics every Go package must
reproduce.
