"""Phase 4: native ultra-core correctness + security contracts."""

from __future__ import annotations

import time

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from free_claude_code.api.security import (
    sanitize_log_value,
    validate_model_ref,
    validate_provider_id,
    validate_session_id,
)
from free_claude_code.core.version import package_version
from free_claude_code.native import (
    BloomFilter,
    SlidingWindow,
    TtlLruCache,
    backend,
    estimate_tokens_fast,
    fnv1a64,
    is_native,
    is_safe_asset_name,
    normalize_path_key,
    sanitize_log_fast,
    validate_model_ref_fast,
    validate_provider_id_fast,
    validate_session_id_fast,
)
from free_claude_code.native import ultra as ultra_py
from tests.api.support import create_test_app


def _local_client(app):
    return TestClient(
        app,
        base_url="http://127.0.0.1",
        client=("127.0.0.1", 50000),
    )


def test_backend_is_python_or_rust():
    assert backend() in {"python", "rust"}
    assert isinstance(is_native(), bool)


def test_fnv_stable():
    assert fnv1a64("fcc") == ultra_py.fnv1a64("fcc")
    assert fnv1a64(b"fcc") == fnv1a64("fcc")


@pytest.mark.parametrize(
    "value,ok",
    [
        ("groq", True),
        ("openrouter", True),
        ("a", True),
        ("", False),
        ("Groq", False),
        ("1abc", False),
        ("has-dash", False),
        ("x" * 65, False),
        ("ok_id_2", True),
    ],
)
def test_provider_id_fast(value, ok):
    assert validate_provider_id_fast(value) is ok


@pytest.mark.parametrize(
    "value,ok",
    [
        ("groq/llama-3.1", True),
        ("openai/gpt-4o", True),
        ("nopath", False),
        ("/x", False),
        ("a/", False),
        ("bad/name with space", False),
        ("ok/" + "m" * 400, True),
        ("ok/" + "m" * 510, False),  # total > 512
    ],
)
def test_model_ref_fast(value, ok):
    assert validate_model_ref_fast(value) is ok


def test_session_id_fast():
    assert validate_session_id_fast("abc-123_X")
    assert not validate_session_id_fast("")
    assert not validate_session_id_fast("has space")
    assert not validate_session_id_fast("x" * 129)


def test_security_wrappers_raise():
    with pytest.raises(HTTPException) as ei:
        validate_provider_id("../etc")
    assert ei.value.status_code == 400
    with pytest.raises(HTTPException):
        validate_model_ref("nope")
    with pytest.raises(HTTPException):
        validate_session_id("../../x")
    assert validate_provider_id("groq") == "groq"
    assert validate_model_ref("groq/llama") == "groq/llama"
    assert validate_session_id("s1") == "s1"


def test_sanitize_log_strips_controls():
    dirty = "ok\nline\r\x00\x1fend"
    clean = sanitize_log_value(dirty, max_length=50)
    assert "\n" not in clean
    assert "\r" not in clean
    assert "\x00" not in clean
    assert sanitize_log_fast("a" * 300, 20).endswith("...")


def test_bloom_no_false_negatives():
    bloom = BloomFilter(capacity=2000, error_rate=0.01)
    keys = [f"provider_{i}" for i in range(500)]
    for k in keys:
        bloom.add(k)
    for k in keys:
        assert k in bloom
        assert bloom.might_contain(k)
    # unknown key may false-positive but must not crash
    _ = "totally-missing-key-xyz" in bloom


def test_ttl_lru_cache():
    cache = TtlLruCache(maxsize=2, default_ttl=0.05)
    cache.set("a", 1)
    assert cache.get("a") == 1
    cache.set("b", 2)
    cache.set("c", 3)  # evicts a
    assert cache.get("a") is None
    time.sleep(0.08)
    assert cache.get("b") is None  # expired
    stats = cache.stats()
    assert stats["misses"] >= 1


def test_sliding_window_blocks():
    w = SlidingWindow()
    for _ in range(5):
        ok, _retry = w.allow("k", max_requests=5, window_seconds=60, block_seconds=1)
        assert ok
    ok, retry = w.allow("k", max_requests=5, window_seconds=60, block_seconds=1)
    assert not ok
    assert retry >= 1


def test_normalize_path_collapses_ids():
    p = normalize_path_key(
        "/admin/api/code/sessions/abcdef12-3456-7890-abcd-ef1234567890?x=1"
    )
    assert ":id" in p
    assert "abcdef12" not in p
    assert normalize_path_key("") == "/"


def test_estimate_tokens_fast_basic():
    assert estimate_tokens_fast("") == 0
    assert estimate_tokens_fast("hi") >= 1
    assert estimate_tokens_fast("你好世界") == 4


def test_asset_name_safety():
    assert is_safe_asset_name("admin.js")
    assert is_safe_asset_name("theme_boot.js")
    assert not is_safe_asset_name("../etc/passwd")
    assert not is_safe_asset_name("a/b.js")
    assert not is_safe_asset_name(".hidden")


def test_admin_rejects_bad_asset_name():
    client = _local_client(create_test_app())
    # allowlisted name only — traversal must 400/404
    r = client.get(f"/admin/assets/{package_version()}/..%2fetc%2fpasswd")
    assert r.status_code in {400, 404}
    r2 = client.get(f"/admin/assets/{package_version()}/admin.js")
    assert r2.status_code == 200


def test_provider_test_still_rate_limited_path_works():
    client = _local_client(create_test_app())
    # metrics endpoint should work (uses normalize_path_key internally after traffic)
    client.get("/health")
    r = client.get("/admin/api/metrics")
    assert r.status_code == 200
    assert "top_routes" in r.json()
