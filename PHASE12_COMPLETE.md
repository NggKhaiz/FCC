# FCC Phase 12 — Rust hotpath v0.2 · Ed25519 · mTLS scrape · Hub mesh

**Branch:** `arena/01a095da-fcc`  
**Date:** 2026-09-12

## Flower restatement (EN + XML)

```xml
<prompt id="continue-next-all-az">
  <en>
    Continue Next ALL A→Z. Maximize performance with Rust for hot paths.
    plan → stats → build → pentest → debug → ship.
    mTLS peer scrape; Ed25519 audit signatures; multi-hub mesh;
    expand fcc_core Rust (SHA-256/HMAC/peer gate/FCCOM1) + Python twin.
  </en>
  <vi>
    Tiếp tục A→Z. Tối đa hóa hiệu năng bằng Rust cho hot-path.
    mTLS scrape; Ed25519 ký audit; mesh multi-hub; mở rộng fcc_core.
  </vi>
</prompt>
```

## Rust status (this environment)

| Item | Status |
|------|--------|
| `crates/fcc_core` **0.2.0 source** | ✅ shipped (SHA-256, HMAC, peer_url_ok, content_digest, FCCOM1, PyO3 bindings) |
| `rustc` / maturin in sandbox | ❌ network/SSL blocked — cannot compile wheel here |
| Python ultra twin | ✅ always-on production path (`native/ultra.py`) |
| CI recipe | `scripts/native-core.ci.yml` builds wheels when runners have Rust |

```bash
# on a machine with rustc + maturin:
./scripts/build_native.sh
maturin develop --release -m crates/fcc_core/Cargo.toml
python -c "from free_claude_code.native import backend; print(backend())"  # → rust
```

## Bench (Python ultra, this host)

| name | ops/s (approx) |
|------|----------------|
| validate_provider | ~1.5M |
| sliding_allow | ~1.6M |
| sha256_hex_64 | ~1.2M |
| peer_url_ok | ~1.1M |
| hmac_sha256_hex | ~0.4M |

See `benchmarks/RESULTS.md`.

## Shipped

| Item | Detail |
|------|--------|
| **fcc_core 0.2** | In-tree SHA-256/HMAC (no third-party crypto crates), peer gate, FCCOM1, content digest |
| **Python twin** | `native/ultra.py` + `__init__._pick` for all new symbols |
| **Ed25519** | Pure-Python RFC 8032 (`ed25519_pure.py`) + audit ZIP `signature.ed25519.json` |
| **Env** | `FCC_AUDIT_ED25519_SEED` (64 hex or passphrase→sha256) |
| **mTLS scrape** | `FCC_FANIN_MTLS_{CERT,KEY,CA}` → httpx client kwargs |
| **Hub mesh** | `POST/GET /admin/api/console/mesh[/register]` |
| **Verify** | `verify_any_signed_zip` (Ed25519 then HMAC) |
| **UI** | ⌘K hub mesh; security badges for Ed25519/mTLS/mesh/hotpath v2 |
| **Tests** | `tests/api/test_phase12_rust_mesh.py` |

## Security

- Rust crypto is std-only (supply-chain minimal)
- Ed25519 seed never embedded; public key in signature file only
- mTLS optional; scrape still SSRF-gated + allowlist
- Mesh registry: max 16 hubs, 10 min stale eviction

## Next (Flower phase 17)

Compile/publish `fcc_core` wheels on CI · wire mesh active pull between hubs · optional `cryptography` fast-path for Ed25519
