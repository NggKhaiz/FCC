# gocore

Go implementation of the FCC streaming hot path, ported byte-for-byte from
the Python reference. Standard library only; no external modules are
required to build or test it.

## Layout

| Package | Python reference | Responsibility |
| --- | --- | --- |
| `sse` | `core/anthropic/streaming/decoder.py`, `core/anthropic/stream_contracts.py`, `api/stream_frames.py` | Incremental SSE framing, event naming, failure scanning, payload cache |
| `retention` | `core/delivered_response.py` (`_json_byte_size`, `_retain`) | Monotonic JSON byte estimation and the retention budget |
| `cmd/bench` | `bench_local/bench_golden.py` | Hot-path benchmark on the shared fixture |

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

Measured on a two-core Xeon at 2.60 GHz: Go processes the fixture in about
5.4 ms per request (roughly 186 req/s) against 83 ms (12 req/s) for the
optimized Python reference, with a 348 KiB live heap after GC. See
`docs/rewrite/01-language-verdict.md` for the full language decision and
`docs/rewrite/02-architecture-and-migration.md` for the migration plan.

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
