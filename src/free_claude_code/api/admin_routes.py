"""Admin UI routes and APIs - remote-enabled with security hardening."""

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path

import httpx
from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Request,
    Response,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from loguru import logger
from pydantic import BaseModel, Field, field_validator

from free_claude_code.application.connected_accounts import (
    ConnectedAccountLoginMode,
)
from free_claude_code.application.errors import ApplicationError
from free_claude_code.application.model_metadata import ProviderModelRefreshResult
from free_claude_code.config.admin.manifest import FIELD_BY_KEY
from free_claude_code.config.model_refs import configured_chat_model_refs
from free_claude_code.config.provider_catalog import (
    PROVIDER_CATALOG,
    ProviderAuthKind,
)
from free_claude_code.core.json_types import JsonObject, JsonValue
from free_claude_code.core.version import package_version

from .admin_security import require_loopback_admin
from .dependencies import get_services, require_admin_token
from .ports import ApiServices
from .rate_limit import check_rate_limit
from .security import (
    check_request_size,
    log_security_event,
    validate_provider_id,
)

router = APIRouter()

def _enforce_admin_api_token(request: Request, services: ApiServices | None = None) -> None:
    """Enforce FCC_ADMIN_API_TOKEN when configured (settings or env)."""
    settings = None
    if services is not None:
        settings = services.requests.current_settings()
    else:
        import os
        token = os.getenv("FCC_ADMIN_API_TOKEN", "").strip()
        if not token:
            return
        from free_claude_code.config.settings import Settings
        # Build minimal settings from env without full load
        settings = Settings.model_construct(admin_api_token=token)
    require_admin_token(request, settings)



STATIC_DIR = Path(__file__).resolve().parent / "admin_static"
PACKAGE_ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets"
_ADMIN_ASSET_VERSION_PLACEHOLDER = "__FCC_VERSION__"
_ADMIN_ASSET_FILENAMES = frozenset(
    {
        "admin.css",
        "admin.js",
        "app-icon.svg",
        "code_sessions.css",
        "code_sessions.js",
        "session_layout.css",
        "session_ui.js",
        "model_combobox.js",
        "theme_boot.js",
        "manifest.webmanifest",
        "sw.js",
    }
)
LOCAL_PROVIDER_PATHS = {
    "lmstudio": "/models",
    "llamacpp": "/models",
    "ollama": "/api/tags",
}
_LOCAL_PROVIDER_CHECK_FAILURE_MESSAGE = (
    "Could not connect. Verify the URL and that the local provider is running."
)


class AdminConfigPayload(BaseModel):
    """Partial config update submitted by the admin UI."""

    values: JsonObject = Field(default_factory=dict)

    @field_validator("values")
    @classmethod
    def validate_values_size(cls, v: JsonObject) -> JsonObject:
        if len(v) > 100:
            raise ValueError("Too many config values")
        # Check key lengths
        for key in v.keys():
            if len(key) > 128:
                raise ValueError(f"Config key too long: {key[:20]}...")
        return v


class ConnectedAccountLoginPayload(BaseModel):
    """Interactive connected-account login selection."""

    mode: ConnectedAccountLoginMode | None = None

    @field_validator("mode")
    @classmethod
    def validate_mode(cls, v):
        if v is not None and v not in (ConnectedAccountLoginMode.BROWSER, ConnectedAccountLoginMode.DEVICE):
            raise ValueError("Invalid login mode")
        return v


def _asset_path(filename: str) -> Path:
    asset_dir = PACKAGE_ASSETS_DIR if filename == "app-icon.svg" else STATIC_DIR
    path = asset_dir / filename
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Admin asset not found")
    return path


def _asset_response(filename: str) -> FileResponse:
    return FileResponse(_asset_path(filename))


def admin_page_response() -> HTMLResponse:
    template = _asset_path("index.html").read_text(encoding="utf-8")
    return HTMLResponse(
        template.replace(_ADMIN_ASSET_VERSION_PLACEHOLDER, package_version())
    )


@router.get("/admin", include_in_schema=False)
@router.get("/admin/model_config", include_in_schema=False)
@router.get("/admin/messaging", include_in_schema=False)
@router.get("/admin/security", include_in_schema=False)
@router.get("/admin/metrics", include_in_schema=False)
@router.get("/admin/console", include_in_schema=False)
@router.get("/admin/integrations", include_in_schema=False)
def admin_page(request: Request):
    check_rate_limit(request)
    require_loopback_admin(request)
    log_security_event("admin_page_access", request)
    return admin_page_response()


@router.get("/admin/assets/{version}/{filename}", include_in_schema=False)
async def admin_asset(version: str, filename: str, request: Request):
    from free_claude_code.native import is_safe_asset_name

    check_rate_limit(request)
    require_loopback_admin(request)
    if version != package_version() or filename not in _ADMIN_ASSET_FILENAMES:
        raise HTTPException(status_code=404, detail="Admin asset not found")
    # Native ultra path validation (no separators / traversal / odd charset)
    if not is_safe_asset_name(filename):
        raise HTTPException(status_code=400, detail="Invalid filename")
    return _asset_response(filename)


@router.get("/admin/api/config")
async def get_admin_config(
    request: Request, services: ApiServices = Depends(get_services)
):
    check_rate_limit(request)
    require_loopback_admin(request)
    _enforce_admin_api_token(request, services)
    return await services.admin.admin_config()


@router.post("/admin/api/config/apply")
async def apply_admin_config(
    payload: AdminConfigPayload,
    request: Request,
    services: ApiServices = Depends(get_services),
):
    check_rate_limit(request)
    check_request_size(request, max_size=1024 * 1024)  # 1MB max for config
    require_loopback_admin(request)
    _enforce_admin_api_token(request, services)
    log_security_event(
        "admin_config_apply",
        request,
        {"keys": list(payload.values.keys())[:10]},
    )
    result = await services.admin.apply_admin_config(_filtered_values(payload.values))
    return result


@router.get("/admin/api/status")
async def admin_status(
    request: Request,
    response: Response,
    services: ApiServices = Depends(get_services),
):
    check_rate_limit(request)
    require_loopback_admin(request)
    # Allow CORS for remote admin reconnection
    if origin := request.headers.get("origin"):
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Vary"] = "Origin"
    return await services.admin.admin_status()


