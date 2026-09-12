# FCC Ultra Core Benchmark Results

- backend: `python`
- generated: 2026-09-12 14:32:24 UTC

| op | avg ns | p50 ns | p99 ns | ops/sec |
|----|-------:|-------:|-------:|--------:|
| `fnv1a64` | 1962.6 | 1819 | 4055 | 509,534 |
| `validate_provider` | 1057.0 | 643 | 13674 | 946,057 |
| `validate_model` | 2419.5 | 1687 | 18599 | 413,311 |
| `normalize_path` | 911.2 | 873 | 1053 | 1,097,429 |
| `estimate_tokens_64` | 5307.2 | 5135 | 6835 | 188,424 |
| `bloom_contains` | 5718.9 | 4275 | 13483 | 174,859 |
| `sliding_allow` | 620.3 | 576 | 1659 | 1,612,116 |

Target: validators & bloom negative checks typically well under a few µs on CPython; Rust wheel (when built) should be faster still.
