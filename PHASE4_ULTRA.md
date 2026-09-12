# FCC Phase 4 — Ultra Core (Rust-ready) + Flower Loop

**Branch:** `arena/01a095da-fcc`  
**Date:** 2026-09-12  
**Skill:** `skills/Fl0w3r_RHLZ_V3r$!0n_03.xml` + `skills/FLOWER_PROMPT_EN.md`

---

## User prompt restated (EN)

> Keep optimizing to peak quality. Self-drive **plan → stats → build → pentest →
> debug → loop**. Stability first. Prefer **Rust** for max performance. On every
> chat: read Flower skill, recreate the prompt as **XML + English**, then execute.

*(Original attach `Fl0w3r_RHLZ_V3r$!0n_02.xml` was not mountable in the sandbox;
v03 reconstructs and upgrades the skill in-repo.)*

---

## Stats (pre-Phase-4 baseline)

| Metric | Value |
|--------|------:|
| Python LOC (src) | ~60,453 |
| Largest modules | openai_chat provider 1861, code_sessions 1423, admission 942 |
| Prior phases | P1 remote admin · P2 security · P3 metrics/theme/deploy |
| Rust toolchain in sandbox | unavailable (SSL to rustup blocked) |
| Native backend at runtime | `python` ultra (Rust drop-in when built) |

---

## What shipped

### 1. Flower skill (every-chat ritual)
- `skills/Fl0w3r_RHLZ_V3r$!0n_03.xml` — full 8-phase plan, constraints, anti-patterns
- `skills/FLOWER_PROMPT_EN.md` — English companion

### 2. Python ultra-core (`free_claude_code.native`)
| API | Role |
|-----|------|
| `BloomFilter` | Negative cache, no false negatives |
| `TtlLruCache` | Thread-safe LRU + TTL |
| `SlidingWindow` | Rate-limit backend |
| `validate_*_fast` | provider / model / session without regex |
| `normalize_path_key` | Metrics path collapse |
| `estimate_tokens_fast` | Stream-chunk token approx (CJK aware) |
| `sanitize_log_fast` | Log injection hardening |
| `is_safe_asset_name` | Admin static allowlist helper |
| `fnv1a64` | Stable fast hash |

Import path prefers Rust `fcc_core` when installed; otherwise pure Python.

### 3. Rust crate `crates/fcc_core`
- PyO3-ready (`maturin`), same API surface
- `scripts/build_native.sh` no-ops cleanly without rustc
- Unit tests in `lib.rs`

### 4. Hot-path wiring
- `api/security.py` → native validators + sanitize
- `api/rate_limit.py` → `SlidingWindow`
- `api/metrics.py` → `normalize_path_key`
- `core/token_estimation.py` → fast path ≤512 chars
- `admin_routes` asset names → `is_safe_asset_name`
- `code_sessions_routes` session/op ids → `validate_session_id`

### 5. UI paint
- `content-visibility` / `contain` on provider grid, metrics, settings sections

### 6. Bench + tests
- `benchmarks/bench_ultra.py` + `benchmarks/RESULTS.md`
- `tests/api/test_native_ultra.py`

---

## Benchmark snapshot (this sandbox, CPython 3.11, backend=python)

| op | avg ns | ops/sec |
|----|-------:|--------:|
| sliding_allow | ~620 | ~1.6M |
| normalize_path | ~910 | ~1.1M |
| validate_provider | ~1.1µs | ~0.95M |
| validate_model | ~2.4µs | ~0.41M |
| bloom_contains | ~5.7µs | ~0.17M |
| estimate_tokens_64 | ~5.3µs | ~0.19M |

*(Rust wheel expected faster; not built here due to toolchain/network.)*

---

## Pentest notes (Phase 4 delta)

| Attack | Result |
|--------|--------|
| Provider id `../etc/passwd` | rejected (fast validator) |
| Session id traversal | rejected |
| Asset `../` / separators | rejected via `is_safe_asset_name` |
| Log injection `\n\r\0` | stripped |
| Rate limit exceed | blocks + retry_after |
| Bloom false negative | none (2000 inserts verified) |
| Secret export | unchanged (Phase 3 still omits secrets) |

No intentional weakening of auth, CSP, or rate limits.

---

## Build Rust later

```bash
# machine with rustc + maturin
./scripts/build_native.sh
# or
maturin develop --release -m crates/fcc_core/Cargo.toml
python -c "from free_claude_code.native import backend; print(backend())"  # rust
```

---

## Loop status

| Phase | Status |
|-------|--------|
| 1 Recon+Stats | done |
| 2 Flower skill | done |
| 3 Python ultra | done |
| 4 Rust crate | source done · wheel pending toolchain |
| 5 Wire hot paths | done |
| 6 UI paint | done |
| 7 Pentest+bench | done (smoke + microbench) |
| 8 Ship | commit + push + PR update |
