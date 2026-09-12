"""Security headers middleware for hardening."""

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send


class SecurityHeadersMiddleware:
    """Add security headers to all responses."""

    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        path = scope.get("path", "")

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                message = dict(message)
                raw_headers = list(message.get("headers", ()))
                headers = MutableHeaders(raw=raw_headers)
                self._apply_headers(headers, path)
                message["headers"] = raw_headers
            await send(message)

        await self._app(scope, receive, send_with_headers)

    def _apply_headers(self, headers: MutableHeaders, path: str) -> None:
        # Prevent MIME type sniffing
        headers["X-Content-Type-Options"] = "nosniff"
        
        # Prevent clickjacking
        headers["X-Frame-Options"] = "DENY"
        
        # XSS protection (legacy but still useful)
        headers["X-XSS-Protection"] = "0"  # Disable legacy XSS filter, rely on CSP
        
        # Referrer policy
        headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        
        # Permissions policy - disable sensitive features
        headers["Permissions-Policy"] = (
            "camera=(), microphone=(), geolocation=(), "
            "payment=(), usb=(), magnetometer=(), gyroscope=()"
        )
        
        # HSTS - only for HTTPS, but safe to send (browsers ignore on HTTP)
        headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        
        # CSP for admin UI
        if path.startswith("/admin"):
            # Strict CSP for admin - no inline scripts except nonced, no external resources
            csp = (
                "default-src 'self'; "
                "script-src 'self' https://fonts.googleapis.com; "
                "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
                "font-src 'self' https://fonts.gstatic.com; "
                "img-src 'self' data: https:; "
                "connect-src 'self'; "
                "frame-ancestors 'none'; "
                "base-uri 'self'; "
                "form-action 'self';"
            )
            headers["Content-Security-Policy"] = csp
        else:
            # API routes - more permissive but still secure
            headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none';"
        
        # Remove server header disclosure
        if "server" in headers:
            del headers["server"]
        headers["Server"] = "FCC"
        
        # Cache control for API
        if path.startswith("/v1/") or path.startswith("/muse-code/"):
            headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
            headers["Pragma"] = "no-cache"
