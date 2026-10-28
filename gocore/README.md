# gocore

Go implementation of the FCC streaming hot path, ported byte-for-byte from
the Python reference. Standard library only; no external modules are
required to build or test it.

## Layout

| Package | Python reference | Responsibility |
| --- | --- | --- |
| `sse` | `core/anthropic/streaming/decoder.py`, `core/anthropic/stream_contracts.py`, `api/stream_frames.py` | Incremental SSE framing, event naming, failure scanning, payload cache, frame rewriting |
| `retention` | `core/delivered_response.py` (`_json_byte_size`, `_retain`) | Monotonic JSON byte estimation and the retention budget |
| `recovery` | `core/stream_recovery.py` (`assess_recovery`) | Stop/restart/continue/handoff decision table |
| `delivery` | `core/delivered_response.py` | Per-request evidence state machine (messages wire), snapshots, handoff and closing events |
| `toolbuf` | `api/tool_call_buffer.py` | Withholding and atomic release of client tool-call groups, responses snapshot filtering |
| `cmd/bench` | `bench_local/bench_golden.py` | Hot-path benchmark on the shared fixture |

Phase 1 (`sse`, `retention`) is covered by `testdata/golden.json`; phase 2
(`recovery`, `delivery`, `toolbuf`) by `testdata/golden_phase2.json`,
generated with `bench_local/generate_golden_phase2.py`. The phase-2
fixture pins event-by-event delivery state (progress, retention limit
behavior, unsafe reasons, snapshots, closing and handoff events) and the
exact frame release timing of the tool-call buffer.

## Parity workflow

The Python implementation is the specification. Golden test data is
generated from it and committed under `testdata/`:

```bash
# from the repository root, with the project virtualenv active
.venv/bin/python bench_local/generate_golden.py
```

The Go tests fail on any drift in frame bytes, event names, failure
classifications, decimal preservation, or retention sizes:

```bash
go test ./...
go vet ./...
gofmt -l .
```

Regenerate the fixture whenever the Python reference changes. The framing
cases include 120 randomized CR/CRLF/LF streams split into one- to
eight-byte wire chunks, and both the fast path and the general scanner are
asserted against the same expected output.

## Benchmark

Both commands run the same pipeline over the same 2,005-frame fixture
(`testdata/stream.txt`) for 100 iterations:

```bash
# Python reference
.venv/bin/python bench_local/bench_golden.py

# Go implementation
go run ./cmd/bench
go test -bench=HotPath -benchmem ./sse/
```

Measured on a two-core Xeon at 2.60 GHz. Phase-1 pipeline (framing, event
naming, failure scan, retention): Go about 5.4 ms per request (~186 req/s)
against 83 ms (~12 req/s) in optimized Python, 348 KiB live heap after GC.
The phase-2 pipeline adds the full delivery state machine and tool-call
buffer: Go about 6.5-7.2 ms (~140-155 req/s) against 95-100 ms (~10 req/s),
roughly a 15x end-to-end component speedup with a ~350 KiB live heap versus
a 1.7 MiB Python peak. See `docs/rewrite/01-language-verdict.md` for the
full language decision and `docs/rewrite/00-master-plan.md` for the
migration plan.

## Porting invariants

- Only CR, LF, and CRLF delimit SSE frames; Unicode line and paragraph
  separators inside JSON are ordinary characters.
- Numbers stay on the wire as `json.Number`; no float round-trip.
- Cached payloads are read-only. Mutations copy first, and edited frames
  re-serialize to new strings that miss the cache.
- Retention estimates are conservative: retention may trigger earlier than
  an exact serialization would, never later.
- Every fast path has a general fallback, and both are covered by the same
  golden cases.
