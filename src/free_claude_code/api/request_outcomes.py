"""Log one final inference outcome after HTTP delivery and owned cleanup."""

import asyncio
import codecs
from time import monotonic

from loguru import logger
from starlette.datastructures import Headers
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from free_claude_code.core.anthropic.stream_contracts import (
    SSEEvent,
    failure_reason_from_frame,
)
from free_claude_code.core.anthropic.streaming.decoder import AnthropicSSEDecoder
from free_claude_code.core.request_outcomes import (
    RequestOutcome,
    current_request_outcome,
    record_request_exception,
    record_request_failure,
)

from .generation_paths import generation_api

_FAILURE_EVENTS = frozenset({"error", "response.error", "response.failed"})


def _observe_event(event: SSEEvent) -> None:
    kind = (
        event.event
        or event.data.get("type")
        or ("error" if "error" in event.data else None)
    )
    if not isinstance(kind, str) or kind not in _FAILURE_EVENTS:
        return
    response = event.data.get("response")
    payload = response if isinstance(response, dict) else event.data
    error = payload.get("error")
    error = error if isinstance(error, dict) else payload
    reason = error.get("code") or error.get("type")
    record_request_failure(reason if isinstance(reason, str) else str(kind))


class RequestOutcomeMiddleware:
    """Observe inference delivery without changing its body or control flow."""

    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] != "http"
            or scope.get("method") != "POST"
            or generation_api(scope.get("path", "")) is None
        ):
            await self._app(scope, receive, send)
            return

        started = monotonic()
        outcome = RequestOutcome()
        status_code: int | None = None
        completed = disconnected = cancelled = False
        decoder: AnthropicSSEDecoder | None = None
        text_decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")

        async def receive_observed() -> Message:
            nonlocal disconnected
            message = await receive()
            if message["type"] == "http.disconnect":
                disconnected = True
            return message

        async def send_observed(message: Message) -> None:
            nonlocal status_code, completed, decoder, disconnected
            try:
                await send(message)
            except OSError:
                disconnected = True
                raise
            if message["type"] == "http.response.start":
                status_code = message["status"]
                if (
                    Headers(raw=message.get("headers", []))
                    .get("content-type", "")
                    .startswith("text/event-stream")
                ):
                    # Framing only; failure detection uses the cheap text scan.
                    decoder = AnthropicSSEDecoder()
            elif message["type"] == "http.response.body":
                completed = not message.get("more_body", False)
                if decoder is not None:
                    text = text_decoder.decode(
                        message.get("body", b""), final=completed
                    )
                    # Healthy streams never carry failure events, so scan frame
                    # names cheaply and JSON-decode only the rare error frame
                    # instead of parsing every SSE payload a second time.
                    for frame in decoder.feed_frames(text):
                        reason = failure_reason_from_frame(frame, _FAILURE_EVENTS)
                        if reason is not None:
                            record_request_failure(reason)
                    if completed:
                        for frame in decoder.finish_frames():
                            reason = failure_reason_from_frame(frame, _FAILURE_EVENTS)
                            if reason is not None:
                                record_request_failure(reason)

        token = current_request_outcome.set(outcome)
        try:
            await self._app(scope, receive_observed, send_observed)
        except asyncio.CancelledError:
            cancelled = True
            raise
        except BaseException as error:
            if not disconnected:
                record_request_exception(error)
                if status_code is None:
                    status_code = 500
            raise
        finally:
            current_request_outcome.reset(token)
            reason = outcome.failure_reason
            if reason is not None or (status_code is not None and status_code >= 400):
                result = "failure"
                reason = reason or f"http_{status_code}"
            elif (cancelled or disconnected) and not completed:
                result = "cancelled"
            elif not completed:
                result, reason = "failure", "incomplete_response"
            else:
                result = "success"
            logger.bind(
                event="request.completed",
                wire_api=generation_api(scope["path"]),
                provider_id=outcome.provider_id,
                model=outcome.model,
                status_code=status_code,
                outcome=result,
                duration_ms=round((monotonic() - started) * 1000, 2),
                failure_reason=reason,
            ).info("Inference request {}", result)
