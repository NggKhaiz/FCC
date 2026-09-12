# FCC Phase 10 — Fan-in · OpenMetrics · Audit bundles

**Branch:** `arena/01a095da-fcc`  
**Date:** 2026-09-12

## Flower restatement (EN + XML)

```xml
<prompt id="continue-next">
  <en>
    Continue Next: Flower phase 14 after Phase 9.
    plan → stats → build → pentest → debug → ship.
    Multi-replica console fan-in; OpenMetrics text;
    admin audit export bundles (ZIP, no secrets).
  </en>
</prompt>
```

## Shipped

| Item | Detail |
|------|--------|
| **OpenMetrics** | `GET /admin/api/metrics/openmetrics` — OM 1.0.0 text + `# EOF` |
| **Events export** | `GET /admin/api/security/events/export` — portable node batch |
| **Fan-in ingest** | `POST /admin/api/console/fanin/ingest` — hub accepts peer exports |
| **Fan-in merge** | `POST /admin/api/console/fanin/merge` — pure multi-node merge |
| **Fan-in snapshot** | `GET /admin/api/console/fanin` — roster + merged events |
| **WS channel** | `fanin` on `/admin/api/console/ws` (2s push) |
| **Audit bundle** | `GET /admin/api/audit/bundle` — ZIP (audit, events, metrics, prom, OM) |
| **UI** | ⌘K OpenMetrics + Download audit bundle; console fan-in lines |
| **Modules** | `openmetrics_export.py`, `audit_bundle.py`, `console_fanin.py` |

## Multi-replica sketch

```bash
# on each replica
curl -H "X-FCC-Admin-Token: $TOK" \
  http://replica-N:8082/admin/api/security/events/export > nN.json

# hub (any admin)
curl -H "X-FCC-Admin-Token: $TOK" -H 'Content-Type: application/json' \
  -d @n1.json http://hub:8082/admin/api/console/fanin/ingest

# or pure merge without mutating hub
curl -H "X-FCC-Admin-Token: $TOK" -H 'Content-Type: application/json' \
  -d "{\"nodes\":[$(cat n1.json),$(cat n2.json)]}" \
  http://hub:8082/admin/api/console/fanin/merge

# live: Console UI → subscribe fanin  (auto on connect)
# incident package:
curl -H "X-FCC-Admin-Token: $TOK" -OJ \
  http://hub:8082/admin/api/audit/bundle
```

## Security

- All routes: loopback/IP gate + admin token
- Event sanitize: allowlisted fields only (no tokens/payloads)
- Bundle ZIP: secrets never included; recent metrics capped
- Fan-in hub: max 32 nodes, 200 events/node, 5 min stale eviction
- OpenMetrics/Prometheus: counters only

## Tests

`tests/api/test_phase10_fanin.py`
