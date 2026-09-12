"""Phase 14: per-hub mesh tokens, continuous sync scheduler, PyPI packaging scripts."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from free_claude_code.api.hub_mesh import HubMeshRegistry, hub_mesh
from free_claude_code.api.mesh_scheduler import (
    DEFAULT_INTERVAL_SEC,
    MIN_INTERVAL_SEC,
    MeshSyncScheduler,
    autostart_from_env,
)
from tests.api.support import create_test_app


def _local_client(app):
    return TestClient(
        app,
        base_url="http://127.0.0.1",
        client=("127.0.0.1", 50000),
    )


def test_per_hub_token_not_in_snapshot():
    mesh = HubMeshRegistry()
    r = mesh.register(
        {
            "hub_id": "hub-a",
            "base_url": "http://a:8082",
            "token": "super-secret-token",
        }
    )
    assert r["token_set"] is True
    snap = mesh.snapshot()
    assert snap["format_version"] == 2
    hub = snap["hubs"][0]
    assert hub["token_set"] is True
    assert "token" not in hub
    assert "super-secret" not in str(snap)
    targets = mesh.pull_targets()
    assert targets[0]["token"] == "super-secret-token"


def test_set_token_clear_and_unknown():
    mesh = HubMeshRegistry()
    mesh.register({"hub_id": "x", "base_url": "http://x"})
    assert mesh.set_token("x", "t1")["token_set"] is True
    assert mesh.set_token("x", "")["token_set"] is False
    with pytest.raises(ValueError):
        mesh.set_token("missing", "t")


def test_register_preserves_token_when_omitted():
    mesh = HubMeshRegistry()
    mesh.register({"hub_id": "y", "base_url": "http://y", "token": "keep"})
    mesh.register({"hub_id": "y", "base_url": "http://y", "nodes_tracked": 3})
    assert mesh.get_token("y") == "keep"
    # explicit clear
    mesh.register({"hub_id": "y", "token": ""})
    assert mesh.get_token("y") is None


def test_scheduler_start_stop_and_clamp():
    import asyncio

    async def _run():
        s = MeshSyncScheduler()
        st = await s.start(interval_seconds=5)  # below min → clamped
        assert st["enabled"] is True
        assert st["interval_seconds"] == MIN_INTERVAL_SEC
        assert st["running"] is True
        once = await s.run_once()
        # no hubs → ok True message no targets (or httpx missing in bare env)
        assert "results" in once
        st2 = await s.stop()
        assert st2["enabled"] is False
        assert st2["running"] is False

    asyncio.run(_run())


def test_autostart_from_env(monkeypatch):
    monkeypatch.delenv("FCC_MESH_SYNC_AUTO", raising=False)
    assert autostart_from_env() is False
    monkeypatch.setenv("FCC_MESH_SYNC_AUTO", "1")
    assert autostart_from_env() is True


def test_mesh_token_endpoint():
    hub_mesh.clear()
    client = _local_client(create_test_app())
    client.post(
        "/admin/api/console/mesh/register",
        json={"hub_id": "h1", "base_url": "http://h1:8082"},
    )
    r = client.post(
        "/admin/api/console/mesh/token",
        json={"hub_id": "h1", "token": "tok-h1"},
    )
    assert r.status_code == 200
    assert r.json()["token_set"] is True
    snap = client.get("/admin/api/console/mesh")
    assert snap.json()["hubs"][0]["token_set"] is True
    assert "tok-h1" not in snap.text


def test_mesh_sync_status_and_once():
    hub_mesh.clear()
    client = _local_client(create_test_app())
    st = client.get("/admin/api/console/mesh/sync")
    assert st.status_code == 200
    body = st.json()
    assert body["enabled"] is False
    assert body["interval_seconds"] == DEFAULT_INTERVAL_SEC or body["interval_seconds"] >= MIN_INTERVAL_SEC
    once = client.post("/admin/api/console/mesh/sync/once", json={})
    assert once.status_code == 200
    assert "results" in once.json()


def test_mesh_sync_start_stop():
    client = _local_client(create_test_app())
    start = client.post(
        "/admin/api/console/mesh/sync/start",
        json={"interval_seconds": 30},
    )
    assert start.status_code == 200
    assert start.json()["enabled"] is True
    assert start.json()["interval_seconds"] == 30
    stop = client.post("/admin/api/console/mesh/sync/stop", json={})
    assert stop.status_code == 200
    assert stop.json()["enabled"] is False


def test_security_audit_phase14_flags():
    client = _local_client(create_test_app())
    r = client.get("/admin/api/security/audit")
    assert r.status_code == 200
    body = r.json()
    assert body.get("hub_mesh_tokens") is True
    assert body.get("hub_mesh_sync") is True
    assert body.get("fcc_core_pypi") is True


def test_publish_and_package_scripts_exist():
    root = Path(__file__).resolve().parents[2]
    pub = root / "scripts" / "publish_fcc_core.sh"
    assert pub.is_file()
    text = pub.read_text()
    assert "FCC_CORE_PUBLISH" in text
    assert "testpypi" in text
    crate = root / "crates" / "fcc_core" / "pyproject.toml"
    assert 'name = "fcc-core"' in crate.read_text()
    assert "License :: OSI Approved :: MIT License" in crate.read_text()


def test_native_status_includes_publish_script():
    client = _local_client(create_test_app())
    r = client.get("/admin/api/native/status")
    assert r.status_code == 200
    pack = r.json()["packaging"]
    assert pack.get("publish_script") == "scripts/publish_fcc_core.sh"
    assert pack.get("pypi_name") == "fcc-core"
