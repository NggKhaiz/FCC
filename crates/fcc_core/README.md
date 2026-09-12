# fcc_core (Rust) v0.2

Optional PyO3 acceleration for FCC hot paths. Pure-Rust logic always compiles;
Python bindings require the `python` feature + maturin.

## Build

```bash
# requires rustc + maturin
./scripts/build_native.sh
# or
maturin develop --release -m crates/fcc_core/Cargo.toml
python -c "from free_claude_code.native import backend; print(backend())"  # rust
```

Without rustc, `free_claude_code.native.ultra` remains the production path.

## API surface (mirrors `free_claude_code.native`)

| Symbol | Notes |
|--------|-------|
| `fnv1a64` | 64-bit FNV-1a |
| `validate_*_fast` | provider / model / session / asset |
| `normalize_path_key` | metrics path collapse |
| `estimate_tokens_fast` | CJK-aware approx |
| `sanitize_log_fast` | log redaction helper |
| `BloomFilter` / `SlidingWindow` | rate / set membership |
| `sha256_hex` / `hmac_sha256_hex` | **Phase 12** crypto hot path (std-only) |
| `key_id16` | key fingerprint |
| `peer_url_ok` | SSRF peer URL gate |
| `content_digest_hex` | audit bundle canonical digest |
| `fccom1_encode_basic` | OpenMetrics protobuf-lite |

## Design

- **No third-party Rust crypto crates** — SHA-256/HMAC implemented in-tree for
  minimal supply-chain surface and maximum portability.
- Release profile: LTO + `codegen-units=1` + strip.
- Python always falls back if the wheel is absent.
