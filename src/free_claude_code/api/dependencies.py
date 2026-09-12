"""FastAPI dependencies for the explicit runtime service boundary - hardened."""

import secrets
import time
from collections import defaultdict

from fastapi import Depends, HTTPException, Request
from loguru import logger

from free_claude_code.application.errors import UnknownProviderError
from free_claude_code.application.ports import ProviderPort, RequestRuntimeLease
from free_claude_code.config.provider_catalog import PROVIDER_CATALOG
from free_claude_code.config.settings import Settings

from .ports import ApiServices
from .security import log_security_event

# Track failed auth attempts for brute force protection
_failed_auth_attempts: dict[str, list[float]] = defaultdict(list)
_MAX_FAILED_ATTEMPTS = 10
_FAILED_WINDOW = 300  # 5 minutes
_BLOCK_DURATION = 600  # 10 minutes
_blocked_ips: dict[str, float] = {}


def _get_client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _is_ip_blocked(client_ip: str) -> bool:
    blocked_until = _blocked_ips.get(client_ip, 0)
    if time.monotonic() < blocked_until:
        return True
    # Cleanup expired blocks
    if client_ip in _blocked_ips:
        del _blocked_ips[client_ip]
    return False


def _record_failed_auth(client_ip: str) -> None:
    now = time.monotonic()
    # Clean old attempts
    _failed_auth_attempts[client_ip] = [
        t for t in _failed_auth_attempts[client_ip] if now - t < _FAILED_WINDOW
    ]
    _failed_auth_attempts[client_ip].append(now)
    
    if len(_failed_auth_attempts[client_ip]) >= _MAX_FAILED_ATTEMPTS:
        _blocked_ips[client_ip] = now + _BLOCK_DURATION
        logger.warning(
            "IP blocked due to brute force: ip={} attempts={}",
            client_ip,
            len(_failed_auth_attempts[client_ip]),
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
