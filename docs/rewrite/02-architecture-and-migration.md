# Rewrite architecture and migration plan

## Scope and starting point

The Python service is roughly 75,000 lines of application code across 423
modules with 149,000 lines of tests. It is a local developer tool, not a
single-purpose server: a streaming proxy, sixty-one provider integrations,
twelve agent launchers, an admin web UI, SQLite persistence, Telegram and
Discord bridges, desktop tray integration, and optional local voice
transcription.

A big-bang rewrite would discard the behavioral contracts encoded in the
test suite and leave no runnable product for months. The rewrite therefore
runs as a strangler-fig migration behind the existing Python process, with
each Go module proven against golden data produced by the Python reference
before any traffic moves.

## What moves to Go first

Profiling places almost all per-frame CPU cost in four tightly scoped
subsystems. They have small interfaces, no I/O of their own, and exhaustive
existing tests, which makes them ideal first candidates.

| Python module | Responsibility | Go package |
| --- | --- | --- |
| `core/anthropic/streaming/decoder.py` | Incremental SSE framing | `gocore/sse` (`framer.go`) |
| `core/anthropic/stream_contracts.py` | Event naming, failure scanning | `gocore/sse` (`event.go`) |
| `api/stream_frames.py` | Data extraction, payload cache | `gocore/sse` (`event.go`, `cache.go`) |
| `core/delivered_response.py` | Retention byte accounting | `gocore/retention` |

The Go packages depend only on the standard library, so the core can be
vendored into any later deployment shape (embedded sidecar, separate
service, or cgo-free shared library) without an ecosystem commitment.

## Parity mechanism

`bench_local/generate_golden.py` exercises the Python reference and writes
`gocore/testdata/golden.json`:

- 120 randomized framing streams with every CR/CRLF/LF combination and
  chunk widths from one to eight bytes;
- 2,019 real frames with expected event names, failure reasons, and data
  payloads, including a decimal value that must survive as an unrounded
  number;
- retention estimates for nested objects, empty containers, booleans, null,
  and multi-byte text.

The Go tests load this fixture and assert byte-identical frame strings,
identical event and failure classifications, and identical retention sizes.
The fixture is regenerated whenever the Python reference changes, so the two
implementations cannot silently drift. Every later ported subsystem gets the
same treatment: Python remains the specification, Go proves conformance.

## Target module layout

```
gocore/
  go.mod
  sse/         framing, event classification, payload cache
  retention/   bounded byte accounting
  wire/        provider wire protocols (messages, responses, gemini)
  recovery/    prefix snapshots, continuation overlap, retry state
  catalog/     model and provider metadata
  transport/   upstream HTTP, admission, failure policy
  store/       SQLite-backed sessions and messaging
  testdata/    golden fixtures generated from Python
  cmd/
    bench/     hot-path benchmark against the shared fixture
    fcc-core/  long-term standalone service entry point
```

The package boundaries mirror the Python package tree so a reviewer can hold
the two implementations side by side.

## Migration phases

1. **Hot-path core (current).** SSE, event classification, payload cache,
   retention. Pure logic, golden parity tests, benchmarked against Python on
   an identical 2,005-frame fixture.
2. **Delivery state machine.** Port `DeliveredResponse`, `ToolCallBuffer`,
   and stream recovery as pure Go state machines with golden snapshots per
   event. At this point a single request can be processed end to end inside
   the Go core while Python still owns HTTP.
3. **Transport sidecar.** Expose the Go core over a local Unix socket or
   stdio RPC. Python delegates frame processing per request behind a feature
   flag; a fallback path keeps the pure-Python implementation available.
4. **Provider adapters.** Move providers in groups, starting with the
   highest-traffic OpenAI-compatible family. The provider contracts are
   already normalized in Python, so each adapter is a mechanical port with
   recorded fixtures.
5. **Service surface.** HTTP server, admin API, and the static admin UI move
   behind the Go binary. The UI itself stays vanilla HTML/CSS/JS; Go serves
   the existing files unchanged.
6. **Outer edges.** CLI launchers become thin wrappers around the single
   binary. Telegram/Discord bridges port to established Go libraries.
   Desktop tray and native dialogs move last. Local voice transcription
   stays a Python sidecar: the torch/whisper ecosystem has no Go equivalent
   and is an optional, non-hot-path feature.

## Decisions that must not change during the port

- SSE line splitting recognizes only CR, LF, and CRLF; Unicode line and
  paragraph separators inside JSON text are never treated as boundaries.
- Numeric payloads retain their exact wire representation. Go decodes with
  `json.Number`; no float round-trip is allowed on ledger or hashing paths.
- Cached payloads are read-only shared state. Any mutation starts from a
  deep copy, and edited frames re-serialize to new strings that naturally
  miss the cache.
- Retention accounting stays monotonic and conservative: it may trigger
  earlier than an exact serialization, never later.
- The fast paths are optimization-only. Each has a general fallback, and
  tests pin both paths against the same golden output.
