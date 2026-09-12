"""Phase 12: Rust hotpath twins, Ed25519, mTLS scrape opts, hub mesh."""

import os
from pathlib import Path

from fastapi.testclient import TestClient

from free_claude_code.api.audit_bundle import build_audit_bundle_zip
from free_claude_code.api.audit_sign import (
    sign_and_attach,
    sign_and_attach_ed25519,
    verify_any_signed_zip,
    verify_ed25519_zip,
    verify_signed_zip,
)
from free_claude_code.api.hub_mesh import HubMeshRegistry
from free_claude_code.api.peer_scrape import mtls_client_kwargs_from_env
from free_claude_code.native import (
    content_digest_hex,
    fccom1_encode_basic,
    hmac_sha256_hex,
    key_id16,
    peer_url_ok as native_peer_url_ok,
    sha256_hex,
)
from free_claude_code.native.ed25519_pure import generate_keypair, sign, verify
from free_claude_code.api.peer_scrape import normalize_peer_url
from tests.api.support import create_test_app


def _local_client(app):
    return TestClient(
        app,
        base_url="http://127.0.0.1",
        client=("127.0.0.1", 50000),
    )


def test_sha256_and_hmac_rfc_vectors():
    assert sha256_hex(b"") == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    key = b"\x0b" * 20
    assert (
        hmac_sha256_hex(key, b"Hi There")
        == "b0344c61d8db38535ca8afceaf0bf12b881dc200c9833da726e9376c2e32cff7"
    )
    assert len(key_id16(b"abc")) == 16


def test_peer_url_ok_native_and_module():
    assert native_peer_url_ok("http://h/admin/api/x")
    assert native_peer_url_ok("https://h.example")
    assert not native_peer_url_ok("http://169.254.169.254/latest")
    assert not native_peer_url_ok("file:///etc/passwd")
    # normalize uses native gate
    try:
        normalize_peer_url("http://169.254.169.254/admin/api/x", allowlist=set())
        raise AssertionError("should reject")
    except ValueError:
        pass


def test_fccom1_encode_basic_magic():
    blob = fccom1_encode_basic("n", "1", 10.0, 1.0, 3.0)
    assert blob[:8] == b"FCCOM1\x00\x00"


def test_content_digest_stable():
    pairs = [("b.txt", b"bb"), ("a.txt", b"aa")]
    d1 = content_digest_hex(pairs)
    d2 = content_digest_hex(list(reversed(pairs)))
    assert d1 == d2


def test_ed25519_pure_sign_verify():
    seed, pub = generate_keypair(b"\x02" * 32)
    msg = b"phase12-ed25519"
    sig = sign(msg, seed)
    assert verify(msg, sig, pub)
    assert not verify(b"tamper", sig, pub)


def test_ed25519_audit_bundle_roundtrip():
    seed, _pub = generate_keypair(b"\x03" * 32)
    raw = build_audit_bundle_zip(
        node_id="n",
        version="1",
        audit={},
        events=[],
        metrics_export={},
        prometheus_text="x\n",
        openmetrics_text="x\n",
    )
    signed, _ = sign_and_attach_ed25519(raw, seed=seed, node_id="n", version="1")
    assert verify_ed25519_zip(signed)["ok"] is True
    # dual hmac+ed
    dual, _ = sign_and_attach(raw, key=b"k" * 16, node_id="n")
    dual, _ = sign_and_attach_ed25519(dual, seed=seed, node_id="n")
    assert verify_signed_zip(dual, key=b"k" * 16)["ok"]
    assert verify_any_signed_zip(dual, hmac_key=b"k" * 16)["ok"]


def test_hub_mesh_registry():
    mesh = HubMeshRegistry()
    mesh.register({"hub_id": "hub-a", "base_url": "http://a:8082", "nodes_tracked": 2})
    mesh.register({"hub_id": "hub-b", "nodes_tracked": 1})
    snap = mesh.snapshot()
    assert snap["hubs_tracked"] == 2
    assert {h["hub_id"] for h in snap["hubs"]} == {"hub-a", "hub-b"}


def test_mesh_endpoints():
    client = _local_client(create_test_app())
    r = client.post(
        "/admin/api/console/mesh/register",
        json={"hub_id": "h1", "base_url": "http://h1:8082", "nodes_tracked": 3},
    )
    assert r.status_code == 200
    assert r.json()["ok"] is True
    snap = client.get("/admin/api/console/mesh")
    assert snap.status_code == 200
    assert snap.json()["hubs_tracked"] >= 1


def test_audit_bundle_ed25519_header(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FCC_AUDIT_ED25519_SEED", "phase12-ed-seed-passphrase")
    monkeypatch.delenv("FCC_AUDIT_SIGNING_KEY", raising=False)
    client = _local_client(create_test_app())
    r = client.get("/admin/api/audit/bundle")
    assert r.status_code == 200
    assert r.headers.get("x-fcc-audit-ed25519") == "1"
    assert verify_ed25519_zip(r.content)["ok"] is True


def test_mtls_kwargs_empty_by_default(monkeypatch):
    monkeypatch.delenv("FCC_FANIN_MTLS_CERT", raising=False)
    monkeypatch.delenv("FCC_FANIN_MTLS_KEY", raising=False)
    monkeypatch.delenv("FCC_FANIN_MTLS_CA", raising=False)
    assert mtls_client_kwargs_from_env() == {}


def test_security_audit_phase12_flags():
    client = _local_client(create_test_app())
    r = client.get("/admin/api/security/audit")
    assert r.status_code == 200
    body = r.json()
    assert body.get("hub_mesh") is True
    assert body.get("native_hotpath_v2") is True
    assert "audit_bundle_ed25519" in body
    assert "console_fanin_mtls" in body
