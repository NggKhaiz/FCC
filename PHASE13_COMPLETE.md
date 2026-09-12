# FCC Phase 13 — Mesh pull · Ed25519 fast-path · fcc_core packaging

**Branch:** `arena/01a095da-fcc`  
**Date:** 2026-09-12

## Flower restatement (EN + XML)

```xml
<prompt id="continue-next">
  <en>
    Continue Next: Flower phase 17 after Phase 12.
    plan → stats → build → pentest → debug → ship.
    Publish fcc_core wheels on CI; active mesh pull between hubs;
    optional cryptography fast-path for Ed25519.
  </en>
</prompt>
```

## Shipped

| Item | Detail |
|------|--------|
| **Mesh pull** | `POST /admin/api/console/mesh/pull` — pull registered hubs (+ optional hubs[]) |
| **Ingest** | Security-events export → local fan-in hub; refresh mesh registry |
| **SSRF** | Same `normalize_peer_url` + mTLS client kwargs as fan-in scrape |
| **Ed25519 fast** | `native/ed25519_fast.py` — `cryptography` if installed, else pure |
| **audit_sign** | Uses fast module; `ed25519_backend()` diagnostic |
| **Native status** | `GET /admin/api/native/status` |
| **Packaging** | `scripts/package_fcc_core.sh` + enhanced `native-core.ci.yml` (py3.11/3.12 matrix) |
| **Optional deps** | `pip install free-claude-code[fcc-crypto]` → cryptography |
| **UI** | ⌘K Pull mesh hubs · Native backend status |
| **Tests** | `tests/api/test_phase13_mesh_pull.py` |

## Mesh pull

```bash
# register peers first
curl -H "X-FCC-Admin-Token: $TOK" -H 'Content-Type: application/json' \
  -d '{"hub_id":"hub-b","base_url":"http://hub-b:8082"}' \
  http://hub-a:8082/admin/api/console/mesh/register

# active pull (uses registered base_urls)
curl -H "X-FCC-Admin-Token: $TOK" -H 'Content-Type: application/json' \
  -d '{}' http://hub-a:8082/admin/api/console/mesh/pull

# or explicit
curl -H "X-FCC-Admin-Token: $TOK" -H 'Content-Type: application/json' \
  -d '{"hubs":["http://hub-b:8082","http://hub-c:8082"]}' \
  http://hub-a:8082/admin/api/console/mesh/pull
```

## Ed25519 fast-path

```bash
# optional speed-up (OpenSSL-backed)
pip install 'cryptography>=42'
# or: pip install free-claude-code[fcc-crypto]

python -c "from free_claude_code.native.ed25519_fast import backend; print(backend())"
# cryptography | pure
```

## fcc_core packaging

```bash
# local (no-op without rustc)
./scripts/package_fcc_core.sh
# → dist/fcc_core/*.whl or README stub

# with toolchain
FCC_CORE_PACKAGE=1 ./scripts/build_native.sh
maturin build --release -m crates/fcc_core/Cargo.toml --out dist/fcc_core

# CI: copy scripts/native-core.ci.yml → .github/workflows/native-core.yml
# when GitHub App has workflows permission
```

## Security

- Mesh pull: no redirects, 3s timeout, admin path only, allowlist/mTLS honored
- Ed25519 API identical across backends (seed 32 bytes, 64-byte sig)
- Packaging scripts never require Rust at runtime

## Tests

`tests/api/test_phase13_mesh_pull.py`
