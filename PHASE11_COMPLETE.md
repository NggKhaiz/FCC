# FCC Phase 11 — Peer scrape · OM protobuf · Signed bundles

**Branch:** `arena/01a095da-fcc`  
**Date:** 2026-09-12

## Flower restatement (EN + XML)

```xml
<prompt id="continue-next">
  <en>
    Continue Next: Flower phase 15 after Phase 10.
    plan → stats → build → pentest → debug → ship.
    Active peer scrape for fan-in; OpenMetrics protobuf-lite;
    signed audit bundles (HMAC-SHA256).
  </en>
</prompt>
```

## Shipped

| Item | Detail |
|------|--------|
| **Peer scrape** | `POST /admin/api/console/fanin/scrape` — hub pulls peers via httpx |
| **SSRF gate** | `peer_scrape.normalize_peer_url` — scheme/path/allowlist/metadata block |
| **Allowlist** | `FCC_FANIN_PEER_ALLOWLIST` (comma hosts / host:port) |
| **OM protobuf** | `GET /admin/api/metrics/openmetrics.pb` — FCCOM1 binary |
| **Signed ZIP** | Audit bundle HMAC when `FCC_AUDIT_SIGNING_KEY` set |
| **Verify** | `POST /admin/api/audit/bundle/verify` (multipart `bundle`) |
| **UI** | ⌘K scrape peers · Open OM protobuf · signed bundle download |
| **Modules** | `peer_scrape.py`, `openmetrics_protobuf.py`, `audit_sign.py` |

## Env

```bash
# Optional: restrict which hosts the hub may scrape
FCC_FANIN_PEER_ALLOWLIST=replica-a,replica-b.internal:8082

# Optional: HMAC key for audit ZIP signatures (min ~16 chars)
FCC_AUDIT_SIGNING_KEY=change-me-long-random-secret
```

## Scrape

```bash
curl -H "X-FCC-Admin-Token: $TOK" -H 'Content-Type: application/json' \
  -d '{"peers":["http://replica-a:8082","http://replica-b:8082"]}' \
  http://hub:8082/admin/api/console/fanin/scrape
```

## Signed bundle

```bash
curl -H "X-FCC-Admin-Token: $TOK" -OJ http://hub:8082/admin/api/audit/bundle
# X-FCC-Audit-Signed: 1 when key configured

curl -H "X-FCC-Admin-Token: $TOK" \
  -F bundle=@fcc-audit-….zip \
  http://hub:8082/admin/api/audit/bundle/verify
```

Signature covers **canonical content digest** (sorted member sha256 lines), so
`signature.hmac.json` can be embedded without invalidating the HMAC.

## Security

- Scrape: http/https only, no URL credentials, path under `/admin/api/`, no redirects
- Metadata IPs blocked; allowlist when set
- Max 8 peers / 3s timeout / no follow redirects
- Signing key never appears in bundle; only `key_id` fingerprint
- Verify uses `hmac.compare_digest`

## Tests

`tests/api/test_phase11_scrape_sign.py`
