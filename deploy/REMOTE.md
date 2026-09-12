# Remote Deployment Guide — Free Claude Code (FCC)

FCC admin is **remote-enabled by default**. Use this guide for VPS, Docker, and reverse-proxy setups.

## Quick start (VPS)

```bash
# Install (see README) then:
export HOST=0.0.0.0
export PORT=8082
export PROXY_AUTH_ENABLED=1
export PROXY_AUTH_TOKEN="$(openssl rand -hex 32)"
# Optional hard locks:
# export FCC_ADMIN_IP_ALLOWLIST=203.0.113.10/32
# export FCC_ADMIN_LOCAL_ONLY=0
fcc-server
```

Open `http://YOUR_IP:8082/admin`.

## Production checklist

| Setting | Recommended |
|---------|-------------|
| `HOST` | `0.0.0.0` behind a reverse proxy |
| `PROXY_AUTH_ENABLED` | `1` |
| `PROXY_AUTH_TOKEN` | long random secret |
| `FCC_ADMIN_IP_ALLOWLIST` | your admin IP / office CIDR |
| `FCC_ENABLE_DOCS` | `0` (default) |
| TLS | Terminate at Caddy / Nginx / Traefik |
| `X-Forwarded-For` | Set by reverse proxy (trusted) |

## Caddy example

```caddyfile
fcc.example.com {
  reverse_proxy 127.0.0.1:8082
}
```

## Nginx example

```nginx
server {
  listen 443 ssl http2;
  server_name fcc.example.com;

  # ssl_certificate     /etc/letsencrypt/live/fcc.example.com/fullchain.pem;
  # ssl_certificate_key /etc/letsencrypt/live/fcc.example.com/privkey.pem;

  location / {
    proxy_pass http://127.0.0.1:8082;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_http_version 1.1;
    proxy_read_timeout 3600s;
    proxy_buffering off;
  }
}
```

## Docker Compose

See `deploy/docker-compose.yml`.

```bash
cd deploy
cp ../.env.example .env   # fill secrets
docker compose up -d
```

## Admin features (Phase 3)

- **Metrics** — `/admin/metrics` (RPS, latency, recent traffic)
- **Export / Import** — non-secret config backup (secrets never exported)
- **Theme** — light/dark (persisted in `localStorage`)
- **⌘K** — command palette; `T` theme; `R` refresh; `1–7` jump views

## Security notes

- Never expose admin without auth + TLS on a public network.
- Prefer IP allowlist + reverse proxy over raw `0.0.0.0` alone.
- Rate limiting and security headers are on by default.
- Metrics are process-local (single instance); use an external scraper if you need multi-node.

## Health

- `GET /health` — liveness
- `GET /admin/api/health/detailed` — admin-authenticated detailed health + metrics summary
- `GET /admin/api/metrics` — full runtime snapshot
