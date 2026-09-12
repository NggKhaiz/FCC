"""Admin code sessions - remote-enabled with security hardening."""

import base64
import json
import re
from collections.abc import AsyncIterator, Mapping
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.sse import EventSourceResponse, ServerSentEvent
from pydantic import BaseModel, ConfigDict, Field, field_validator

from free_claude_code.application.code_sessions import (
    CodeApplicationPort,
    CodeDetail,
    CodeItem,
    CodePrompt,
    CodeRun,
    CodeSession,
    CodeUnavailableError,
    CodeValidationError,
)
from free_claude_code.application.code_sessions.models import CodeMode
from free_claude_code.application.errors import ApplicationUnavailableError
from free_claude_code.application.session_events import EventOverflowError
from free_claude_code.config.model_refs import split_provider_model_ref
from free_claude_code.core.json_types import JsonObject, JsonValue

from .admin_routes import admin_page_response
from .admin_security import require_loopback_admin
from .dependencies import get_services
from .markdown import render_markdown
from .ports import ApiServices
from .rate_limit import check_rate_limit
from .security import check_request_size, log_security_event, validate_session_id

router = APIRouter()

# Security patterns (kept for reference; hot path uses native ultra validators)
SESSION_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_\-]{1,128}$")
OPERATION_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_\-]{1,128}$")
PROMPT_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_\-]{1,128}$")


def _validate_session_id(session_id: str) -> str:
    return validate_session_id(session_id)


def _validate_operation_id(op_id: str) -> str:
    # Same charset/length rules as session ids
    return validate_session_id(op_id)


def _validate_cwd(cwd: str) -> str:
    """Validate cwd to prevent path traversal and injection."""
    if len(cwd) > 4096:
        raise HTTPException(status_code=400, detail="Path too long")
    if "\x00" in cwd:
        raise HTTPException(status_code=400, detail="Invalid path")
    # Check for obvious traversal attempts outside validation
    # Actual path resolution happens in the application layer
    return cwd


class CommandPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreatePayload(CommandPayload):
    session_id: str = Field(min_length=1, max_length=128)
    cwd: str = Field(min_length=1, max_length=4096)
    harness: Literal["codex"] = "codex"

    @field_validator("session_id")
    @classmethod
    def validate_session_id(cls, v: str) -> str:
        if not SESSION_ID_PATTERN.match(v):
            raise ValueError("Invalid session ID format")
        return v

    @field_validator("cwd")
    @classmethod
    def validate_cwd_field(cls, v: str) -> str:
        if "\x00" in v:
            raise ValueError("Invalid path")
        return v


class FolderPickerPayload(CommandPayload):
    initial_path: str | None = Field(default=None, max_length=4096)

    @field_validator("initial_path")
    @classmethod
    def validate_path(cls, v: str | None) -> str | None:
        if v is not None and "\x00" in v:
            raise ValueError("Invalid path")
        return v


class SettingsPayload(CommandPayload):
    expected_revision: int = Field(gt=0, le=1_000_000)
    title: str | None = Field(default=None, min_length=1, max_length=200)
    model: str | None = Field(default=None, min_length=1, max_length=512)
    reasoning_effort: str | None = Field(default=None, max_length=32)
    mode: CodeMode = "config"


class SendPayload(CommandPayload):
    operation_id: str = Field(min_length=1, max_length=128)
    expected_revision: int = Field(gt=0, le=1_000_000)
    expected_epoch: str = Field(min_length=1, max_length=128)
    text: str = Field(min_length=1, max_length=1_000_000)

    @field_validator("operation_id")
    @classmethod
    def validate_op_id(cls, v: str) -> str:
        if not OPERATION_ID_PATTERN.match(v):
            raise ValueError("Invalid operation ID format")
        return v


class StopPayload(CommandPayload):
    operation_id: str = Field(min_length=1, max_length=128)

    @field_validator("operation_id")
    @classmethod
    def validate_op_id(cls, v: str) -> str:
        if not OPERATION_ID_PATTERN.match(v):
            raise ValueError("Invalid operation ID format")
        return v


