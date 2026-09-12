"""Phase 9: Prometheus exposition + admin WebSocket console protocol."""

from free_claude_code.api.admin_console import (
    ALLOWED_CHANNELS,
    dumps,
    error_message,
    handle_command,
    metrics_frame,
    parse_client_message,
    security_event_frame,
    welcome_message,
)
from free_claude_code.api.prometheus_export import render_prometheus_text
from free_claude_code.core.version import package_version
from fastapi.testclient import TestClient
from tests.api.support import create_test_app


def _local_client(app):
    return TestClient(
        app,
        base_url="http://127.0.0.1",
        client=("127.0.0.1", 50000),
    )


def test_render_prometheus_text_basic():
    text = render_prometheus_text(
        {
            "uptime_seconds": 10,
            "total_requests": 20,
            "total_errors": 1,
            "error_rate": 0.05,
            "requests_per_second": 2.0,
            "rate_limit_hits": 0,
            "latency_overflow": 1,
            "status_codes": {200: 19, 500: 1},
            "latency_histogram_ms": {"50": 10, "100": 10},
            "provider_latency": [
                {
                    "provider_id": "groq",
                    "count": 3,
                    "errors": 0,
                    "avg_ms": 11.5,
                    "max_ms": 20,
                }
            ],
            "top_routes": [
                {
                    "route": "GET /health",
                    "count": 20,
                    "errors": 1,
                    "avg_ms": 2.5,
                }
            ],
        },
        node_id='n"1',
        version="1.2.3",
    )
    assert text.endswith("\n")
    assert "fcc_info{" in text
    assert 'version="1.2.3"' in text
    # label escape
    assert 'node_id="n\\"1"' in text
    assert "fcc_requests_total" in text
    assert "fcc_http_responses_total" in text
    assert 'code="200"' in text
    assert "fcc_request_latency_ms_bucket" in text
    assert 'le="+Inf"' in text
    assert 'provider="groq"' in text
    assert 'route="GET /health"' in text
    # no secret-looking keys
    assert "token" not in text.lower()
    assert "password" not in text.lower()


def test_prometheus_endpoint():
    client = _local_client(create_test_app())
    r = client.get("/admin/api/metrics/prometheus")
    assert r.status_code == 200
    assert "text/plain" in r.headers.get("content-type", "")
    body = r.text
    assert "fcc_info" in body
    assert "fcc_requests_total" in body
    assert package_version() in body or "version=" in body


def test_console_protocol_ping_subscribe():
    data = parse_client_message('{"op":"PING","nonce":"x"}')
    assert data["op"] == "ping"
    reply, sub = handle_command("ping", data, subscribed=set())
    assert reply and reply["op"] == "pong"
    assert reply.get("nonce") == "x"

    reply, sub = handle_command(
        "subscribe",
        {"op": "subscribe", "channels": ["security", "metrics", "evil", "system"]},
        subscribed=set(),
    )
    assert reply["op"] == "subscribed"
    assert sub == {"security", "metrics", "system"}
    assert sub <= ALLOWED_CHANNELS

    reply, sub = handle_command(
        "unsubscribe",
        {"op": "unsubscribe", "channels": ["metrics"]},
        subscribed=sub,
    )
    assert "metrics" not in sub
    assert "security" in sub

    bad = error_message("x", "y")
    assert bad["op"] == "error"
    w = welcome_message(node_id="n", version="v")
    assert w["protocol"] == 1
    assert "security" in dumps(w)


def test_console_frames_strip_noise():
    frame = security_event_frame(
        [
            {
                "seq": 1,
                "event": "test",
                "level": "info",
                "client_ip": "1.2.3.4",
                "path": "/admin",
                "method": "GET",
                "secret": "should-not-leak",
            }
        ],
        latest_seq=1,
    )
    assert frame["channel"] == "security"
    assert "secret" not in frame["events"][0]

    m = metrics_frame(
        {
            "total_requests": 5,
            "provider_latency": [{"provider_id": "a"}] * 20,
            "top_routes": [{"route": "x"}] * 20,
        }
    )
    assert len(m["metrics"]["provider_latency"]) <= 10
    assert len(m["metrics"]["top_routes"]) <= 8


def test_console_parse_rejects_huge():
    import pytest

    with pytest.raises(ValueError):
        parse_client_message("x" * (9 * 1024))


def test_admin_console_ws_connect():
    client = _local_client(create_test_app())
    with client.websocket_connect("/admin/api/console/ws") as ws:
        # no token configured in default test app → welcome immediately
        msg = ws.receive_json()
        # may be welcome or system after welcome
        ops = {msg.get("op")}
        # drain a couple frames
        try:
            msg2 = ws.receive_json()
            ops.add(msg2.get("op"))
        except Exception:
            pass
        assert "welcome" in ops or "event" in ops
        ws.send_json({"op": "ping", "nonce": "t9"})
        # may receive system/event interleaved; wait for pong
        saw_pong = False
        for _ in range(10):
            m = ws.receive_json()
            if m.get("op") == "pong":
                assert m.get("nonce") == "t9"
                saw_pong = True
                break
        assert saw_pong
        ws.send_json({"op": "subscribe", "channels": ["metrics", "security"]})
        saw_sub = False
        for _ in range(10):
            m = ws.receive_json()
            if m.get("op") == "subscribed":
                assert "metrics" in m.get("channels", [])
                saw_sub = True
                break
        assert saw_sub


def test_console_view_in_admin_page():
    client = _local_client(create_test_app())
    page = client.get("/admin/console")
    assert page.status_code == 200
    assert "consoleLog" in page.text or "view-console" in page.text
