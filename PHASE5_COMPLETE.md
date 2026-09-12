# FCC Phase 5 — Hardening + JSON Ultra + Live Tail + Helm

**Branch:** `arena/01a095da-fcc`  
**Date:** 2026-09-12  
**Skill ritual:** read `skills/Fl0w3r_RHLZ_V3r$!0n_03.xml` · EN restatement below

---

## User prompt restated (EN + XML intent)

```xml
<prompt>
  <en>You have full access. Continue coding. Self-plan, self-stats, self-build,
  self-pentest, self-debug, loop. Peak quality and stability. Prefer Rust-ready
  ultra cores. Every chat: Flower skill + XML/EN restatement.</en>
  <goal>Phase 5 production hardening beyond Phase 4 ultra-core</goal>
</prompt>
```

---

## Plan executed

| # | Item | Status |
|---|------|--------|
| 1 | Dedicated Admin API token (`FCC_ADMIN_API_TOKEN`) | done |
| 2 | Brute-force auth via native `SlidingWindow` | done |
| 3 | Security event ring + `/admin/api/security/events` + UI tail | done |
| 4 | JSON compact helpers on Responses tool path | done |
| 5 | Helm chart under `deploy/helm/fcc` | done |
| 6 | Tests `tests/api/test_phase5_hardening.py` | done |
| 7 | Docs + ship | done |

---

## Details

### Admin API token
- Setting: `FCC_ADMIN_API_TOKEN` (nullable secret)
- Manifest field under Runtime (advanced)
- Headers: `X-FCC-Admin-Token` **or** `Authorization: Bearer …`
- Empty = disabled (IP allowlist / local-only remain)
- Enforced on config/metrics/export/import/test/audit/events/health

### Auth brute-force
- `dependencies.py` now uses `SlidingWindow` (`authfail:{ip}`)
- 10 fails / 300s → block 600s (same policy, cleaner implementation)

### Live security tail
- `SecurityEventRing` in `native/ultra.py`
- `log_security_event` appends to ring
- Admin Security view shows newest events + refresh

### JSON ultra
- `json_dumps_compact` / `json_loads_object` / `json_loads_any`
- Wired into `core/openai_responses/tools.py` parse/normalize paths

### Helm
```
deploy/helm/fcc/
  Chart.yaml values.yaml README.md
  templates/{deployment,service,secret,pvc,_helpers}
```

---

## Pentest delta

| Test | Result |
|------|--------|
| Admin API without token when configured | 401 |
| Admin API with Bearer / X-FCC-Admin-Token | 200 |
| Security events endpoint | 200 + ring data |
| JSON object-only parse rejects arrays | ValueError |
| Brute-force window block/clear | OK |

---

## How to enable admin token

```bash
export FCC_ADMIN_API_TOKEN="$(openssl rand -hex 24)"
# Admin UI fetch will need the header once you wire it into admin.js api()
# For API clients:
curl -H "X-FCC-Admin-Token: $FCC_ADMIN_API_TOKEN" http://127.0.0.1:8082/admin/api/metrics
```

> Note: Browser Admin UI still works without the header when token is **empty**.
> When you set the token for production API hardening, pair with IP allowlist
> and/or reverse-proxy auth for the HTML shell.