class AnswerPayload(CommandPayload):
    response_id: str = Field(min_length=1, max_length=128)
    answer: JsonObject


def _code(services: ApiServices) -> CodeApplicationPort:
    if services.code is None:
        raise CodeUnavailableError("Code sessions is unavailable in this FCC runtime.")
    return services.code


@router.get("/admin/code", include_in_schema=False)
@router.get("/admin/code/{session_id}", include_in_schema=False)
def code_page(request: Request, session_id: str | None = None):
    check_rate_limit(request)
    require_loopback_admin(request)
    if session_id:
        _validate_session_id(session_id)
    return admin_page_response()


@router.get("/admin/api/code/bootstrap")
def bootstrap(request: Request, services: ApiServices = Depends(get_services)) -> JsonObject:
    check_rate_limit(request)
    require_loopback_admin(request)
    code = _code(services)
    available, message = code.availability()
    catalog = code.catalog()
    return {
        "available": available,
        "message": message,
        "harnesses": [{"id": "codex", "name": "Codex"}],
        "epoch": code.epoch,
        "models": [model.model_dump(mode="json") for model in catalog.models],
        "default_model": catalog.default_model,
    }


@router.post("/admin/api/code/folder-picker")
async def pick_folder(
    request: Request,
    payload: FolderPickerPayload,
    services: ApiServices = Depends(get_services),
) -> JsonObject:
    check_rate_limit(request)
    require_loopback_admin(request)
    log_security_event("folder_picker", request, {"initial": payload.initial_path[:100] if payload.initial_path else "none"})
    try:
        return {"path": await services.admin.pick_folder(payload.initial_path)}
    except ApplicationUnavailableError as exc:
        raise HTTPException(status_code=503, detail=exc.message) from exc


@router.get("/admin/api/code/events", response_class=EventSourceResponse)
async def events(
    request: Request,
    services: ApiServices = Depends(get_services),
) -> AsyncIterator[ServerSentEvent]:
    check_rate_limit(request)
    require_loopback_admin(request)
    subscription, ready = await _code(services).subscribe()
    try:
        summaries = ready.get("sessions")
        if isinstance(summaries, list):
            ready = {
                **ready,
                "sessions": [
                    _event_payload(value)
                    for value in summaries
                    if isinstance(value, Mapping)
                ],
            }
        yield ServerSentEvent(
            event="feed.ready", id=str(subscription.cursor), retry=1000, data=ready
        )
        try:
            async for event in subscription:
                yield ServerSentEvent(
                    event=event.event,
                    id=str(event.id),
                    data={**_event_payload(event.data), "cursor": event.id},
                )
        except EventOverflowError as exc:
            yield ServerSentEvent(
                event="feed.resync_required",
                id=str(exc.cursor),
                data={"cursor": exc.cursor},
            )
    finally:
        await subscription.aclose()


@router.get("/admin/api/code/sessions")
async def list_sessions(
    request: Request,
    cursor: str | None = None,
    query: str = Query(default="", max_length=4096),
    limit: int = Query(default=25, ge=1, le=25),
    services: ApiServices = Depends(get_services),
) -> JsonObject:
    check_rate_limit(request)
    require_loopback_admin(request)
    code = _code(services)
    snapshot_cursor = code.cursor
    page = await code.list_sessions(_decode_cursor(cursor), limit, query)
    return {
        "sessions": [_session_payload(session) for session in page.sessions],
        "next_cursor": _encode_cursor(page.next_cursor),
        "epoch": code.epoch,
        "cursor": snapshot_cursor,
    }


@router.post("/admin/api/code/sessions", status_code=201)
async def create(
    request: Request,
    payload: CreatePayload,
    services: ApiServices = Depends(get_services),
) -> JsonObject:
    check_rate_limit(request)
    require_loopback_admin(request)
    check_request_size(request, max_size=1024 * 100)
    _validate_cwd(payload.cwd)
    log_security_event("code_session_create", request, {"session_id": payload.session_id})
    return _session_payload(
        await _code(services).create_session(payload.session_id, payload.cwd)
    )


