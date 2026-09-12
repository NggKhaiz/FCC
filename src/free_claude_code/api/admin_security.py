"""Security boundary for Admin product surfaces - now remote-friendly."""

import ipaddress
import os
from urllib.parse import urlsplit

from fastapi import HTTPException, Request
from loguru import logger


def _is_loopback_host(host: str | None) -> bool:
    if host is None:
        return False
    normalized = host.strip().strip("[]").lower()
    if normalized == "localhost":
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


def _origin_is_local(origin: str | None) -> bool:
    if not origin:
        return True
    try:
        parsed = urlsplit(origin)
        _ = parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme in {"http", "https"}
        and parsed.username is None
        and parsed.password is None
        and not parsed.path
        and not parsed.query
        and not parsed.fragment
        and _is_loopback_host(parsed.hostname)
    )


def _authority_is_local(authority: str | None) -> bool:
    if not authority:
        return False
    try:
        parsed = urlsplit(f"//{authority}")
        _ = parsed.port
    except ValueError:
        return False
    return (
        parsed.username is None
        and parsed.password is None
        and not parsed.path
        and not parsed.query
        and not parsed.fragment
        and _is_loopback_host(parsed.hostname)
    )


def _is_remote_admin_allowed() -> bool:
    """Check if remote admin access is allowed.

    Priority:
    1. FCC_ADMIN_LOCAL_ONLY env var (if set to 1/true, enforce local-only)
    2. FCC_ALLOW_REMOTE_ADMIN env var (if set to 1/true, allow remote)
    3. Default: allow remote (modern deployment friendly)
    """
    # Legacy enforcement flag - if explicitly set to true, enforce local-only
    local_only = os.getenv("FCC_ADMIN_LOCAL_ONLY", "").lower()
    if local_only in {"1", "true", "yes", "on"}:
        return False

    # Explicit allow flag
    allow_remote = os.getenv("FCC_ALLOW_REMOTE_ADMIN", "").lower()
    if allow_remote in {"0", "false", "no", "off"}:
        return False
    if allow_remote in {"1", "true", "yes", "on"}:
        return True

    # Default: allow remote access for better UX
    # Security is handled via PROXY_AUTH_ENABLED + token
    return True


def require_loopback_admin(request: Request) -> None:
    """Allow Admin access from anywhere by default, with optional local-only enforcement.

    Security model:
    - Remote access is allowed by default (FCC_ALLOW_REMOTE_ADMIN=true)
    - Set FCC_ADMIN_LOCAL_ONLY=1 to re-enable strict local-only mode
    - When PROXY_AUTH_ENABLED is true, bearer token is still required for API
    - Admin UI itself is protected by origin checks only when local-only mode is active
    """

    # If remote admin is allowed, skip all loopback checks
    if _is_remote_admin_allowed():
        # Still log remote access for audit
        client_host = request.client.host if request.client else "unknown"
        if not _is_loopback_host(client_host):
            logger.debug(
                "Admin accessed remotely from {} path={}",
                client_host,
                request.url.path,
            )
        return

    # Strict local-only mode (when FCC_ADMIN_LOCAL_ONLY=1)
    client_host = request.client.host if request.client else None
    if not _is_loopback_host(client_host):
        raise HTTPException(
            status_code=403,
            detail="Admin UI is local-only (set FCC_ALLOW_REMOTE_ADMIN=1 to allow remote)",
        )
    if not _authority_is_local(request.headers.get("host")):
        raise HTTPException(status_code=403, detail="Admin UI is local-only")
    if not _origin_is_local(request.headers.get("origin")):
        raise HTTPException(status_code=403, detail="Admin UI is local-only")


def require_admin_access(request: Request) -> None:
    """Alias for require_loopback_admin - now remote-friendly."""
    return require_loopback_admin(request)