@router.get("/admin/api/providers/local-status")
async def local_provider_status(
    request: Request, services: ApiServices = Depends(get_services)
):
    check_rate_limit(request)
    require_loopback_admin(request)
    values = {
        key: entry.value or ""
        for key, entry in (await services.admin.admin_values()).items()
    }
    checks = await asyncio.gather(
        *(
            _check_local_provider(
                provider_id,
                _local_provider_url(provider_id, values),
                path,
            )
            for provider_id, path in LOCAL_PROVIDER_PATHS.items()
        )
    )
    return {"providers": checks}


@router.post("/admin/api/providers/{provider_id}/test")
async def test_provider(
    provider_id: str,
    request: Request,
    services: ApiServices = Depends(get_services),
):
    import time as _time

    check_rate_limit(request)
    require_loopback_admin(request)
    _enforce_admin_api_token(request, services)
    validate_provider_id(provider_id)
    log_security_event("provider_test", request, {"provider_id": provider_id})
    started = _time.perf_counter()
    result = await services.admin.test_provider(provider_id)
    latency_ms = (_time.perf_counter() - started) * 1000.0
    try:
        from .metrics import metrics as runtime_metrics

        ok = False
        message = ""
        if isinstance(result, dict):
            status = str(result.get("status") or result.get("ok") or "")
            ok = status.lower() in {"ok", "true", "configured", "reachable", "connected", "1"}
            if result.get("ok") is True:
                ok = True
            message = str(result.get("message") or result.get("detail") or status)[:300]
        runtime_metrics.record_provider_test(
            provider_id, ok=ok, message=message, latency_ms=latency_ms
        )
    except Exception:
        pass
    return result


@router.get("/admin/api/security/audit")
async def security_audit(request: Request):
    """Security audit endpoint - shows current security config."""
    check_rate_limit(request)
    require_loopback_admin(request)
    _enforce_admin_api_token(request)
    from .admin_security import _is_remote_admin_allowed
    import os

    return {
        "remote_admin_allowed": _is_remote_admin_allowed(),
        "local_only_enforced": os.getenv("FCC_ADMIN_LOCAL_ONLY", "").lower() in {"1", "true", "yes"},
        "ip_allowlist_configured": bool(os.getenv("FCC_ADMIN_IP_ALLOWLIST", "").strip()),
        "admin_api_token_configured": bool(os.getenv("FCC_ADMIN_API_TOKEN", "").strip()),
        "cors_enabled": True,
        "security_headers": True,
        "rate_limiting": True,
        "metrics_enabled": True,
        "security_event_ring": True,
        "prometheus_export": True,
        "openmetrics_export": True,
        "openmetrics_protobuf": True,
        "admin_console_ws": True,
        "audit_bundle_export": True,
        "audit_bundle_signed": bool(os.getenv("FCC_AUDIT_SIGNING_KEY", "").strip()),
        "audit_bundle_ed25519": bool(os.getenv("FCC_AUDIT_ED25519_SEED", "").strip()),
        "console_fanin": True,
        "console_fanin_scrape": True,
        "console_fanin_mtls": bool(os.getenv("FCC_FANIN_MTLS_CERT", "").strip()),
        "hub_mesh": True,
        "native_hotpath_v2": True,
        "native_backend": __import__("free_claude_code.native", fromlist=["backend"]).backend(),
        "version": package_version(),
    }


@router.get("/admin/api/health/detailed")
async def detailed_health(
    request: Request, services: ApiServices = Depends(get_services)
):
    """Detailed health check with security info."""
    check_rate_limit(request)
    require_loopback_admin(request)
    _enforce_admin_api_token(request, services)
    status = await services.admin.admin_status()
    from .metrics import metrics as runtime_metrics

    snap = runtime_metrics.snapshot()
    return {
        "status": "healthy",
        "version": package_version(),
        "instance_id": status.get("instance_id"),
        "providers_configured": len(
            [p for p in (await services.admin.admin_values()).values() if p.value]
        ),
        "model_count": len(services.requests.cached_prefixed_model_infos()),
        "uptime_seconds": snap["uptime_seconds"],
        "total_requests": snap["total_requests"],
        "error_rate": snap["error_rate"],
        "requests_per_second": snap["requests_per_second"],
    }




class AdminSessionTokenPayload(BaseModel):
    """Set/clear the HttpOnly admin token cookie for EventSource."""

    token: str = Field(default="", max_length=512)


@router.post("/admin/api/session/token")
async def set_admin_session_token(
    payload: AdminSessionTokenPayload,
    request: Request,
    response: Response,
    services: ApiServices = Depends(get_services),
):
    """Store admin API token in an HttpOnly cookie (EventSource bridge).

    Does not replace header auth. Cookie is SameSite=Strict, path=/admin.
    Empty token clears the cookie.
    """
    import os
    import secrets as _secrets

    check_rate_limit(request)
    require_loopback_admin(request)
    try:
        settings = services.requests.current_settings()
        configured = (getattr(settings, "admin_api_token", None) or "").strip()
    except Exception:
        configured = ""
    if not configured:
        configured = os.getenv("FCC_ADMIN_API_TOKEN", "").strip()
    value = (payload.token or "").strip()[:512]
    # If server requires a token, validate before setting cookie
    if configured:
        if not value or not _secrets.compare_digest(
            value.encode("utf-8"), configured.encode("utf-8")
        ):
            log_security_event("admin_session_token_rejected", request, level="warning")
            raise HTTPException(status_code=401, detail="Invalid admin authentication token")

    secure = request.url.scheme == "https"
    if value:
        response.set_cookie(
            key="fcc_admin_token",
            value=value,
            httponly=True,
            secure=secure,
            samesite="strict",
            path="/admin",
            max_age=60 * 60 * 12,
        )
        log_security_event("admin_session_token_set", request, level="info")
        return _no_store({"ok": True, "set": True})
    response.delete_cookie("fcc_admin_token", path="/admin")
    log_security_event("admin_session_token_cleared", request, level="info")
    return _no_store({"ok": True, "set": False})


@router.delete("/admin/api/session/token")
async def clear_admin_session_token(request: Request, response: Response):
    """Clear the admin token cookie."""
    check_rate_limit(request)
    require_loopback_admin(request)
    response.delete_cookie("fcc_admin_token", path="/admin")
    log_security_event("admin_session_token_cleared", request, level="info")
    return _no_store({"ok": True, "set": False})


