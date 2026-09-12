"""ASGI middleware that records request metrics."""

import time

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .metrics import metrics


class MetricsMiddleware:
    """Record method/path/status/duration for every HTTP request."""

    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        method = scope.get("method", "GET")
        path = scope.get("path", "/")
        client = None
        if scope.get("client"):
            client = scope["client"][0]
        # Prefer first X-Forwarded-For hop when present (set by reverse proxy)
        for name, value in scope.get("headers") or ():
            if name == b"x-forwarded-for":
                try:
                    client = value.decode("latin-1").split(",")[0].strip() or client
                except Exception:
                    pass
                break

        started = time.perf_counter()
        status_code = 500

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = int(message.get("status", 500))
            await send(message)

        try:
            await self._app(scope, receive, send_wrapper)
        finally:
            duration_ms = (time.perf_counter() - started) * 1000.0
            try:
                metrics.record_request(
                    method=method,
                    path=path,
                    status=status_code,
                    duration_ms=duration_ms,
                    client=client,
                )
            except Exception:
                # Metrics must never break the request path
                pass
