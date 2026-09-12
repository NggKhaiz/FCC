# FCC Helm chart

```bash
# Build image first (see deploy/Dockerfile)
docker build -t fcc:local -f deploy/Dockerfile .

helm upgrade --install fcc deploy/helm/fcc \
  --set image.repository=fcc \
  --set image.tag=local \
  --set secrets.PROXY_AUTH_TOKEN="$(openssl rand -hex 24)" \
  --set secrets.FCC_ADMIN_API_TOKEN="$(openssl rand -hex 24)"
```

Expose via Ingress or `kubectl port-forward svc/fcc 8082:8082`.