@router.get("/admin/api/metrics/stream")
async def metrics_live_stream(request: Request, services: ApiServices = Depends(get_services)):
    """SSE live metrics snapshots for the Metrics view."""
    import asyncio
    import json

    check_rate_limit(request)
    require_loopback_admin(request)
    _enforce_admin_api_token(request, services)
    from .metrics import metrics as runtime_metrics
    from fastapi.responses import StreamingResponse

    async def gen():
        while True:
            if await request.is_disconnected():
                break
            snap = runtime_metrics.snapshot()
            # Trim heavy fields for wire
            slim = {
                "uptime_seconds": snap.get("uptime_seconds"),
                "total_requests": snap.get("total_requests"),
                "total_errors": snap.get("total_errors"),
                "error_rate": snap.get("error_rate"),
                "requests_per_second": snap.get("requests_per_second"),
                "rate_limit_hits": snap.get("rate_limit_hits"),
                "latency_histogram_ms": snap.get("latency_histogram_ms"),
                "latency_overflow": snap.get("latency_overflow"),
                "provider_latency": (snap.get("provider_latency") or [])[:15],
                "top_routes": (snap.get("top_routes") or [])[:10],
                "status_codes": snap.get("status_codes"),
            }
            body = json.dumps(slim, separators=(",", ":"), default=str)
            yield f"event: metrics\ndata: {body}\n\n"
            await asyncio.sleep(2.0)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-store",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@router.get("/admin/api/metrics/export")
async def metrics_export(request: Request, services: ApiServices = Depends(get_services)):
    """Export metrics snapshot for multi-node federation / scraping."""
    check_rate_limit(request)
    require_loopback_admin(request)
    _enforce_admin_api_token(request, services)
    from .metrics import metrics as runtime_metrics
    import time as _time
    import os

    snap = runtime_metrics.snapshot()
    return _no_store(
        {
            "format": "fcc-metrics-federation",
            "format_version": 1,
            "exported_at": _time.time(),
            "node_id": os.getenv("FCC_NODE_ID")
            or (f"node-{int(snap['started_at'])}" if snap.get("started_at") else "node-local"),
            "version": package_version(),
            "snapshot": snap,
        }
    )


@router.post("/admin/api/metrics/merge")
async def metrics_merge(
    request: Request,
    services: ApiServices = Depends(get_services),
):
    """Merge multiple federation export payloads into one aggregate view.

    Body: { "nodes": [ <export>, ... ] } — pure function, does not mutate local metrics.
    """
    check_rate_limit(request)
    require_loopback_admin(request)
    _enforce_admin_api_token(request, services)
    check_request_size(request, max_size=2 * 1024 * 1024)
    body = await request.json()
    nodes = body.get("nodes") if isinstance(body, dict) else None
    if not isinstance(nodes, list) or len(nodes) > 64:
        raise HTTPException(status_code=400, detail="nodes must be a list (max 64)")
    from .metrics_federation import merge_metric_exports

    return _no_store(merge_metric_exports(nodes))



@router.get("/admin/api/metrics/prometheus")
async def metrics_prometheus(
    request: Request, services: ApiServices = Depends(get_services)
):
    """Prometheus text exposition of runtime metrics (scrape-compatible)."""
    import os

    from fastapi.responses import PlainTextResponse

    from .metrics import metrics as runtime_metrics
    from .prometheus_export import render_prometheus_text

    check_rate_limit(request)
    require_loopback_admin(request)
    _enforce_admin_api_token(request, services)
    snap = runtime_metrics.snapshot()
    node_id = os.getenv("FCC_NODE_ID") or (
        f"node-{int(snap['started_at'])}" if snap.get("started_at") else "node-local"
    )
    body = render_prometheus_text(
        snap,
        node_id=node_id,
        version=package_version(),
    )
    return PlainTextResponse(
        body,
        media_type="text/plain; version=0.0.4; charset=utf-8",
        headers={"Cache-Control": "no-store"},
    )



@router.get("/admin/api/metrics/openmetrics")
async def metrics_openmetrics(
    request: Request, services: ApiServices = Depends(get_services)
):
    """OpenMetrics 1.0.0 text exposition of runtime metrics."""
    import os

    from fastapi.responses import PlainTextResponse

    from .metrics import metrics as runtime_metrics
    from .openmetrics_export import render_openmetrics_text

    check_rate_limit(request)
    require_loopback_admin(request)
    _enforce_admin_api_token(request, services)
    snap = runtime_metrics.snapshot()
    node_id = os.getenv("FCC_NODE_ID") or (
        f"node-{int(snap['started_at'])}" if snap.get("started_at") else "node-local"
    )
    body = render_openmetrics_text(
        snap,
        node_id=node_id,
        version=package_version(),
    )
    return PlainTextResponse(
        body,
        media_type="application/openmetrics-text; version=1.0.0; charset=utf-8",
        headers={"Cache-Control": "no-store"},
    )



@router.get("/admin/api/metrics/openmetrics.pb")
async def metrics_openmetrics_protobuf(
    request: Request, services: ApiServices = Depends(get_services)
):
    """OpenMetrics protobuf-lite binary metrics (FCCOM1, no external deps)."""
    import os

    from fastapi.responses import Response as FastResponse

    from .metrics import metrics as runtime_metrics
    from .openmetrics_protobuf import render_openmetrics_protobuf

    check_rate_limit(request)
    require_loopback_admin(request)
    _enforce_admin_api_token(request, services)
    snap = runtime_metrics.snapshot()
    node_id = os.getenv("FCC_NODE_ID") or (
        f"node-{int(snap['started_at'])}" if snap.get("started_at") else "node-local"
    )
    body = render_openmetrics_protobuf(
        snap,
        node_id=node_id,
        version=package_version(),
    )
    return FastResponse(
        content=body,
        media_type="application/octet-stream",
        headers={
            "Cache-Control": "no-store",
            "X-FCC-Metrics-Format": "fccom1",
        },
    )


