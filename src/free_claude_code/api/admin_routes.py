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
from .dependencies import get_services
from .ports import ApiServices
from .rate_limit import check_rate_limit
from .security import (
    check_request_size,
    log_security_event,
    validate_provider_id,
)

router = APIRouter()

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
@router.get("/admin/integrations", include_in_schema=False)
def admin_page(request: Request):
    check_rate_limit(request)
    require_loopback_admin(request)
    log_security_event("admin_page_access", request)
    return admin_page_response()


@router.get("/admin/assets/{version}/{filename}", include_in_schema=False)
async def admin_asset(version: str, filename: str, request: Request):
    check_rate_limit(request)
    require_loopback_admin(request)
    if version != package_version() or filename not in _ADMIN_ASSET_FILENAMES:
        raise HTTPException(status_code=404, detail="Admin asset not found")
    # Validate filename to prevent path traversal
    if ".." in filename or "/" in filename or "\\" in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")
    return _asset_response(filename)


@router.get("/admin/api/config")
async def get_admin_config(
    request: Request, services: ApiServices = Depends(get_services)
):
    check_rate_limit(request)
    require_loopback_admin(request)
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
    from .admin_security import _is_remote_admin_allowed
    import os

    return {
        "remote_admin_allowed": _is_remote_admin_allowed(),
        "local_only_enforced": os.getenv("FCC_ADMIN_LOCAL_ONLY", "").lower() in {"1", "true", "yes"},
        "ip_allowlist_configured": bool(os.getenv("FCC_ADMIN_IP_ALLOWLIST", "").strip()),
        "cors_enabled": True,
        "security_headers": True,
        "rate_limiting": True,
        "metrics_enabled": True,
        "version": package_version(),
    }


@router.get("/admin/api/health/detailed")
async def detailed_health(
    request: Request, services: ApiServices = Depends(get_services)
):
    """Detailed health check with security info."""
    check_rate_limit(request)
    require_loopback_admin(request)
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


@router.get("/admin/api/metrics")
async def admin_metrics(request: Request):
    """Runtime metrics for the admin dashboard (latency, RPS, recent traffic)."""
    check_rate_limit(request)
    require_loopback_admin(request)
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
