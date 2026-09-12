"""Phase 8: cookie bridge, metrics federation merge, live streams."""

import secrets

from fastapi.testclient import TestClient

from free_claude_code.api.metrics_federation import merge_metric_exports
from free_claude_code.core.version import package_version
from tests.api.support import create_test_app


def _local_client(app):
    return TestClient(
        app,
        base_url="http://127.0.0.1",
        client=("127.0.0.1", 50000),
    )


def test_merge_metric_exports_aggregates_nodes():
    nodes = [
        {
            "node_id": "a",
            "version": "1",
            "snapshot": {
                "total_requests": 10,
                "total_errors": 1,
                "rate_limit_hits": 2,
                "status_codes": {"200": 9, "500": 1},
                "latency_histogram_ms": {"50": 5, "100": 5},
                "latency_overflow": 0,
                "provider_latency": [
                    {"provider_id": "groq", "count": 4, "errors": 0, "avg_ms": 10, "max_ms": 20}
                ],
                "top_routes": [
                    {"route": "GET /health", "count": 10, "errors": 0, "avg_ms": 1, "max_ms": 2}
                ],
                "uptime_seconds": 10,
                "requests_per_second": 1,
            },
        },
        {
            "node_id": "b",
            "snapshot": {
                "total_requests": 5,
                "total_errors": 0,
                "rate_limit_hits": 0,
                "status_codes": {"200": 5},
                "latency_histogram_ms": {"50": 5},
                "provider_latency": [
                    {"provider_id": "groq", "count": 2, "errors": 1, "avg_ms": 30, "max_ms": 40},
                    {"provider_id": "openrouter", "count": 1, "errors": 0, "avg_ms": 5, "max_ms": 5},
                ],
                "top_routes": [
                    {"route": "GET /health", "count": 5, "errors": 0, "avg_ms": 2, "max_ms": 3}
                ],
            },
        },
    ]
    merged = merge_metric_exports(nodes)
    assert merged["nodes_accepted"] == 2
    assert merged["total_requests"] == 15
    assert merged["total_errors"] == 1
    assert merged["status_codes"][200] == 14
    groq = next(p for p in merged["provider_latency"] if p["provider_id"] == "groq")
    assert groq["count"] == 6
    assert groq["errors"] == 1
    # weighted avg: (10*4 + 30*2) / 6 = 50/3 ≈ 16.67
    assert 16.0 <= groq["avg_ms"] <= 17.0
    health = next(r for r in merged["top_routes"] if r["route"] == "GET /health")
    assert health["count"] == 15


def test_metrics_export_endpoint():
    client = _local_client(create_test_app())
    r = client.get("/admin/api/metrics/export")
    assert r.status_code == 200
    body = r.json()
    assert body["format"] == "fcc-metrics-federation"
    assert "snapshot" in body
    assert "version" in body and body["format_version"] == 1


def test_metrics_merge_endpoint():
    client = _local_client(create_test_app())
    payload = {
        "nodes": [
            {
                "node_id": "n1",
                "snapshot": {
                    "total_requests": 3,
                    "total_errors": 0,
                    "provider_latency": [],
                    "top_routes": [],
                    "status_codes": {},
                    "latency_histogram_ms": {},
                },
            }
        ]
    }
    r = client.post("/admin/api/metrics/merge", json=payload)
    assert r.status_code == 200
    assert r.json()["nodes_accepted"] == 1
    assert r.json()["total_requests"] == 3


def test_session_token_cookie_bridge(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    token = "phase8-admin-token-" + secrets.token_hex(8)
    monkeypatch.setenv("FCC_ADMIN_API_TOKEN", token)
    client = _local_client(create_test_app())

    denied = client.get("/admin/api/metrics")
    assert denied.status_code == 401

    # Wrong cookie rejected via session set
    bad = client.post("/admin/api/session/token", json={"token": "wrong"})
    assert bad.status_code == 401

    ok = client.post("/admin/api/session/token", json={"token": token})
    assert ok.status_code == 200
    assert ok.json().get("set") is True
    # cookie should unlock metrics without header
    r = client.get("/admin/api/metrics")
    assert r.status_code == 200

    # SSE stream should accept cookie auth
    with client.stream("GET", "/admin/api/security/events/stream") as resp:
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers.get("content-type", "")

    cleared = client.delete("/admin/api/session/token")
    assert cleared.status_code == 200
    denied2 = client.get("/admin/api/metrics")
    assert denied2.status_code == 401


def test_metrics_stream_headers():
    client = _local_client(create_test_app())
    with client.stream("GET", "/admin/api/metrics/stream") as resp:
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers.get("content-type", "")
        # First event should arrive immediately (before the 2s sleep)
        chunk = next(resp.iter_bytes())
        assert b"metrics" in chunk or b"data:" in chunk
        # disconnect so generator exits
        resp.close()
