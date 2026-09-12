"""Rate limiting middleware for security."""

import time
from dataclasses import dataclass

from fastapi import HTTPException, Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send
from loguru import logger

from free_claude_code.native import SlidingWindow


@dataclass
class RateLimitConfig:
    max_requests: int
    window_seconds: int
    block_seconds: int = 60


# Default configs for different endpoints
ADMIN_RATE_LIMIT = RateLimitConfig(max_requests=100, window_seconds=60, block_seconds=60)
API_RATE_LIMIT = RateLimitConfig(max_requests=300, window_seconds=60, block_seconds=30)
AUTH_RATE_LIMIT = RateLimitConfig(max_requests=20, window_seconds=60, block_seconds=120)


class InMemoryRateLimiter:
    """In-memory rate limiter backed by native SlidingWindow ultra-core."""

    def __init__(self) -> None:
        self._window = SlidingWindow()

    def is_allowed(self, key: str, config: RateLimitConfig) -> tuple[bool, int]:
        allowed, retry_after = self._window.allow(
            key,
            max_requests=config.max_requests,
            window_seconds=float(config.window_seconds),
            block_seconds=float(config.block_seconds),
        )
        if not allowed:
            logger.warning(
                "Rate limit exceeded for {} - blocking for {}s",
                key,
                retry_after or config.block_seconds,
            )
            return False, int(retry_after or config.block_seconds)
        return True, 0

    def cleanup(self) -> None:
        """Remove stale entries to prevent memory leak."""
        self._window.cleanup(stale_after=3600.0)


# Global limiter instance
_limiter = InMemoryRateLimiter()
_last_cleanup = time.monotonic()


def _get_client_key(request: Request) -> str:
    """Get client identifier for rate limiting."""
    # Use X-Forwarded-For if behind proxy, else client host
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        # Take first IP (original client)
        client_ip = forwarded.split(",")[0].strip()
    else:
        client_ip = request.client.host if request.client else "unknown"
    
    # Include path prefix for more granular limiting
    path = request.url.path
    if path.startswith("/admin/api/providers/"):
        # Group by provider
        return f"{client_ip}:provider"
    elif path.startswith("/admin/"):
        return f"{client_ip}:admin"
    elif path.startswith("/v1/"):
        return f"{client_ip}:api"
    return f"{client_ip}:general"


def check_rate_limit(request: Request) -> None:
    """Check rate limit and raise 429 if exceeded."""
    global _last_cleanup
    now = time.monotonic()
    
    # Periodic cleanup every 5 minutes
    if now - _last_cleanup > 300:
        _limiter.cleanup()
        _last_cleanup = now
    
    key = _get_client_key(request)
    path = request.url.path
    
    # Select config based on path
    if "/auth/login" in path or "/auth" in path:
        config = AUTH_RATE_LIMIT
    elif path.startswith("/admin/"):
        config = ADMIN_RATE_LIMIT
    else:
        config = API_RATE_LIMIT
    
    allowed, retry_after = _limiter.is_allowed(key, config)
    if not allowed:
        try:
            from .metrics import metrics as _metrics

            _metrics.record_rate_limit_hit()
        except Exception:
            pass
        raise HTTPException(
            status_code=429,
            detail=f"Too many requests. Retry after {retry_after}s",
            headers={"Retry-After": str(retry_after)},
        )


class RateLimitMiddleware:
    """Middleware to enforce rate limiting."""

    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        # Create a mock request for rate limiting check
        # We need to parse scope to check path
        path = scope.get("path", "")
        
        # Skip rate limiting for health checks
        if path in ("/health", "/"):
            await self._app(scope, receive, send)
            return

        # For actual rate limiting, we check in dependency, but middleware can do basic
        await self._app(scope, receive, send)