@router.get("/admin/api/security/events/export")
async def security_events_export(request: Request):
    """Portable security-events export for multi-replica fan-in."""
    import os

    check_rate_limit(request)
    require_loopback_admin(request)
    _enforce_admin_api_token(request)
    from free_claude_code.native import security_events as ring

    from .console_fanin import export_local_events

    events = ring.snapshot(after_seq=0, limit=200)
    node_id = os.getenv("FCC_NODE_ID") or "node-local"
    return _no_store(
        export_local_events(
            node_id=node_id,
            version=package_version(),
            events=events,
            latest_seq=ring.latest_seq(),
        )
    )


@router.post("/admin/api/console/fanin/ingest")
async def console_fanin_ingest(request: Request):
    """Ingest a peer node's security-events export into the local fan-in hub."""
    check_rate_limit(request)
    require_loopback_admin(request)
    _enforce_admin_api_token(request)
    check_request_size(request, max_size=1 * 1024 * 1024)
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="JSON object required")
    from .console_fanin import fanin_hub

    try:
        result = fanin_hub.ingest(body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)[:200]) from exc
    log_security_event("console_fanin_ingest", request, {"node_id": result.get("node_id")})
    return _no_store(result)


@router.post("/admin/api/console/fanin/merge")
async def console_fanin_merge(request: Request):
    """Pure merge of multiple security-events exports (no hub mutation)."""
    check_rate_limit(request)
    require_loopback_admin(request)
    _enforce_admin_api_token(request)
    check_request_size(request, max_size=2 * 1024 * 1024)
    body = await request.json()
    nodes = body.get("nodes") if isinstance(body, dict) else None
    if not isinstance(nodes, list) or len(nodes) > 32:
        raise HTTPException(status_code=400, detail="nodes must be a list (max 32)")
    from .console_fanin import merge_event_exports

    return _no_store(merge_event_exports(nodes))


@router.get("/admin/api/console/fanin")
async def console_fanin_snapshot(request: Request, limit: int = 100):
    """Current fan-in hub snapshot (merged events + node roster)."""
    check_rate_limit(request)
    require_loopback_admin(request)
    _enforce_admin_api_token(request)
    from .console_fanin import fanin_hub

    return _no_store(fanin_hub.snapshot(limit=limit))



@router.post("/admin/api/console/fanin/scrape")
async def console_fanin_scrape(request: Request):
    """Actively scrape peer FCC nodes and ingest security-event exports.

    Body: { "peers": ["http://host:8082", ...], "token": "optional override" }
    Peers must pass SSRF checks; when FCC_FANIN_PEER_ALLOWLIST is set, hosts
    must match. Uses FCC_ADMIN_API_TOKEN (or body token) as X-FCC-Admin-Token.
    """
    import os

    import httpx

    from .console_fanin import fanin_hub
    from .peer_scrape import normalize_peer_list, scrape_result

    check_rate_limit(request)
    require_loopback_admin(request)
    _enforce_admin_api_token(request)
    check_request_size(request, max_size=64 * 1024)
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="JSON object required")
    try:
        peers = normalize_peer_list(body.get("peers") or [])
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)[:200]) from exc
    if not peers:
        raise HTTPException(status_code=400, detail="peers list required")

    token = str(body.get("token") or "").strip()[:512]
    if not token:
        token = os.getenv("FCC_ADMIN_API_TOKEN", "").strip()
    headers = {}
    if token:
        headers["X-FCC-Admin-Token"] = token

    results = []
    from .peer_scrape import mtls_client_kwargs_from_env

    _mtls = mtls_client_kwargs_from_env()
    async with httpx.AsyncClient(
        timeout=3.0, follow_redirects=False, **_mtls
    ) as client:
        for url in peers:
            try:
                resp = await client.get(url, headers=headers)
                if resp.status_code != 200:
                    results.append(
                        scrape_result(
                            url=url,
                            ok=False,
                            status_code=resp.status_code,
                            error=f"HTTP {resp.status_code}",
                        )
                    )
                    continue
                try:
                    payload = resp.json()
                except Exception:
                    results.append(
                        scrape_result(
                            url=url,
                            ok=False,
                            status_code=resp.status_code,
                            error="invalid JSON",
                        )
                    )
                    continue
                if not isinstance(payload, dict):
                    results.append(
                        scrape_result(
                            url=url,
                            ok=False,
                            status_code=resp.status_code,
                            error="JSON object required",
                        )
                    )
                    continue
                # ensure node_id
                if not payload.get("node_id"):
                    from urllib.parse import urlparse

                    host = urlparse(url).hostname or "peer"
                    payload["node_id"] = f"peer-{host}"[:64]
                try:
                    ing = fanin_hub.ingest(payload)
                except ValueError as exc:
                    results.append(
                        scrape_result(
                            url=url,
                            ok=False,
                            status_code=resp.status_code,
                            error=str(exc)[:200],
                        )
                    )
                    continue
                results.append(
                    scrape_result(
                        url=url,
                        ok=True,
                        status_code=resp.status_code,
                        node_id=ing.get("node_id"),
                        event_count=int(ing.get("event_count") or 0),
                        ingested=True,
                    )
                )
            except httpx.TimeoutException:
                results.append(scrape_result(url=url, ok=False, error="timeout"))
            except httpx.HTTPError as exc:
                results.append(
                    scrape_result(url=url, ok=False, error=type(exc).__name__)
                )
            except Exception as exc:
                results.append(
                    scrape_result(url=url, ok=False, error=type(exc).__name__)
                )

    log_security_event(
        "console_fanin_scrape",
        request,
        {
            "peers": len(peers),
            "ok": sum(1 for r in results if r.get("ok")),
        },
        level="info",
    )
    return _no_store(
        {
            "format": "fcc-console-fanin-scrape",
            "format_version": 1,
            "results": results,
            "hub": fanin_hub.summary(),
        }
    )


@router.post("/admin/api/audit/bundle/verify")
async def audit_bundle_verify(request: Request):
    """Verify a signed audit ZIP (multipart file field ``bundle``).

    Uses FCC_AUDIT_SIGNING_KEY on the server. Returns {ok, reason, ...}.
    """
    import os

    from .audit_sign import signing_key_from_env, verify_any_signed_zip

    check_rate_limit(request)
    require_loopback_admin(request)
    _enforce_admin_api_token(request)
    key = signing_key_from_env()
    form = await request.form()
    upload = form.get("bundle")
    if upload is None:
        raise HTTPException(status_code=400, detail="multipart field 'bundle' required")
    if hasattr(upload, "read"):
        data = await upload.read()
    elif isinstance(upload, (bytes, bytearray)):
        data = bytes(upload)
    else:
        raise HTTPException(status_code=400, detail="invalid upload")
    if not isinstance(data, (bytes, bytearray)):
        raise HTTPException(status_code=400, detail="invalid upload")
    if len(data) > 8 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="bundle too large (max 8MB)")
    result = verify_any_signed_zip(bytes(data), hmac_key=key)
    log_security_event(
        "audit_bundle_verify",
        request,
        {"ok": result.get("ok"), "reason": result.get("reason")},
        level="info" if result.get("ok") else "warning",
    )
    return _no_store(result)



