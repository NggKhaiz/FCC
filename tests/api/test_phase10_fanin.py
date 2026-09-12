"""Phase 10: OpenMetrics, audit ZIP bundle, multi-replica console fan-in."""

import io
import json
import zipfile

from fastapi.testclient import TestClient

from free_claude_code.api.audit_bundle import build_audit_bundle_zip, sanitize_event
from free_claude_code.api.console_fanin import (
    ConsoleFanInHub,
    export_local_events,
    merge_event_exports,
)
from free_claude_code.api.openmetrics_export import render_openmetrics_text
from free_claude_code.native import security_events
from tests.api.support import create_test_app


def _local_client(app):
    return TestClient(
        app,
        base_url="http://127.0.0.1",
        client=("127.0.0.1", 50000),
    )


def test_openmetrics_ends_with_eof():
    text = render_openmetrics_text(
        {
            "uptime_seconds": 1,
            "total_requests": 3,
            "total_errors": 0,
            "error_rate": 0,
            "requests_per_second": 0.1,
            "rate_limit_hits": 0,
            "latency_overflow": 0,
            "status_codes": {200: 3},
            "latency_histogram_ms": {"50": 3},
            "provider_latency": [],
            "top_routes": [],
        },
        node_id="n1",
        version="1.0",
    )
    assert text.rstrip().endswith("# EOF")
    assert "fcc_requests_total" in text
    assert text.endswith("\n")


def test_openmetrics_endpoint():
    client = _local_client(create_test_app())
    r = client.get("/admin/api/metrics/openmetrics")
    assert r.status_code == 200
    assert "openmetrics" in r.headers.get("content-type", "").lower() or "text/plain" in r.headers.get(
        "content-type", ""
    )
    assert "# EOF" in r.text
    assert "fcc_info" in r.text


def test_sanitize_event_strips_unknown():
    clean = sanitize_event(
        {
            "seq": 1,
            "event": "x",
            "token": "secret",
            "password": "no",
            "client_ip": "1.1.1.1",
        }
    )
    assert "token" not in clean and "password" not in clean
    assert clean["event"] == "x"


def test_fanin_hub_merge_and_stale():
    hub = ConsoleFanInHub()
    hub.ingest(
        {
            "node_id": "alpha",
            "latest_seq": 3,
            "events": [
                {"seq": 1, "event": "a", "ts": 10.0, "secret": "x"},
                {"seq": 2, "event": "b", "ts": 11.0},
            ],
        }
    )
    hub.ingest(
        {
            "node_id": "beta",
            "events": [{"seq": 9, "event": "c", "ts": 12.0}],
        }
    )
    snap = hub.snapshot(limit=10)
    assert snap["nodes_tracked"] == 2
    assert snap["events"][0]["event"] == "c"
    assert all("secret" not in e for e in snap["events"])


def test_merge_event_exports_pure():
    a = export_local_events(
        node_id="n1",
        version="1",
        events=[{"seq": 1, "event": "e1", "ts": 1}],
        latest_seq=1,
    )
    b = {
        "node_id": "n2",
        "events": [{"seq": 2, "event": "e2", "ts": 2}],
    }
    merged = merge_event_exports([a, b, "bad", None])
    assert merged["nodes_accepted"] == 2
    assert merged["nodes_tracked"] == 2
    assert len(merged["events"]) >= 2


def test_build_audit_bundle_zip_no_secrets():
    blob = build_audit_bundle_zip(
        node_id="local",
        version="9.9",
        audit={"admin_api_token_configured": True},
        events=[{"seq": 1, "event": "hit", "authorization": "Bearer x"}],
        metrics_export={
            "snapshot": {
                "total_requests": 1,
                "recent": [{"path": "/v1", "client": "1.2.3.4"}] * 25,
            }
        },
        prometheus_text="fcc_info 1\n",
        openmetrics_text="fcc_info 1\n# EOF\n",
        fanin_summary={"nodes_tracked": 0},
    )
    zf = zipfile.ZipFile(io.BytesIO(blob))
    names = set(zf.namelist())
    assert "manifest.json" in names
    assert "metrics.openmetrics.txt" in names
    assert "fanin_summary.json" in names
    events_doc = json.loads(zf.read("security_events.json"))
    blob_txt = json.dumps(events_doc)
    assert "Bearer" not in blob_txt
    assert "authorization" not in blob_txt
    metrics_doc = json.loads(zf.read("metrics_export.json"))
    assert len(metrics_doc["snapshot"]["recent"]) <= 20


def test_security_events_export_endpoint():
    security_events.append("phase10_export_test", client_ip="127.0.0.1", path="/t")
    client = _local_client(create_test_app())
    r = client.get("/admin/api/security/events/export")
    assert r.status_code == 200
    body = r.json()
    assert body["format"] == "fcc-security-events"
    assert "events" in body
    assert body["node_id"]


def test_fanin_ingest_and_snapshot_endpoints():
    client = _local_client(create_test_app())
    payload = {
        "node_id": "replica-1",
        "version": "1",
        "latest_seq": 4,
        "events": [
            {"seq": 4, "event": "remote_hit", "ts": 100.0, "path": "/admin"},
        ],
    }
    r = client.post("/admin/api/console/fanin/ingest", json=payload)
    assert r.status_code == 200
    assert r.json()["ok"] is True
    snap = client.get("/admin/api/console/fanin")
    assert snap.status_code == 200
    body = snap.json()
    assert body["nodes_tracked"] >= 1
    assert any(e.get("event") == "remote_hit" for e in body.get("events") or [])


def test_fanin_merge_endpoint():
    client = _local_client(create_test_app())
    r = client.post(
        "/admin/api/console/fanin/merge",
        json={
            "nodes": [
                {
                    "node_id": "m1",
                    "events": [{"seq": 1, "event": "merge_a", "ts": 1}],
                },
                {
                    "node_id": "m2",
                    "events": [{"seq": 2, "event": "merge_b", "ts": 2}],
                },
            ]
        },
    )
    assert r.status_code == 200
    assert r.json()["nodes_accepted"] == 2


def test_audit_bundle_endpoint_zip():
    client = _local_client(create_test_app())
    r = client.get("/admin/api/audit/bundle")
    assert r.status_code == 200
    assert "application/zip" in r.headers.get("content-type", "")
    assert "attachment" in r.headers.get("content-disposition", "")
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    assert "manifest.json" in zf.namelist()
    manifest = json.loads(zf.read("manifest.json"))
    assert manifest["format"] == "fcc-audit-bundle"


def test_security_audit_phase10_flags():
    client = _local_client(create_test_app())
    r = client.get("/admin/api/security/audit")
    assert r.status_code == 200
    body = r.json()
    assert body.get("openmetrics_export") is True
    assert body.get("audit_bundle_export") is True
    assert body.get("console_fanin") is True
