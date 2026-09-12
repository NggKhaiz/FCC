"""Phase 5: admin token, security ring, json helpers, auth window."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from free_claude_code.native import (
    json_dumps_compact,
    json_loads_any,
    json_loads_object,
    security_events,
)
from free_claude_code.native.ultra import SlidingWindow
from tests.api.support import create_test_app


def _local_client(app):
    return TestClient(
        app,
        base_url="http://127.0.0.1",
        client=("127.0.0.1", 50000),
    )


def test_json_helpers_roundtrip():
    payload = {"a": 1, "b": ["x", True], "c": None}
    s = json_dumps_compact(payload)
    assert " " not in s
    assert json_loads_object(s) == payload
    assert json_loads_any("not-json{") == "not-json{"
    with pytest.raises(ValueError):
        json_loads_object("[1,2]")


def test_security_event_ring():
    security_events.append("unit_test_event", client_ip="127.0.0.1", path="/t")
    snap = security_events.snapshot(limit=5)
    assert snap
    assert snap[0]["event"] == "unit_test_event"
    assert security_events.latest_seq() >= 1


def test_sliding_window_block_api():
    w = SlidingWindow()
    assert not w.is_blocked("x")
    w.block("x", 2.0)
    assert w.is_blocked("x")
    w.clear_block("x")
    assert not w.is_blocked("x")


def test_security_events_endpoint():
    client = _local_client(create_test_app())
    # Generate an event via admin page access
    client.get("/admin")
    r = client.get("/admin/api/security/events?limit=10")
    assert r.status_code == 200
    body = r.json()
    assert "events" in body
    assert "latest_seq" in body


def test_admin_token_optional_by_default(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("FCC_ADMIN_API_TOKEN", raising=False)
    client = _local_client(create_test_app())
    assert client.get("/admin/api/config").status_code == 200


def test_admin_token_enforced_when_set(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FCC_ADMIN_API_TOKEN", "super-admin-token-xyz")
    client = _local_client(create_test_app())
    denied = client.get("/admin/api/config")
    assert denied.status_code == 401
    ok = client.get(
        "/admin/api/config",
        headers={"X-FCC-Admin-Token": "super-admin-token-xyz"},
    )
    assert ok.status_code == 200
    ok2 = client.get(
        "/admin/api/config",
        headers={"Authorization": "Bearer super-admin-token-xyz"},
    )
    assert ok2.status_code == 200


def test_security_audit_reports_admin_token_flag(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FCC_ADMIN_API_TOKEN", "tok")
    client = _local_client(create_test_app())
    # audit itself requires token when set
    r = client.get(
        "/admin/api/security/audit",
        headers={"X-FCC-Admin-Token": "tok"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body.get("admin_api_token_configured") is True
    assert body.get("security_event_ring") is True
    assert body.get("native_backend") in {"python", "rust"}