@router.post("/admin/api/console/mesh/register")
async def console_mesh_register(request: Request):
    """Register or refresh a peer hub in the multi-hub federation mesh."""
    check_rate_limit(request)
    require_loopback_admin(request)
    _enforce_admin_api_token(request)
    check_request_size(request, max_size=64 * 1024)
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="JSON object required")
    from .hub_mesh import hub_mesh

    try:
        result = hub_mesh.register(body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)[:200]) from exc
    log_security_event("hub_mesh_register", request, {"hub_id": result.get("hub_id")})
    return _no_store(result)


@router.get("/admin/api/console/mesh")
async def console_mesh_snapshot(request: Request):
    """Snapshot of registered peer hubs."""
    check_rate_limit(request)
    require_loopback_admin(request)
    _enforce_admin_api_token(request)
    from .hub_mesh import hub_mesh

    return _no_store(hub_mesh.snapshot())


@router.get("/admin/api/audit/bundle")
async def audit_bundle_export(
    request: Request, services: ApiServices = Depends(get_services)
):
    """Download a ZIP audit bundle (metrics + security + audit, no secrets)."""
    import os
    import time as _time

    from fastapi.responses import Response as FastResponse

    from .audit_bundle import build_audit_bundle_zip
    from .console_fanin import fanin_hub
    from .metrics import metrics as runtime_metrics
    from .openmetrics_export import render_openmetrics_text
    from .prometheus_export import render_prometheus_text
    from free_claude_code.native import security_events as ring
    from .admin_security import _is_remote_admin_allowed

    check_rate_limit(request)
    require_loopback_admin(request)
    _enforce_admin_api_token(request, services)
    log_security_event("audit_bundle_export", request, level="info")

    snap = runtime_metrics.snapshot()
    node_id = os.getenv("FCC_NODE_ID") or (
        f"node-{int(snap['started_at'])}" if snap.get("started_at") else "node-local"
    )
    ver = package_version()
    audit = {
        "remote_admin_allowed": _is_remote_admin_allowed(),
        "local_only_enforced": os.getenv("FCC_ADMIN_LOCAL_ONLY", "").lower()
        in {"1", "true", "yes"},
        "ip_allowlist_configured": bool(os.getenv("FCC_ADMIN_IP_ALLOWLIST", "").strip()),
        "admin_api_token_configured": bool(os.getenv("FCC_ADMIN_API_TOKEN", "").strip()),
        "prometheus_export": True,
        "openmetrics_export": True,
        "openmetrics_protobuf": True,
        "admin_console_ws": True,
        "audit_bundle_export": True,
        "audit_bundle_signed": bool(os.getenv("FCC_AUDIT_SIGNING_KEY", "").strip()),
        "audit_bundle_ed25519": bool(os.getenv("FCC_AUDIT_ED25519_SEED", "").strip()),
        "console_fanin": True,
        "console_fanin_scrape": True,
        "console_fanin_mtls": bool(os.getenv("FCC_FANIN_MTLS_CERT", "").strip()),
        "hub_mesh": True,
        "native_hotpath_v2": True,
        "version": ver,
    }
    events = ring.snapshot(after_seq=0, limit=200)
    metrics_exp = {
        "format": "fcc-metrics-federation",
        "format_version": 1,
        "exported_at": _time.time(),
        "node_id": node_id,
        "version": ver,
        "snapshot": snap,
    }
    prom = render_prometheus_text(snap, node_id=node_id, version=ver)
    om = render_openmetrics_text(snap, node_id=node_id, version=ver)
    # Optional OpenMetrics protobuf-lite attachment
    from .openmetrics_protobuf import render_openmetrics_protobuf

    pb = render_openmetrics_protobuf(snap, node_id=node_id, version=ver)
    blob = build_audit_bundle_zip(
        node_id=node_id,
        version=ver,
        audit=audit,
        events=events,
        metrics_export=metrics_exp,
        prometheus_text=prom,
        openmetrics_text=om,
        fanin_summary=fanin_hub.summary(),
        extra_files={"metrics.openmetrics.pb": pb},
    )
    signed = False
    signed_ed = False
    from .audit_sign import (
        ed25519_seed_from_env,
        sign_and_attach,
        sign_and_attach_ed25519,
        signing_key_from_env,
    )

    key = signing_key_from_env()
    if key:
        blob, _sig = sign_and_attach(blob, key=key, node_id=node_id, version=ver)
        signed = True
    seed = ed25519_seed_from_env()
    if seed:
        blob, _esig = sign_and_attach_ed25519(
            blob, seed=seed, node_id=node_id, version=ver
        )
        signed_ed = True
    stamp = _time.strftime("%Y%m%d-%H%M%S", _time.gmtime())
    filename = f"fcc-audit-{node_id}-{stamp}.zip"
    headers = {
        "Cache-Control": "no-store",
        "Content-Disposition": f'attachment; filename="{filename}"',
        "X-FCC-Audit-Signed": "1" if signed else "0",
        "X-FCC-Audit-Ed25519": "1" if signed_ed else "0",
    }
    return FastResponse(
        content=blob,
        media_type="application/zip",
        headers=headers,
    )


