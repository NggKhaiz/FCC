# FCC Phase 14 — Per-hub tokens · Mesh sync · fcc-core PyPI

**Branch:** `arena/01a095da-fcc`  
**Date:** 2026-09-13

## Flower restatement (EN + XML)

```xml
<prompt id="continue-next">
  <en>
    Continue Next: Flower phase 18 after Phase 13.
    plan → stats → build → pentest → debug → ship.
    PyPI optional fcc-core package; mesh auth tokens per-hub;
    continuous mesh sync scheduler.
  </en>
</prompt>
```

## Shipped

| Item | Detail |
|------|--------|
| **Per-hub tokens** | `POST /admin/api/console/mesh/token` + `token` on register |
| **Token privacy** | Snapshot exposes `token_set` only — never the secret |
| **Pull uses tokens** | Mesh pull / scheduler prefer per-hub token over global |
| **Sync scheduler** | `GET/POST …/mesh/sync[/start|/stop|/once]` |
| **Autostart** | `FCC_MESH_SYNC_AUTO=1` + `FCC_MESH_SYNC_INTERVAL` via app lifespan |
| **PyPI packaging** | `fcc-core` metadata classifiers + `scripts/publish_fcc_core.sh` (gated) |
| **UI** | ⌘K start/stop/once sync · set hub token |
| **Tests** | `tests/api/test_phase14_mesh_sync.py` |

## Per-hub token

```bash
# register with token
curl -H "X-FCC-Admin-Token: $TOK" -H 'Content-Type: application/json' \
  -d '{"hub_id":"hub-b","base_url":"http://hub-b:8082","token":"peer-secret"}' \
  http://hub-a:8082/admin/api/console/mesh/register

# or set later
curl -H "X-FCC-Admin-Token: $TOK" -H 'Content-Type: application/json' \
  -d '{"hub_id":"hub-b","token":"peer-secret"}' \
  http://hub-a:8082/admin/api/console/mesh/token
```

## Continuous sync

```bash
# start (min interval 15s)
curl -H "X-FCC-Admin-Token: $TOK" -d '{"interval_seconds":60}' \
  http://hub-a:8082/admin/api/console/mesh/sync/start

curl -H "X-FCC-Admin-Token: $TOK" http://hub-a:8082/admin/api/console/mesh/sync
curl -H "X-FCC-Admin-Token: $TOK" -d '{}' \
  http://hub-a:8082/admin/api/console/mesh/sync/once
curl -H "X-FCC-Admin-Token: $TOK" -d '{}' \
  http://hub-a:8082/admin/api/console/mesh/sync/stop

# autostart on process boot
export FCC_MESH_SYNC_AUTO=1
export FCC_MESH_SYNC_INTERVAL=60
```

## Publish fcc-core (optional, gated)

```bash
./scripts/package_fcc_core.sh
# safety gate — will not upload unless:
FCC_CORE_PUBLISH=1 FCC_CORE_REPOSITORY=testpypi \
  TWINE_USERNAME=__token__ TWINE_PASSWORD=pypi-... \
  ./scripts/publish_fcc_core.sh
# production requires FCC_CORE_REPOSITORY=pypi explicitly
```

## Security

- Tokens never appear in mesh snapshot / audit logs (only `token_set`)
- Scheduler uses same SSRF + mTLS gates as scrape/pull
- Publish script defaults to refuse; TestPyPI default over PyPI
- Interval clamped 15s–3600s

## Tests

`tests/api/test_phase14_mesh_sync.py`
