"""Phase 7: live provider latency recording helpers + SSE + PWA assets."""

from free_claude_code.api.metrics import RuntimeMetrics, metrics
from free_claude_code.core.version import package_version
from fastapi.testclient import TestClient
from tests.api.support import create_test_app


def _local_client(app):
    return TestClient(
        app,
        base_url="http://127.0.0.1",
        client=("127.0.0.1", 50000),
    )


def test_record_provider_latency_live_path():
    m = RuntimeMetrics()
    m.record_provider_latency("groq", latency_ms=12.5, ok=True)
    m.record_provider_latency("groq", latency_ms=20.0, ok=False)
    snap = m.snapshot()
    rows = {r["provider_id"]: r for r in snap["provider_latency"]}
    assert "groq" in rows
    assert rows["groq"]["count"] == 2
    assert rows["groq"]["errors"] == 1
    assert rows["groq"]["avg_ms"] > 0


def test_pwa_assets_served():
    client = _local_client(create_test_app())
    ver = package_version()
    for name in ("manifest.webmanifest", "sw.js"):
        r = client.get(f"/admin/assets/{ver}/{name}")
        assert r.status_code == 200, name
    page = client.get("/admin")
    assert page.status_code == 200
    assert "manifest.webmanifest" in page.text


def test_security_events_stream_headers():
    client = _local_client(create_test_app())
    with client.stream("GET", "/admin/api/security/events/stream") as resp:
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers.get("content-type", "")
        # read first chunk
        chunk = next(resp.iter_bytes())
        assert b"event:" in chunk or b"data:" in chunk or b":" in chunk