@router.websocket("/admin/api/console/ws")
async def admin_console_ws(websocket: WebSocket):
    """Bidirectional admin console: security + metrics push, ping/subscribe.

    Auth: cookie ``fcc_admin_token`` on upgrade, or first message
    ``{"op":"auth","token":"..."}`` when ``FCC_ADMIN_API_TOKEN`` is set.
    """
    import asyncio
    import os
    import secrets as _secrets
    import time as _time

    from .admin_console import (
        dumps,
        error_message,
        fanin_frame,
        handle_command,
        metrics_frame,
        parse_client_message,
        security_event_frame,
        system_frame,
        welcome_message,
    )
    from .metrics import metrics as runtime_metrics

    # IP / remote gate before accepting the socket
    client = websocket.client
    real_ip = None
    forwarded = websocket.headers.get("x-forwarded-for")
    if forwarded:
        real_ip = forwarded.split(",")[0].strip()
    elif client:
        real_ip = client.host
    try:
        from .admin_security import _is_ip_allowed, _is_remote_admin_allowed, _is_loopback_host
        if not _is_ip_allowed(real_ip or (client.host if client else None)):
            await websocket.close(code=1008, reason="IP allowlist")
            return
        if not _is_remote_admin_allowed():
            host = client.host if client else None
            if host and not _is_loopback_host(host):
                await websocket.close(code=1008, reason="local only")
                return
    except Exception:
        await websocket.close(code=1011, reason="gate error")
        return

    await websocket.accept()

    configured = os.getenv("FCC_ADMIN_API_TOKEN", "").strip()
    cookie_tok = (websocket.cookies.get("fcc_admin_token") or "").strip()
    authed = False
    if not configured:
        authed = True
    elif cookie_tok and _secrets.compare_digest(
        cookie_tok.encode("utf-8"), configured.encode("utf-8")
    ):
        authed = True

    node_id = os.getenv("FCC_NODE_ID") or "node-local"
    subscribed: set[str] = set()
    send_task = None
    try:
        if not authed:
            try:
                raw = await asyncio.wait_for(websocket.receive_text(), timeout=10.0)
            except (asyncio.TimeoutError, WebSocketDisconnect):
                await websocket.send_text(dumps(error_message("auth_timeout", "Auth required")))
                await websocket.close(code=1008, reason="auth required")
                return
            try:
                data = parse_client_message(raw)
            except (ValueError, Exception) as e:
                await websocket.send_text(dumps(error_message("bad_frame", str(e)[:200])))
                await websocket.close(code=1003, reason="bad frame")
                return
            if data.get("op") != "auth":
                await websocket.send_text(
                    dumps(error_message("auth_required", "Send op=auth first"))
                )
                await websocket.close(code=1008, reason="auth required")
                return
            token = str(data.get("token") or "").strip()
            if not token or not _secrets.compare_digest(
                token.encode("utf-8"), configured.encode("utf-8")
            ):
                log_security_event("admin_ws_auth_failed", None, level="warning")
                await websocket.send_text(dumps(error_message("auth_failed", "Invalid token")))
                await websocket.close(code=1008, reason="auth failed")
                return
            authed = True
            await websocket.send_text(dumps({"op": "auth_ok", "ts": _time.time()}))

        log_security_event("admin_ws_connected", None, level="info")
        await websocket.send_text(
            dumps(welcome_message(node_id=node_id, version=package_version()))
        )
        subscribed = {"system"}
        await websocket.send_text(dumps(system_frame("console ready")))

        from free_claude_code.native import security_events as ring

        last_seq = ring.latest_seq()
        last_metrics_push = 0.0
        last_fanin_push = 0.0

        async def sender() -> None:
            nonlocal last_seq, last_metrics_push, last_fanin_push
            from .console_fanin import fanin_hub

            while True:
                await asyncio.sleep(0.5)
                now = _time.time()
                if "security" in subscribed:
                    cur = ring.latest_seq()
                    if cur > last_seq:
                        events = list(reversed(ring.snapshot(after_seq=last_seq, limit=40)))
                        last_seq = cur
                        await websocket.send_text(dumps(security_event_frame(events, cur)))
                if "metrics" in subscribed and (now - last_metrics_push) >= 2.0:
                    last_metrics_push = now
                    snap = runtime_metrics.snapshot()
                    await websocket.send_text(dumps(metrics_frame(snap)))
                if "fanin" in subscribed and (now - last_fanin_push) >= 2.0:
                    last_fanin_push = now
                    await websocket.send_text(dumps(fanin_frame(fanin_hub.snapshot(limit=40))))

        send_task = asyncio.create_task(sender())
        while True:
            raw = await websocket.receive_text()
            try:
                data = parse_client_message(raw)
            except (ValueError, Exception) as e:
                await websocket.send_text(dumps(error_message("bad_frame", str(e)[:200])))
                continue
            op = data["op"]
            if op == "auth":
                await websocket.send_text(dumps({"op": "auth_ok", "ts": _time.time()}))
                continue
            reply, subscribed = handle_command(op, data, subscribed=subscribed)
            if reply is not None:
                await websocket.send_text(dumps(reply))
    except WebSocketDisconnect:
        log_security_event("admin_ws_disconnected", None, level="info")
    except Exception as exc:
        logger.warning("admin console ws error: {}", exc)
        try:
            await websocket.close(code=1011, reason="server error")
        except Exception:
            pass
    finally:
        if send_task is not None:
            send_task.cancel()
            try:
                await send_task
            except (asyncio.CancelledError, Exception):
                pass


@router.get("/admin/api/security/events/stream")
async def security_events_stream(request: Request):
    """SSE live tail of security/audit events (admin-only)."""
    import asyncio
    import json

    check_rate_limit(request)
    require_loopback_admin(request)
    _enforce_admin_api_token(request)
    from free_claude_code.native import security_events as ring
    from fastapi.responses import StreamingResponse

    async def event_gen():
        last_seq = ring.latest_seq()
        # initial snapshot
        snap = ring.snapshot(after_seq=0, limit=20)
        payload = json.dumps(
            {"events": list(reversed(snap)), "latest_seq": ring.latest_seq()},
            separators=(",", ":"),
            default=str,
        )
        yield f"event: snapshot\ndata: {payload}\n\n"
        while True:
            if await request.is_disconnected():
                break
            await asyncio.sleep(1.0)
            cur = ring.latest_seq()
            if cur <= last_seq:
                yield ": keepalive\n\n"
                continue
            events = ring.snapshot(after_seq=last_seq, limit=50)
            # snapshot returns newest first; reverse for chronological SSE
            events = list(reversed(events))
            last_seq = cur
            body = json.dumps(
                {"events": events, "latest_seq": cur},
                separators=(",", ":"),
                default=str,
            )
            yield f"event: events\ndata: {body}\n\n"

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-store",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@router.get("/admin/api/security/events")
async def security_events_tail(
    request: Request,
    after: int = 0,
    limit: int = 100,
):
    """Recent security/audit events for the admin live tail."""
    check_rate_limit(request)
    require_loopback_admin(request)
    _enforce_admin_api_token(request)
    from free_claude_code.native import security_events as ring
    after = max(0, int(after or 0))
    limit = max(1, min(int(limit or 100), 500))
    return _no_store(
        {
            "events": ring.snapshot(after_seq=after, limit=limit),
            "latest_seq": ring.latest_seq(),
        }
    )


