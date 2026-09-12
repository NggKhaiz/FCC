"""Security boundary for Admin product surfaces - hardened, remote-friendly."""

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
    local_only = os.getenv("FCC_ADMIN_LOCAL_ONLY", "").lower()
    if local_only in {"1", "true", "yes", "on"}:
        return False

    allow_remote = os.getenv("FCC_ALLOW_REMOTE_ADMIN", "").lower()
    if allow_remote in {"0", "false", "no", "off"}:
        return False
    if allow_remote in {"1", "true", "yes", "on"}:
        return True

    # Default: allow remote access - security via PROXY_AUTH_ENABLED + token
    return True


def _is_ip_allowed(client_host: str | None) -> bool:
    """Check IP allowlist if configured."""
    allowlist = os.getenv("FCC_ADMIN_IP_ALLOWLIST", "").strip()
    if not allowlist:
        return True  # No allowlist = allow all
    
    if not client_host:
        return False
    
    allowed_ips = [ip.strip() for ip in allowlist.split(",") if ip.strip()]
    for allowed in allowed_ips:
        try:
            # Support CIDR notation
            if "/" in allowed:
                network = ipaddress.ip_network(allowed, strict=False)
                if ipaddress.ip_address(client_host) in network:
                    return True
            else:
                # Exact match or loopback check
                if allowed.lower() == "localhost" and _is_loopback_host(client_host):
                    return True
                if client_host == allowed:
                    return True
                # Try as IP
                if ipaddress.ip_address(client_host) == ipaddress.ip_address(allowed):
                    return True
        except ValueError:
            # Invalid allowlist entry, skip
            continue
    return False


def require_loopback_admin(request: Request) -> None:
    """Allow Admin access from anywhere by default, with optional restrictions.

    Security model:
    - Remote access allowed by default (FCC_ALLOW_REMOTE_ADMIN=true)
    - Set FCC_ADMIN_LOCAL_ONLY=1 to re-enable strict local-only
    - Set FCC_ADMIN_IP_ALLOWLIST to restrict by IP (CIDR supported)
    - When PROXY_AUTH_ENABLED, bearer token required for sensitive operations
    - Audit logging for remote access
    """

    client_host = request.client.host if request.client else None
    forwarded = request.headers.get("x-forwarded-for")
    real_ip = forwarded.split(",")[0].strip() if forwarded else client_host

    # IP allowlist check (strongest)
    if not _is_ip_allowed(real_ip or client_host):
        logger.warning(
            "Admin access denied by IP allowlist: ip={} path={}",
            real_ip or client_host,
            request.url.path,
        )
        raise HTTPException(
            status_code=403,
            detail="Admin access denied by IP allowlist",
        )

    # If remote admin is allowed, skip loopback checks but log
    if _is_remote_admin_allowed():
        if client_host and not _is_loopback_host(client_host):
            logger.info(
                "Admin remote access: ip={} path={} method={}",
                real_ip or client_host,
                request.url.path,
                request.method,
            )
        return

    # Strict local-only mode (when FCC_ADMIN_LOCAL_ONLY=1)
    if not _is_loopback_host(client_host):
        logger.warning(
            "Admin local-only violation: ip={} path={}",
            client_host,
            request.url.path,
        )
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

