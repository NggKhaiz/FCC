"""Rate limiting middleware for security."""

import time
from collections import defaultdict, deque
from dataclasses import dataclass

from fastapi import HTTPException, Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send
from loguru import logger


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
    """Simple in-memory rate limiter with sliding window."""

    def __init__(self) -> None:
        self._requests: dict[str, deque[float]] = defaultdict(deque)
        self._blocked_until: dict[str, float] = {}

    def is_allowed(self, key: str, config: RateLimitConfig) -> tuple[bool, int]:
        now = time.monotonic()
        
        # Check if blocked
        blocked_until = self._blocked_until.get(key, 0)
        if now < blocked_until:
            remaining = int(blocked_until - now)
            return False, remaining
        
        # Clean old entries
        window_start = now - config.window_seconds
        queue = self._requests[key]
        while queue and queue[0] < window_start:
            queue.popleft()
        
        # Check limit
        if len(queue) >= config.max_requests:
            self._blocked_until[key] = now + config.block_seconds
            logger.warning(
                "Rate limit exceeded for {} - blocking for {}s",
                key,
                config.block_seconds,
            )
            return False, config.block_seconds
        
        queue.append(now)
        return True, 0

    def cleanup(self) -> None:
        """Remove stale entries to prevent memory leak."""
        now = time.monotonic()
        stale_keys = [
            key for key, queue in self._requests.items()
            if not queue or (queue and now - queue[-1] > 3600)
        ]
        for key in stale_keys:
            del self._requests[key]
            self._blocked_until.pop(key, None)


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