@router.get("/admin/api/metrics")
async def admin_metrics(
    request: Request, services: ApiServices = Depends(get_services)
):
    """Runtime metrics for the admin dashboard (latency, RPS, recent traffic)."""
    check_rate_limit(request)
    require_loopback_admin(request)
    _enforce_admin_api_token(request, services)
    from .metrics import metrics as runtime_metrics

    return _no_store(runtime_metrics.snapshot())


@router.get("/admin/api/config/export")
async def export_admin_config(
    request: Request, services: ApiServices = Depends(get_services)
):
    """Export non-secret admin config as a portable JSON backup.

    Secret fields are omitted (never exported). Safe to download and share
    structure without leaking API keys.
    """
    check_rate_limit(request)
    require_loopback_admin(request)
    _enforce_admin_api_token(request, services)
    log_security_event("admin_config_export", request, {})
    config = await services.admin.admin_config()
    fields = config.get("fields") or []
    exported: dict[str, object] = {}
    skipped_secrets: list[str] = []
    for field in fields:
        if not isinstance(field, dict):
            continue
        key = field.get("key")
        if not isinstance(key, str) or not key:
            continue
        if field.get("secret"):
            skipped_secrets.append(key)
            continue
        if field.get("locked"):
            continue
        value = field.get("value")
        # Skip empty / unset values to keep export small
        if value is None or value == "":
            continue
        exported[key] = value
    return _no_store(
        {
            "format": "fcc-admin-config",
            "format_version": 1,
            "exported_at": __import__("time").time(),
            "version": package_version(),
            "values": exported,
            "skipped_secrets": skipped_secrets,
            "note": "Secret fields are never exported. Re-enter API keys after import.",
        }
    )


class AdminConfigImportPayload(BaseModel):
    """Import payload for portable admin config backups."""

    values: JsonObject = Field(default_factory=dict)
    apply: bool = False

    @field_validator("values")
    @classmethod
    def validate_import_values(cls, v: JsonObject) -> JsonObject:
        if len(v) > 200:
            raise ValueError("Too many config values")
        for key, value in v.items():
            if not isinstance(key, str) or len(key) > 128:
                raise ValueError("Invalid config key")
            if isinstance(value, str) and len(value) > 10_000:
                raise ValueError(f"Value too long for key {key[:20]}")
        return v


@router.post("/admin/api/config/import")
async def import_admin_config(
    payload: AdminConfigImportPayload,
    request: Request,
    services: ApiServices = Depends(get_services),
):
    """Preview or apply an imported non-secret config backup.

    When ``apply`` is false (default), returns a dry-run summary.
    Secrets in the payload are rejected. Known secret field keys from the
    live config are stripped even if the client sends them.
    """
    check_rate_limit(request)
    check_request_size(request, max_size=1024 * 1024)
    require_loopback_admin(request)
    _enforce_admin_api_token(request, services)
    log_security_event(
        "admin_config_import",
        request,
        {"keys": list(payload.values.keys())[:20], "apply": payload.apply},
    )

    live = await services.admin.admin_config()
    secret_keys = {
        field["key"]
        for field in (live.get("fields") or [])
        if isinstance(field, dict) and field.get("secret") and isinstance(field.get("key"), str)
    }
    known_keys = {
        field["key"]
        for field in (live.get("fields") or [])
        if isinstance(field, dict) and isinstance(field.get("key"), str)
    }

    cleaned: dict[str, object] = {}
    rejected_secrets: list[str] = []
    unknown_keys: list[str] = []
    for key, value in payload.values.items():
        if key in secret_keys or "KEY" in key.upper() or "TOKEN" in key.upper() or "SECRET" in key.upper():
            rejected_secrets.append(key)
            continue
        if key not in known_keys:
            unknown_keys.append(key)
            continue
        cleaned[key] = value

    preview = {
        "would_apply": sorted(cleaned.keys()),
        "rejected_secrets": rejected_secrets,
        "unknown_keys": unknown_keys[:50],
        "count": len(cleaned),
    }
    if not payload.apply:
        return _no_store({"applied": False, "dry_run": True, **preview})

    if not cleaned:
        return _no_store(
            {
                "applied": False,
                "dry_run": False,
                "errors": ["No importable non-secret values"],
                **preview,
            }
        )

    result = await services.admin.apply_admin_config(_filtered_values(cleaned))
    if isinstance(result, dict):
        result = {**result, **preview}
    return result


@router.get("/admin/api/providers/{provider_id}/auth")
async def connected_account_status(
    provider_id: str,
    request: Request,
    services: ApiServices = Depends(get_services),
):
    check_rate_limit(request)
    require_loopback_admin(request)
    validate_provider_id(provider_id)
    _require_connected_account_provider(provider_id)
    status = await services.admin.connected_account_status(provider_id)
    return _no_store(status.as_dict())


@router.post("/admin/api/providers/{provider_id}/auth/login")
async def start_connected_account_login(
    provider_id: str,
    payload: ConnectedAccountLoginPayload,
    request: Request,
    services: ApiServices = Depends(get_services),
):
    check_rate_limit(request)
    require_loopback_admin(request)
    validate_provider_id(provider_id)
    _require_connected_account_provider(provider_id)
    log_security_event("connected_account_login_start", request, {"provider_id": provider_id})
    account = await services.admin.connected_account_status(provider_id)
    mode = payload.mode or account.default_login_mode
    if mode not in account.supported_login_modes:
        raise HTTPException(
            status_code=422,
            detail="Login mode is not supported by this provider.",
        )
    try:
        status = await services.admin.start_connected_account_login(provider_id, mode)
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=(f"Could not start connected-account login ({type(exc).__name__})."),
        ) from exc
    return _no_store(status.as_dict())


