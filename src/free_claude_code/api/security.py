"""Additional security utilities and hardening."""

import re
import secrets
import hashlib
from pathlib import Path
from typing import Annotated

from fastapi import Request, HTTPException
from pydantic import StringConstraints
from loguru import logger

from free_claude_code.native import (
    sanitize_log_fast,
    validate_model_ref_fast,
    validate_provider_id_fast,
    validate_session_id_fast,
)


# Input validation patterns (kept for external/docs; hot path uses native ultra)
SAFE_PATH_PATTERN = re.compile(r"^[a-zA-Z0-9/_\-\.]+$")
PROVIDER_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")
MODEL_REF_PATTERN = re.compile(r"^[a-z0-9_]+/[a-zA-Z0-9/_\-\.:]+$")
SESSION_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_\-]{1,128}$")


def validate_provider_id(provider_id: str) -> str:
    """Validate provider ID to prevent injection (native ultra-fast path)."""
    if not isinstance(provider_id, str) or not validate_provider_id_fast(provider_id):
        raise HTTPException(status_code=400, detail="Invalid provider ID format")
    return provider_id


def validate_model_ref(model_ref: str) -> str:
    """Validate model reference (native ultra-fast path)."""
    if not isinstance(model_ref, str) or not validate_model_ref_fast(model_ref):
        raise HTTPException(status_code=400, detail="Invalid model reference format")
    return model_ref


def validate_session_id(session_id: str) -> str:
    """Validate session / code-session id."""
    if not isinstance(session_id, str) or not validate_session_id_fast(session_id):
        raise HTTPException(status_code=400, detail="Invalid session ID format")
    return session_id


def validate_file_path(path: str, allowed_base: Path | None = None) -> Path:
    """Validate file path to prevent directory traversal."""
    # Check for traversal patterns
    if ".." in path or path.startswith("/"):
        # Allow absolute paths only if within allowed_base
        if allowed_base is None:
            raise HTTPException(status_code=400, detail="Invalid path")
    
    # Check for null bytes and control chars
    if "\x00" in path or any(ord(c) < 32 and c not in "\r\n\t" for c in path):
        raise HTTPException(status_code=400, detail="Invalid path characters")
    
    try:
        resolved = Path(path).resolve()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid path")
    
    if allowed_base:
        try:
            resolved.relative_to(allowed_base.resolve())
        except ValueError:
            raise HTTPException(status_code=403, detail="Path outside allowed directory")
    
    return resolved


def sanitize_log_value(value: str, max_length: int = 200) -> str:
    """Sanitize value for logging to prevent log injection (native fast path)."""
    if not isinstance(value, str):
        value = str(value)
    return sanitize_log_fast(value, max_length)


def generate_secure_token(length: int = 32) -> str:
    """Generate a cryptographically secure token."""
    return secrets.token_urlsafe(length)


def hash_token(token: str) -> str:
    """Hash token for storage comparison (prevent timing attacks)."""
    return hashlib.sha256(token.encode()).hexdigest()


def check_request_size(request: Request, max_size: int = 10 * 1024 * 1024) -> None:
    """Check request content length."""
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            size = int(content_length)
            if size > max_size:
                raise HTTPException(
                    status_code=413,
                    detail=f"Request too large (max {max_size // 1024 // 1024}MB)",
                )
        except ValueError:
            pass


def log_security_event(
    event: str,
    request: Request | None = None,
    details: dict | None = None,
    level: str = "warning",
) -> None:
    """Log security-relevant events for audit + ring buffer for live tail."""
    from free_claude_code.native import security_events

    client_ip = "unknown"
    path = "unknown"
    method = "unknown"
    if request:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            client_ip = forwarded.split(",")[0].strip()
        elif request.client:
            client_ip = request.client.host
        path = request.url.path
        method = request.method

    ring_fields: dict = {"client_ip": client_ip, "path": path, "method": method, "level": level}
    if details:
        for k, v in list(details.items())[:20]:
            ring_fields[str(k)[:64]] = v
    try:
        security_events.append(event, **ring_fields)
    except Exception:
        pass

    if level == "warning":
        logger.warning("Security: {} ip={} path={}", event, client_ip, path)
    elif level == "error":
        logger.error("Security: {} ip={} path={}", event, client_ip, path)
    else:
        logger.info("Security: {} ip={} path={}", event, client_ip, path)


# Pydantic constrained types for extra validation
SafeProviderId = Annotated[
    str,
    StringConstraints(
        pattern=r"^[a-z][a-z0-9_]*$",
        min_length=1,
        max_length=64,
        strip_whitespace=True,
    ),
]

SafeModelRef = Annotated[
    str,
    StringConstraints(
        min_length=1,
        max_length=512,
        strip_whitespace=True,
    ),
]

SafePath = Annotated[
    str,
    StringConstraints(
        min_length=1,
        max_length=4096,
        strip_whitespace=True,
    ),
]


def validate_admin_origin(request: Request) -> bool:
    """Validate origin for admin requests (CSRF protection)."""
    origin = request.headers.get("origin")
    if not origin:
        return True  # No origin = same-origin or non-browser
    
    # Allow same host or localhost
    host = request.headers.get("host", "")
    try:
        from urllib.parse import urlparse
        origin_host = urlparse(origin).netloc
        # Allow same host
        if origin_host == host:
            return True
        # Allow localhost variations
        if origin_host in ("localhost", "127.0.0.1", "::1") or origin_host.startswith("localhost:") or origin_host.startswith("127.0.0.1:"):
            return True
        # For remote admin, allow any origin if remote allowed (but log)
        from .admin_security import _is_remote_admin_allowed
        if _is_remote_admin_allowed():
            return True
    except Exception:
        pass
    
    return False
