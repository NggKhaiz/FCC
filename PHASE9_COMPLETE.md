# FCC Phase 9 — WebSocket console · Prometheus export

**Branch:** `arena/01a095da-fcc`  
**Date:** 2026-09-12

## Flower restatement (EN + XML)

```xml
<prompt id="continue-next">
  <en>
    Continue Next: Flower phase 13 after Phase 8.
    plan → stats → build → pentest → debug → ship.
    Bidirectional WebSocket admin console;
    scrape-compatible Prometheus text exposition.
  </en>
</prompt>
```

## Shipped

| Item | Detail |
|------|--------|
| **Prometheus** | `GET /admin/api/metrics/prometheus` — text exposition 0.0.4 |
| **Renderer** | Pure `prometheus_export.render_prometheus_text` (label-escaped) |
| **WebSocket** | `WS /admin/api/console/ws` — auth, subscribe, ping, live push |
| **Protocol** | Pure `admin_console.py` (ops: auth, ping, subscribe, unsubscribe, help) |
| **Channels** | `security`, `metrics`, `system` |
| **UI** | Console view — connect/ping/command line, live log |
| **Audit flags** | `prometheus_export`, `admin_console_ws` on security audit |
| **⌘K** | Open Prometheus metrics · Open console |

## Protocol (client → server)

```json
{"op":"auth","token":"<FCC_ADMIN_API_TOKEN>"}
{"op":"ping","nonce":"optional"}
{"op":"subscribe","channels":["security","metrics","system"]}
{"op":"unsubscribe","channels":["metrics"]}
{"op":"help"}
```

Auth is skipped when no server token is configured. Cookie `fcc_admin_token` (Phase 8) unlocks the socket without an auth frame.

## Prometheus scrape

```yaml
# prometheus.yml fragment
scrape_configs:
  - job_name: fcc
    metrics_path: /admin/api/metrics/prometheus
    static_configs:
      - targets: ["fcc:8082"]
    # Prefer network policy + admin token header via a scrape proxy;
    # or scrape from a sidecar on loopback with FCC_ADMIN_API_TOKEN unset locally.
```

```bash
curl -H "X-FCC-Admin-Token: $TOK" http://127.0.0.1:8082/admin/api/metrics/prometheus
```

## Security

- WS gated by IP allowlist + local-only flags before accept
- Token via cookie or first-frame `auth` (`compare_digest`)
- Max frame 8 KiB; channel allowlist; event field strip
- Prometheus output: counters/gauges only — no secrets/payloads
- Admin console path remains under `/admin` security boundary

## Tests

`tests/api/test_phase9_console.py`
