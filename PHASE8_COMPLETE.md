# FCC Phase 8 — Cookie bridge · Metrics SSE · Federation

**Branch:** `arena/01a095da-fcc`  
**Date:** 2026-09-12

## Flower restatement (EN + XML)

```xml
<prompt id="continue-next">
  <en>
    Continue Next: next Flower loop after Phase 7.
    plan → stats → build → pentest → debug → ship.
    Close phase-12 gaps: EventSource admin-token cookie bridge,
    multi-node metrics federation, live metrics stream.
  </en>
</prompt>
```

## Shipped

| Item | Detail |
|------|--------|
| **Cookie bridge** | `POST/DELETE /admin/api/session/token` sets HttpOnly `fcc_admin_token` (SameSite=Strict, path=/admin) |
| **Auth** | `require_admin_token` accepts cookie in addition to Bearer / `X-FCC-Admin-Token` |
| **SSE + token** | Security EventSource works when admin token is set (via cookie) |
| **Metrics SSE** | `GET /admin/api/metrics/stream` — live slim snapshots every 2s |
| **Federation export** | `GET /admin/api/metrics/export` — portable node snapshot (`FCC_NODE_ID`) |
| **Federation merge** | `POST /admin/api/metrics/merge` + pure `metrics_federation.py` |
| **UI** | Token prompt syncs cookie; Metrics uses EventSource; ⌘K export metrics |

## Security

- Cookie: HttpOnly, SameSite=Strict, path scoped to `/admin`, Secure on HTTPS
- Setting cookie validates against `FCC_ADMIN_API_TOKEN` when configured
- No token in query string (leak risk avoided)
- Federation payloads contain counters only — no secrets/payloads
- Merge is pure (does not mutate local process metrics)

## Multi-node sketch

```bash
# on each node
curl -H "X-FCC-Admin-Token: $TOK" http://node1:8082/admin/api/metrics/export > n1.json
curl -H "X-FCC-Admin-Token: $TOK" http://node2:8082/admin/api/metrics/export > n2.json

# merge on any admin
curl -H "X-FCC-Admin-Token: $TOK" -H 'Content-Type: application/json' \
  -d "{\"nodes\":[$(cat n1.json),$(cat n2.json)]}" \
  http://admin:8082/admin/api/metrics/merge
```

Or UI: ⌘K → **Export metrics federation**.

## Tests

`tests/api/test_phase8_federation.py`
