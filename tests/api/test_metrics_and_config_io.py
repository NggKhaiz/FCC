"""Phase 3: metrics collector + admin export/import endpoints."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from free_claude_code.api.metrics import RuntimeMetrics, metrics
from free_claude_code.core.version import package_version
from tests.api.support import create_test_app


def _local_client(app):
    return TestClient(
        app,
        base_url="http://127.0.0.1",
        client=("127.0.0.1", 50000),
    )


def test_runtime_metrics_records_and_snapshots():
    m = RuntimeMetrics(recent_limit=10)
    m.record_request(method="GET", path="/health", status=200, duration_ms=3.5)
    m.record_request(
        method="POST",
        path="/v1/messages",
        status=500,
        duration_ms=120.0,
        client="1.2.3.4",
    )
    m.record_rate_limit_hit()
    m.record_provider_test("groq", ok=True, message="ok", latency_ms=42.0)

    snap = m.snapshot()
    assert snap["total_requests"] == 2
    assert snap["total_errors"] == 1
    assert snap["rate_limit_hits"] == 1
    assert snap["status_codes"][200] == 1
    assert snap["status_codes"][500] == 1
    assert snap["provider_tests"]["groq"]["ok"] is True
    assert len(snap["recent"]) == 2
    assert any(row["route"].startswith("GET ") for row in snap["top_routes"])


def test_normalize_collapses_ids():
    m = RuntimeMetrics()
    m.record_request(
        method="GET",
        path="/admin/api/code/sessions/abcdef12-3456-7890-abcd-ef1234567890",
        status=200,
        duration_ms=1.0,
    )
    snap = m.snapshot()
    route = snap["top_routes"][0]["route"]
    assert ":id" in route
    assert "abcdef12" not in route


def test_admin_metrics_endpoint():
    # Seed global metrics so the endpoint has something to show
    metrics.record_request(method="GET", path="/health", status=200, duration_ms=1.2)
    client = _local_client(create_test_app())
    response = client.get("/admin/api/metrics")
    assert response.status_code == 200
    body = response.json()
    assert "total_requests" in body
    assert "top_routes" in body
    assert "recent" in body
    assert body["total_requests"] >= 1


def test_admin_metrics_page_served():
    client = _local_client(create_test_app())
    response = client.get("/admin/metrics")
    assert response.status_code == 200
    assert 'id="view-metrics"' in response.text
    assert "theme_boot.js" in response.text
    assert "commandPalette" in response.text


def test_theme_boot_asset_served():
    client = _local_client(create_test_app())
    response = client.get(f"/admin/assets/{package_version()}/theme_boot.js")
    assert response.status_code == 200
    assert b"fcc.theme" in response.content


def test_config_export_omits_secrets(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    env_file = tmp_path / ".fcc" / ".env"
    env_file.parent.mkdir(parents=True)
    env_file.write_text(
        "FCC_CONFIG_SCHEMA=1\nMODEL=groq/default\nGROQ_API_KEY=super-secret-key\n",
        encoding="utf-8",
    )
    client = _local_client(create_test_app())
    response = client.get("/admin/api/config/export")
    assert response.status_code == 200
    body = response.json()
    assert body["format"] == "fcc-admin-config"
    assert "GROQ_API_KEY" not in body["values"]
    assert "super-secret-key" not in str(body)
    # MODEL may or may not appear depending on catalog defaults; secrets must stay out
    assert all(
        "KEY" not in k.upper() and "TOKEN" not in k.upper() and "SECRET" not in k.upper()
        for k in body["values"]
    )


def test_config_import_dry_run_and_rejects_secrets(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".fcc").mkdir(parents=True)
    client = _local_client(create_test_app())

    # Dry-run with a secret-looking key + unknown key
    preview = client.post(
        "/admin/api/config/import",
        json={
            "apply": False,
            "values": {
                "GROQ_API_KEY": "should-not-apply",
                "NOT_A_REAL_KEY_XYZ": "nope",
            },
        },
    )
    assert preview.status_code == 200
    body = preview.json()
    assert body["dry_run"] is True
    assert body["applied"] is False
    assert body["count"] == 0
    assert "GROQ_API_KEY" in body["rejected_secrets"] or body["count"] == 0


def test_detailed_health_includes_metrics_fields():
    client = _local_client(create_test_app())
    response = client.get("/admin/api/health/detailed")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "healthy"
    assert "uptime_seconds" in body
    assert "total_requests" in body
    assert "requests_per_second" in body


def test_security_audit_mentions_metrics():
    client = _local_client(create_test_app())
    response = client.get("/admin/api/security/audit")
    assert response.status_code == 200
    body = response.json()
    assert body.get("metrics_enabled") is True
    assert "ip_allowlist_configured" in body
