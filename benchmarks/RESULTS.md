# FCC Ultra Core Benchmark Results

- backend: `python`
- generated: 2026-09-12 15:53:45 UTC

| op | avg ns | p50 ns | p99 ns | ops/sec |
|----|-------:|-------:|-------:|--------:|
| `fnv1a64` | 1899.6 | 1830 | 3115 | 526,426 |
| `validate_provider` | 669.3 | 640 | 1067 | 1,494,114 |
| `validate_model` | 1781.0 | 1629 | 3747 | 561,482 |
| `normalize_path` | 881.1 | 840 | 1452 | 1,134,944 |
| `estimate_tokens_64` | 5281.2 | 5001 | 9851 | 189,349 |
| `bloom_contains` | 4507.6 | 4330 | 7960 | 221,849 |
| `sliding_allow` | 620.8 | 569 | 1734 | 1,610,829 |
| `sha256_hex_64` | 811.0 | 784 | 852 | 1,233,109 |
| `hmac_sha256_hex` | 2327.1 | 2124 | 4947 | 429,717 |
| `peer_url_ok` | 869.8 | 808 | 1626 | 1,149,752 |

Target: validators & bloom negative checks typically well under a few µs on CPython; Rust wheel (when built) should be faster still.
