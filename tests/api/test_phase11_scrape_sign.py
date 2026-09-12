"""Phase 11: peer scrape, OpenMetrics protobuf-lite, signed audit bundles."""

import io
import zipfile

from fastapi.testclient import TestClient

from free_claude_code.api.audit_bundle import build_audit_bundle_zip
from free_claude_code.api.audit_sign import (
    key_id_for,
    sign_and_attach,
    verify_signed_zip,
)
from free_claude_code.api.openmetrics_protobuf import (
    MAGIC,
    parse_openmetrics_protobuf,
    render_openmetrics_protobuf,
)
from free_claude_code.api.peer_scrape import (
    normalize_peer_list,
    normalize_peer_url,
    parse_peer_allowlist,
)
from tests.api.support import create_test_app


def _local_client(app):
    return TestClient(
        app,
        base_url="http://127.0.0.1",
        client=("127.0.0.1", 50000),
    )


def test_peer_url_blocks_metadata_and_bad_scheme():
    for bad in (
        "http://169.254.169.254/latest",
        "file:///etc/passwd",
        "http://user:pass@h/admin/api/x",
        "http://ok.com/not-admin",
    ):
        try:
            normalize_peer_url(bad, allowlist=set())
            raise AssertionError(f"should reject {bad}")
        except ValueError:
            pass


def test_peer_allowlist_enforced():
    try:
        normalize_peer_url("http://evil.example/admin/api/x", allowlist={"good.example"})
        raise AssertionError("allowlist should block")
    except ValueError:
        pass
    url = normalize_peer_url("http://good.example:8082", allowlist={"good.example"})
    assert url.endswith("/admin/api/security/events/export")
    peers = normalize_peer_list(
        ["http://good.example:8082", "http://good.example:8082"],
        allowlist={"good.example"},
    )
    assert len(peers) == 1


def test_parse_peer_allowlist():
    assert "a.com" in parse_peer_allowlist("a.com, b.com:8082")


def test_openmetrics_protobuf_roundtrip():
    blob = render_openmetrics_protobuf(
        {
            "uptime_seconds": 9,
            "total_requests": 10,
            "total_errors": 1,
            "error_rate": 0.1,
            "requests_per_second": 1,
            "rate_limit_hits": 0,
            "latency_overflow": 0,
            "status_codes": {200: 9, 500: 1},
            "provider_latency": [
                {"provider_id": "groq", "count": 2, "errors": 0, "avg_ms": 12}
            ],
        },
        node_id="n1",
        version="1.2.3",
    )
    assert blob.startswith(MAGIC)
    parsed = parse_openmetrics_protobuf(blob)
    assert parsed["count"] >= 5
    names = {m["name"] for m in parsed["metrics"]}
    assert "fcc_requests_total" in names
    assert "fcc_info" in names


def test_openmetrics_pb_endpoint():
    client = _local_client(create_test_app())
    r = client.get("/admin/api/metrics/openmetrics.pb")
    assert r.status_code == 200
    assert r.content.startswith(MAGIC)
    assert r.headers.get("x-fcc-metrics-format") == "fccom1"
    parsed = parse_openmetrics_protobuf(r.content)
    assert parsed["count"] >= 1


def test_sign_and_verify_bundle():
    raw = build_audit_bundle_zip(
        node_id="n",
        version="1",
        audit={"x": 1},
        events=[{"seq": 1, "event": "e"}],
        metrics_export={"snapshot": {}},
        prometheus_text="a 1\n",
        openmetrics_text="a 1\n# EOF\n",
    )
    key = b"phase11-test-signing-key!!"
    signed, sig = sign_and_attach(raw, key=key, node_id="n", version="1")
    assert sig["key_id"] == key_id_for(key)
    with zipfile.ZipFile(io.BytesIO(signed)) as zf:
        assert "signature.hmac.json" in zf.namelist()
    ok = verify_signed_zip(signed, key=key)
    assert ok["ok"] is True
    bad = verify_signed_zip(signed, key=b"other-key-other-key-other")
    assert bad["ok"] is False


def test_audit_bundle_signed_header(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FCC_AUDIT_SIGNING_KEY", "phase11-live-signing-key!")
    client = _local_client(create_test_app())
    r = client.get("/admin/api/audit/bundle")
    assert r.status_code == 200
    assert r.headers.get("x-fcc-audit-signed") == "1"
    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        assert "signature.hmac.json" in zf.namelist()
        assert "metrics.openmetrics.pb" in zf.namelist()
    ok = verify_signed_zip(r.content, key=b"phase11-live-signing-key!")
    assert ok["ok"] is True


def test_audit_bundle_verify_endpoint(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    key = b"verify-endpoint-key-123456"
    monkeypatch.setenv("FCC_AUDIT_SIGNING_KEY", key.decode())
    client = _local_client(create_test_app())
    bundle = client.get("/admin/api/audit/bundle")
    assert bundle.status_code == 200
    files = {"bundle": ("audit.zip", bundle.content, "application/zip")}
    v = client.post("/admin/api/audit/bundle/verify", files=files)
    assert v.status_code == 200
    assert v.json()["ok"] is True


def test_fanin_scrape_rejects_disallowed_peer(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FCC_FANIN_PEER_ALLOWLIST", "allowed.example")
    client = _local_client(create_test_app())
    r = client.post(
        "/admin/api/console/fanin/scrape",
        json={"peers": ["http://evil.example:8082"]},
    )
    assert r.status_code == 400


def test_fanin_scrape_self_loopback(monkeypatch, tmp_path):
    """Scrape own export URL via TestClient ASGI transport is not used by httpx;
    instead validate empty-peer and successful normalize on loopback allowlist free.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("FCC_FANIN_PEER_ALLOWLIST", raising=False)
    client = _local_client(create_test_app())
    r = client.post("/admin/api/console/fanin/scrape", json={"peers": []})
    assert r.status_code == 400
    # Bad path rejected
    r2 = client.post(
        "/admin/api/console/fanin/scrape",
        json={"peers": ["http://127.0.0.1/secret"]},
    )
    assert r2.status_code == 400


def test_security_audit_phase11_flags():
    client = _local_client(create_test_app())
    r = client.get("/admin/api/security/audit")
    assert r.status_code == 200
    body = r.json()
    assert body.get("openmetrics_protobuf") is True
    assert body.get("console_fanin_scrape") is True
    assert "audit_bundle_signed" in body
