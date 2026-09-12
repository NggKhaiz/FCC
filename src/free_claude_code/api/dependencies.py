"""FastAPI dependencies for the explicit runtime service boundary - hardened."""

import secrets
import time

from fastapi import Depends, HTTPException, Request
from loguru import logger

from free_claude_code.application.errors import UnknownProviderError
from free_claude_code.application.ports import ProviderPort, RequestRuntimeLease
from free_claude_code.config.provider_catalog import PROVIDER_CATALOG
from free_claude_code.config.settings import Settings
from free_claude_code.native import SlidingWindow

from .ports import ApiServices
from .security import log_security_event

# Brute-force protection via native sliding window (auth failures)
_AUTH_FAIL_WINDOW = SlidingWindow()
_MAX_FAILED_ATTEMPTS = 10
_FAILED_WINDOW_SECONDS = 300.0  # 5 minutes
_BLOCK_SECONDS = 600.0  # 10 minutes


def _get_client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _is_ip_blocked(client_ip: str) -> bool:
    return _AUTH_FAIL_WINDOW.is_blocked(f"authfail:{client_ip}")


def _record_failed_auth(client_ip: str) -> None:
    key = f"authfail:{client_ip}"
    allowed, _retry = _AUTH_FAIL_WINDOW.allow(
        key,
        max_requests=_MAX_FAILED_ATTEMPTS,
        window_seconds=_FAILED_WINDOW_SECONDS,
        block_seconds=_BLOCK_SECONDS,
    )
    if not allowed:
        logger.warning(
            "IP blocked due to brute force: ip={} window={}s block={}s",
            client_ip,
            int(_FAILED_WINDOW_SECONDS),
            int(_BLOCK_SECONDS),
        )


def get_services(request: Request) -> ApiServices:
    """Return the complete services supplied when the app was constructed."""
    return request.app.state.services


def get_settings(services: ApiServices = Depends(get_services)) -> Settings:
    """Return the current request-runtime settings snapshot."""
    return services.requests.current_settings()


def resolve_provider(
    provider_type: str,
    *,
    lease: RequestRuntimeLease,
) -> ProviderPort:
    """Resolve a provider through one retained generation."""
    should_log_init = not lease.is_provider_cached(provider_type)
    try:
        provider = lease.resolve_provider(provider_type)
    except UnknownProviderError:
        logger.error(
            "Unknown provider_type: '{}'. Supported: {}",
            provider_type,
            ", ".join(f"'{key}'" for key in PROVIDER_CATALOG),
        )
        raise
    if should_log_init:
        logger.info("Provider initialized: {}", provider_type)
    return provider


def require_proxy_auth(
    request: Request,
    settings: Settings = Depends(get_settings),
) -> None:
    """Require the configured proxy token as HTTP bearer authorization."""
    client_ip = _get_client_ip(request)

    if _is_ip_blocked(client_ip):
        log_security_event("blocked_ip_auth_attempt", request, level="warning")
        raise HTTPException(
            status_code=429,
            detail="Too many failed authentication attempts. Try again later.",
        )

    if not settings.proxy_auth_enabled:
        return

    authorization = request.headers.get("authorization")
    if not authorization:
        _record_failed_auth(client_ip)
        log_security_event("missing_auth_token", request, level="warning")
        raise HTTPException(
            status_code=401,
            detail="Missing proxy authentication token",
        )

    if not _proxy_token_matches(
        authorization,
        settings.proxy_auth_token,
        require_bearer=True,
    ):
        _record_failed_auth(client_ip)
        log_security_event("invalid_auth_token", request, level="warning")
        raise HTTPException(
            status_code=401,
            detail="Invalid proxy authentication token",
        )


def require_anthropic_proxy_auth(
    request: Request,
    settings: Settings = Depends(get_settings),
) -> None:
    """Require Bearer or Anthropic ``x-api-key`` proxy authentication."""
    client_ip = _get_client_ip(request)

    if _is_ip_blocked(client_ip):
        log_security_event("blocked_ip_auth_attempt", request, level="warning")
        raise HTTPException(
            status_code=429,
            detail="Too many failed authentication attempts. Try again later.",
        )

    if not settings.proxy_auth_enabled:
        return

    authorization = request.headers.get("authorization")
    if authorization is not None:
        if _proxy_token_matches(
            authorization,
            settings.proxy_auth_token,
            require_bearer=True,
        ):
            return
        _record_failed_auth(client_ip)
        log_security_event("invalid_auth_token_bearer", request, level="warning")
        raise HTTPException(
            status_code=401,
            detail="Invalid proxy authentication token",
        )

    x_api_key = request.headers.get("x-api-key")
    if x_api_key is None:
        _record_failed_auth(client_ip)
        log_security_event("missing_auth_token", request, level="warning")
        raise HTTPException(
            status_code=401,
            detail="Missing proxy authentication token",
        )

    if not _proxy_token_matches(
        x_api_key,
        settings.proxy_auth_token,
        require_bearer=False,
    ):
        _record_failed_auth(client_ip)
        log_security_event("invalid_auth_token_apikey", request, level="warning")
        raise HTTPException(
            status_code=401,
            detail="Invalid proxy authentication token",
        )


def require_admin_token(
    request: Request,
    settings: Settings = Depends(get_settings),
) -> None:
    """Optional dedicated admin API token (separate from proxy token).

    When ``FCC_ADMIN_API_TOKEN`` is empty, this is a no-op (loopback/IP
    controls remain the primary admin boundary). When set, admin JSON APIs
    must present the token as Bearer or ``X-FCC-Admin-Token``.
    """
    token = (getattr(settings, "admin_api_token", None) or "").strip()
    if not token:
        return

    client_ip = _get_client_ip(request)
    if _is_ip_blocked(client_ip):
        log_security_event("blocked_ip_admin_auth", request, level="warning")
        raise HTTPException(
            status_code=429,
            detail="Too many failed authentication attempts. Try again later.",
        )

    provided = request.headers.get("x-fcc-admin-token")
    if not provided:
        authorization = request.headers.get("authorization")
        if authorization:
            parts = authorization.strip().split(maxsplit=1)
            if len(parts) == 2 and parts[0].casefold() == "bearer":
                provided = parts[1].strip()

    if not provided or not secrets.compare_digest(
        provided.encode("utf-8"),
        token.encode("utf-8"),
    ):
        _record_failed_auth(client_ip)
        log_security_event("invalid_admin_token", request, level="warning")
        raise HTTPException(status_code=401, detail="Invalid admin authentication token")


def _proxy_token_matches(
    credential: str,
    configured_token: str,
    *,
    require_bearer: bool,
) -> bool:
    token = credential.strip()
    if require_bearer:
        parts = token.split(maxsplit=1)
        if len(parts) != 2 or parts[0].casefold() != "bearer":
            return False
        token = parts[1].strip()

    return bool(token) and secrets.compare_digest(
        token.encode("utf-8"),
        configured_token.encode("utf-8"),
    )


# Silence unused import warning if time only used historically
_ = time