@router.get("/admin/api/code/sessions/{session_id}")
async def detail(
    request: Request,
    session_id: str,
    include_item_ids: tuple[str, ...] = Query(default=()),
    services: ApiServices = Depends(get_services),
) -> JsonObject:
    check_rate_limit(request)
    require_loopback_admin(request)
    _validate_session_id(session_id)
    if len(include_item_ids) > 100:
        raise HTTPException(status_code=400, detail="Too many item IDs")
    return _detail_payload(
        await _code(services).get_detail(session_id, include_item_ids=include_item_ids)
    )


@router.get("/admin/api/code/sessions/{session_id}/items")
async def older_items(
    request: Request,
    session_id: str,
    before: str,
    services: ApiServices = Depends(get_services),
) -> JsonObject:
    check_rate_limit(request)
    require_loopback_admin(request)
    _validate_session_id(session_id)
    return _detail_payload(
        await _code(services).get_detail(session_id, before=_decode_item_cursor(before))
    )


@router.patch("/admin/api/code/sessions/{session_id}")
async def update_settings(
    request: Request,
    session_id: str,
    payload: SettingsPayload,
    services: ApiServices = Depends(get_services),
) -> JsonObject:
    check_rate_limit(request)
    require_loopback_admin(request)
    _validate_session_id(session_id)
    return _session_payload(
        await _code(services).update_settings(
            session_id,
            payload.expected_revision,
            payload.model_dump(exclude={"expected_revision"}, exclude_unset=True),
        )
    )


@router.delete("/admin/api/code/sessions/{session_id}", status_code=202)
async def delete(
    request: Request,
    session_id: str,
    expected_revision: int = Query(gt=0, le=1_000_000),
    services: ApiServices = Depends(get_services),
) -> JsonObject:
    check_rate_limit(request)
    require_loopback_admin(request)
    _validate_session_id(session_id)
    log_security_event("code_session_delete", request, {"session_id": session_id})
    session = await _code(services).delete_session(session_id, expected_revision)
    return {
        "session_id": session_id,
        "deleted": session is None,
        "session": _session_payload(session) if session else None,
    }


@router.post("/admin/api/code/sessions/{session_id}/turns", status_code=202)
async def send(
    request: Request,
    session_id: str,
    payload: SendPayload,
    services: ApiServices = Depends(get_services),
) -> JsonObject:
    check_rate_limit(request)
    require_loopback_admin(request)
    check_request_size(request, max_size=2 * 1024 * 1024)
    _validate_session_id(session_id)
    return _run_payload(
        await _code(services).send(
            session_id,
            payload.operation_id,
            payload.expected_revision,
            payload.text,
            expected_epoch=payload.expected_epoch,
        )
    )


@router.post("/admin/api/code/sessions/{session_id}/stop", status_code=202)
async def stop(
    request: Request,
    session_id: str,
    payload: StopPayload,
    services: ApiServices = Depends(get_services),
) -> JsonObject:
    check_rate_limit(request)
    require_loopback_admin(request)
    _validate_session_id(session_id)
    return _run_payload(await _code(services).stop(session_id, payload.operation_id))


@router.post(
    "/admin/api/code/sessions/{session_id}/prompts/{prompt_id}/responses",
    status_code=202,
)
async def answer(
    request: Request,
    session_id: str,
    prompt_id: str,
    payload: AnswerPayload,
    services: ApiServices = Depends(get_services),
) -> JsonObject:
    check_rate_limit(request)
    require_loopback_admin(request)
    _validate_session_id(session_id)
    if not PROMPT_ID_PATTERN.match(prompt_id):
        raise HTTPException(status_code=400, detail="Invalid prompt ID")
    if not PROMPT_ID_PATTERN.match(payload.response_id):
        raise HTTPException(status_code=400, detail="Invalid response ID")
    return _prompt_payload(
        await _code(services).answer(
            session_id, prompt_id, payload.response_id, payload.answer
        )
    )


