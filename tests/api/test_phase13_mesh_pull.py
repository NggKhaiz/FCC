"""Phase 13: mesh pull, Ed25519 fast-path, native status, packaging scripts."""

from pathlib import Path

from fastapi.testclient import TestClient

from free_claude_code.api.audit_sign import ed25519_backend, sign_and_attach_ed25519, verify_ed25519_zip
from free_claude_code.api.audit_bundle import build_audit_bundle_zip
from free_claude_code.api.hub_mesh import HubMeshRegistry, hub_mesh
from free_claude_code.native.ed25519_fast import (
    backend as ed_backend,
    generate_keypair,
    sign,
    verify,
)
from tests.api.support import create_test_app


def _local_client(app):
    return TestClient(
        app,
        base_url="http://127.0.0.1",
        client=("127.0.0.1", 50000),
    )


def test_ed25519_fast_backend_pure_or_crypto():
    b = ed_backend()
    assert b in {"pure", "cryptography"}
    assert ed25519_backend() == b
    seed, pub = generate_keypair(b"\x0a" * 32)
    msg = b"phase13-fast"
    assert verify(msg, sign(msg, seed), pub)


def test_ed25519_fast_matches_audit_zip():
    seed, _ = generate_keypair(b"\x0b" * 32)
    raw = build_audit_bundle_zip(
        node_id="n",
        version="1",
        audit={},
        events=[],
        metrics_export={},
        prometheus_text="x\n",
        openmetrics_text="x\n",
    )
    signed, _ = sign_and_attach_ed25519(raw, seed=seed, node_id="n")
    assert verify_ed25519_zip(signed)["ok"] is True


def test_hub_mesh_pull_targets():
    mesh = HubMeshRegistry()
    mesh.register({"hub_id": "a", "base_url": "http://a:8082"})
    mesh.register({"hub_id": "b"})  # no base_url
    targets = mesh.pull_targets()
    assert len(targets) == 1
    assert targets[0]["hub_id"] == "a"


def test_mesh_pull_requires_targets():
    hub_mesh.clear()
    client = _local_client(create_test_app())
    r = client.post("/admin/api/console/mesh/pull", json={})
    assert r.status_code == 400


def test_mesh_pull_rejects_bad_hub_url():
    client = _local_client(create_test_app())
    r = client.post(
        "/admin/api/console/mesh/pull",
        json={"hubs": ["http://169.254.169.254/admin/api/x"]},
    )
    assert r.status_code == 400


def test_native_status_endpoint():
    client = _local_client(create_test_app())
    r = client.get("/admin/api/native/status")
    assert r.status_code == 200
    body = r.json()
    assert body["fcc_core_backend"] in {"python", "rust"}
    assert body["ed25519_backend"] in {"pure", "cryptography"}
    assert body["packaging"]["crate"] == "crates/fcc_core"
    assert "package_script" in body["packaging"]


def test_security_audit_phase13_flags():
    client = _local_client(create_test_app())
    r = client.get("/admin/api/security/audit")
    assert r.status_code == 200
    body = r.json()
    assert body.get("hub_mesh_pull") is True
    assert body.get("fcc_core_packaging") is True
    assert body.get("ed25519_backend") in {"pure", "cryptography"}


def test_package_script_exists_and_stub():
    root = Path(__file__).resolve().parents[2]
    script = root / "scripts" / "package_fcc_core.sh"
    assert script.is_file()
    text = script.read_text()
    assert "maturin build" in text
    ci = root / "scripts" / "native-core.ci.yml"
    assert "matrix:" in ci.read_text()
    assert "package_fcc_core" in ci.read_text() or "dist/fcc_core" in ci.read_text()