@router.post("/admin/api/providers/{provider_id}/auth/cancel")
async def cancel_connected_account_login(
    provider_id: str,
    request: Request,
    services: ApiServices = Depends(get_services),
):
    check_rate_limit(request)
    require_loopback_admin(request)
    validate_provider_id(provider_id)
    _require_connected_account_provider(provider_id)
    status = await services.admin.cancel_connected_account_login(provider_id)
    return _no_store(status.as_dict())


@router.delete("/admin/api/providers/{provider_id}/auth")
async def disconnect_connected_account(
    provider_id: str,
    request: Request,
    services: ApiServices = Depends(get_services),
):
    check_rate_limit(request)
    require_loopback_admin(request)
    validate_provider_id(provider_id)
    _require_connected_account_provider(provider_id)
    log_security_event("connected_account_disconnect", request, {"provider_id": provider_id})
    status = await services.admin.disconnect_connected_account(provider_id)
    return _no_store(status.as_dict())


@router.get("/admin/api/models")
async def models(
    request: Request,
    services: ApiServices = Depends(get_services),
):
    check_rate_limit(request)
    require_loopback_admin(request)
    return _model_options(services)


@router.get("/admin/api/integrations/claude-vscode")
async def claude_vscode_status(
    request: Request,
    services: ApiServices = Depends(get_services),
):
    check_rate_limit(request)
    require_loopback_admin(request)
    return await _integration_response(services.admin.claude_vscode_status)


@router.post("/admin/api/integrations/claude-vscode/connect")
async def connect_claude_vscode(
    request: Request,
    services: ApiServices = Depends(get_services),
):
    check_rate_limit(request)
    require_loopback_admin(request)
    log_security_event("integration_connect", request, {"integration": "claude-vscode"})
    return await _integration_response(services.admin.connect_claude_vscode)


@router.post("/admin/api/integrations/claude-vscode/disconnect")
async def disconnect_claude_vscode(
    request: Request,
    services: ApiServices = Depends(get_services),
):
    check_rate_limit(request)
    require_loopback_admin(request)
    return await _integration_response(services.admin.disconnect_claude_vscode)


@router.get("/admin/api/integrations/codex")
async def codex_integration_status(
    request: Request,
    services: ApiServices = Depends(get_services),
):
    check_rate_limit(request)
    require_loopback_admin(request)
    return await _integration_response(services.admin.codex_integration_status)


@router.post("/admin/api/integrations/codex/connect")
async def connect_codex(
    request: Request,
    services: ApiServices = Depends(get_services),
):
    check_rate_limit(request)
    require_loopback_admin(request)
    log_security_event("integration_connect", request, {"integration": "codex"})
    return await _integration_response(services.admin.connect_codex)


@router.post("/admin/api/integrations/codex/disconnect")
async def disconnect_codex(
    request: Request,
    services: ApiServices = Depends(get_services),
):
    check_rate_limit(request)
    require_loopback_admin(request)
    return await _integration_response(services.admin.disconnect_codex)


async def _integration_response(
    operation: Callable[[], Awaitable[JsonObject]],
) -> JSONResponse:
    try:
        return _no_store(await operation())
    except ApplicationError as exc:
        return JSONResponse(
            {"detail": exc.message},
            status_code=exc.status_code,
            headers={"Cache-Control": "no-store"},
        )


@router.post("/admin/api/models/refresh")
async def refresh_models(
    request: Request,
    services: ApiServices = Depends(get_services),
):
    check_rate_limit(request)
    require_loopback_admin(request)
    log_security_event("models_refresh", request)
    result = await services.admin.refresh_models()
    return _model_options(services, refresh_result=result)


def _model_options(
    services: ApiServices,
    *,
    refresh_result: ProviderModelRefreshResult | None = None,
) -> dict[str, list[str]]:
    configured = {
        ref.model_ref
        for ref in configured_chat_model_refs(services.requests.current_settings())
    }
    discovered = {
        info.model_id for info in services.requests.cached_prefixed_model_infos()
    }
    failed_provider_ids = (
        refresh_result.failed_provider_ids if refresh_result is not None else ()
    )
    return {
        "models": sorted(configured | discovered, key=str.casefold),
        "failed_providers": list(failed_provider_ids),
    }


def _filtered_values(values: Mapping[str, JsonValue]) -> JsonObject:
    return {key: value for key, value in values.items() if key in FIELD_BY_KEY}


def _local_provider_url(provider_id: str, values: dict[str, str]) -> str:
    if provider_id == "lmstudio":
        return values.get("LM_STUDIO_BASE_URL", "")
    if provider_id == "llamacpp":
        return values.get("LLAMACPP_BASE_URL", "")
    if provider_id == "ollama":
        return values.get("OLLAMA_BASE_URL", "")
    return ""


async def _check_local_provider(
    provider_id: str, base_url: str, path: str
) -> JsonObject:
    clean_url = base_url.strip().rstrip("/")
    if not clean_url:
        return {
            "provider_id": provider_id,
            "status": "missing_url",
            "label": "Missing URL",
            "base_url": base_url,
        }

    url = f"{clean_url}{path}"
    try:
        async with httpx.AsyncClient(timeout=1.5) as client:
            response = await client.get(url)
        ok = 200 <= response.status_code < 300
        return {
            "provider_id": provider_id,
            "status": "reachable" if ok else "offline",
            "label": "Reachable" if ok else "Offline",
            "base_url": base_url,
            "status_code": response.status_code,
        }
    except Exception as exc:
        logger.debug(
            "Admin local provider check failed: provider={} exc_type={}",
            provider_id,
            type(exc).__name__,
        )
        return {
            "provider_id": provider_id,
            "status": "offline",
            "label": "Offline",
            "base_url": base_url,
            "message": _LOCAL_PROVIDER_CHECK_FAILURE_MESSAGE,
        }


def _require_connected_account_provider(provider_id: str) -> None:
    descriptor = PROVIDER_CATALOG.get(provider_id)
    if (
        descriptor is None
        or descriptor.auth_kind is not ProviderAuthKind.CONNECTED_ACCOUNT
    ):
        raise HTTPException(
            status_code=404,
            detail="Provider does not support connected-account login.",
        )


def _no_store(payload: JsonValue) -> JSONResponse:
    return JSONResponse(payload, headers={"Cache-Control": "no-store"})
