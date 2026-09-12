# FCC Phase 7 — Live latency · SSE audit · PWA (A→Z)

**Branch:** `arena/01a095da-fcc`  
**Date:** 2026-09-12

## Flower restatement (EN + XML)

```xml
<prompt id="loop-a-z">
  <en>
    Loop Start A-Z: full Flower cycle end-to-end.
    plan → stats → build → pentest → debug → ship.
    Peak quality, stability first, Rust-ready ultra cores.
    Every chat: read skill, restate XML+EN, execute.
  </en>
</prompt>
```

## A→Z checklist

| Step | Action | Result |
|------|--------|--------|
| A | Read Flower skill | v03 phases 1–11 |
| B | Restate prompt EN+XML | above |
| C | Stats | P3–P6 on PR; gap = live provider latency, SSE, PWA |
| D | Plan Phase 7 | live latency + SSE tail + PWA shell |
| E | Build execution instrumentation | `ProviderExecutor` finally → `record_provider_latency` |
| F | Build SSE | `GET /admin/api/security/events/stream` |
| G | Build PWA | `manifest.webmanifest` + `sw.js` + register |
| H | Wire UI | EventSource live tail; SW register |
| I | Tests | `tests/api/test_phase7_live.py` |
| J | Compile / node check | py_compile + node --check |
| K | Docs | this file + FINAL/ANALYSIS/skill |
| L–Y | (reserved future loops) | |
| Z | Ship | commit + push + PR comment |

## Details

### Live provider latency
- `application/execution.py` `_stream_candidates` records per-candidate duration
  on **every** messages/responses stream attempt (success or fail).
- Metrics view provider bars now reflect real proxy turns, not only Test All.

### SSE security tail
- `GET /admin/api/security/events/stream` — `text/event-stream`
- Events: `snapshot`, `events`, keepalive comments
- Admin UI uses `EventSource` when **no** session admin token is set
  (EventSource cannot send `X-FCC-Admin-Token`; poll remains fallback)

### PWA
- `manifest.webmanifest` (standalone Admin)
- `sw.js` cache-first for `/admin/assets/*` only — **never** caches `/admin/api/*`
- Registered from `admin.js`

## Security notes
- SW does not cache APIs or SSE
- SSE still behind loopback/IP + optional admin token (header mode → poll)
- Provider latency stores provider_id only (no payloads/secrets)

## Verify

```bash
PYTHONPATH=src python3 -c "from free_claude_code.api.metrics import metrics; metrics.record_provider_latency('x', latency_ms=1); print(metrics.snapshot()['provider_latency'])"
# Admin → Security view: live tail without admin token
# Admin → Metrics: provider bars after traffic
```
