# fcc_core (Rust)

Native acceleration for Free Claude Code hot paths.

## Build (when Rust toolchain is available)

```bash
# from repo root
pip install maturin
maturin develop --release -m crates/fcc_core/Cargo.toml
# or wheel:
maturin build --release -m crates/fcc_core/Cargo.toml
```

## API surface (mirrors `free_claude_code.native.ultra`)

- `fnv1a64(data) -> int`
- `validate_provider_id_fast(s) -> bool`
- `validate_model_ref_fast(s) -> bool`
- `validate_session_id_fast(s) -> bool`
- `is_safe_asset_name(s) -> bool`
- `normalize_path_key(s) -> str`
- `estimate_tokens_fast(s) -> int`
- `sanitize_log_fast(s, max_length=200) -> str`
- `BloomFilter(capacity, error_rate)`
- `SlidingWindow`

Python always falls back to `free_claude_code.native.ultra` if this module is absent.