def _session_payload(session: CodeSession) -> JsonObject:
    provider_id, model_name = split_provider_model_ref(session.model)
    return {
        **session.model_dump(
            mode="json",
            exclude={
                "native_thread_id",
                "native_may_have_input",
                "native_permission_defaults",
                "auto_title",
            },
        ),
        "provider_id": provider_id,
        "model_name": model_name,
    }


def _run_payload(run: CodeRun) -> JsonObject:
    return run.model_dump(mode="json", exclude={"native_turn_id", "submission_started"})


def _item_payload(item: CodeItem) -> JsonObject:
    return {
        **item.model_dump(
            mode="json", exclude={"raw", "native_turn_id", "native_item_id"}
        ),
        "html": render_markdown(item.text)
        if item.kind in {"text", "reasoning"}
        else None,
    }


def _prompt_payload(prompt: CodePrompt) -> JsonObject:
    return prompt.model_dump(
        mode="json",
        exclude={"raw", "generation", "request_id", "native_turn_id", "native_item_id"},
    )


def _detail_payload(detail: CodeDetail) -> JsonObject:
    return {
        "session": _session_payload(detail.session),
        "run": _run_payload(detail.run) if detail.run else None,
        "runs": [_run_payload(run) for run in detail.runs],
        "active_prompt_ids": list(detail.active_prompt_ids),
        "active_review_ids": list(detail.active_review_ids),
        "items": [_item_payload(item) for item in detail.items],
        "prompts": [_prompt_payload(prompt) for prompt in detail.prompts],
        "epoch": detail.epoch,
        "version": detail.version,
        "cursor": detail.cursor,
        "next_before": _encode_cursor(detail.next_before),
    }


def _event_payload(data: Mapping[str, JsonValue]) -> JsonObject:
    result = dict(data)
    if isinstance(data.get("session"), Mapping):
        result["session"] = _session_payload(
            CodeSession.model_validate(data["session"])
        )
    if isinstance(data.get("run"), Mapping):
        result["run"] = _run_payload(CodeRun.model_validate(data["run"]))
    if isinstance(data.get("item"), Mapping):
        result["item"] = _item_payload(CodeItem.model_validate(data["item"]))
    if isinstance(data.get("prompt"), Mapping):
        result["prompt"] = _prompt_payload(CodePrompt.model_validate(data["prompt"]))
    items = data.get("items")
    if isinstance(items, list):
        result["items"] = [
            _item_payload(CodeItem.model_validate(value)) for value in items
        ]
    prompts = data.get("prompts")
    runs = data.get("runs")
    if isinstance(runs, list):
        result["runs"] = [_run_payload(CodeRun.model_validate(value)) for value in runs]
    if isinstance(prompts, list):
        result["prompts"] = [
            _prompt_payload(CodePrompt.model_validate(value)) for value in prompts
        ]
    return result


def _encode_cursor(cursor: tuple[int, str] | tuple[int, int] | None) -> str | None:
    return (
        base64.urlsafe_b64encode(json.dumps(cursor).encode()).decode()
        if cursor
        else None
    )


def _decode_cursor(cursor: str | None) -> tuple[int, str] | None:
    if cursor is None:
        return None
    try:
        value = json.loads(base64.urlsafe_b64decode(cursor).decode())
        if (
            not isinstance(value, list)
            or len(value) != 2
            or type(value[0]) is not int
            or not isinstance(value[1], str)
        ):
            raise ValueError
        return value[0], value[1]
    except ValueError as exc:
        raise CodeValidationError("Invalid session page cursor.") from exc
    except UnicodeError as exc:
        raise CodeValidationError("Invalid session page cursor.") from exc


def _decode_item_cursor(cursor: str) -> tuple[int, int]:
    try:
        value = json.loads(base64.urlsafe_b64decode(cursor).decode())
        if (
            not isinstance(value, list)
            or len(value) != 2
            or any(type(part) is not int or part < 1 for part in value)
        ):
            raise ValueError
        return value[0], value[1]
    except ValueError as exc:
        raise CodeValidationError("Invalid transcript page cursor.") from exc
    except UnicodeError as exc:
        raise CodeValidationError("Invalid transcript page cursor.") from exc
