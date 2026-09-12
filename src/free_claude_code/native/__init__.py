"""Optional native acceleration layer for FCC hot paths.

Import order:
1. Try compiled ``fcc_core`` (Rust / PyO3) if installed.
2. Always expose pure-Python ultra implementations as the stable API.

Callers should import from ``free_claude_code.native`` only — never branch on
whether Rust is present. The Python path is production-grade and correct;
Rust is a drop-in speed boost when the wheel is built.
"""

from free_claude_code.native import ultra as _ultra_py

_fcc_core = None
_NATIVE = False
_BACKEND = "python"

try:  # pragma: no cover - exercised when wheel is installed
    import importlib

    _fcc_core = importlib.import_module("fcc_core")
    _NATIVE = True
    _BACKEND = "rust"
except Exception:  # noqa: BLE001 - any import/ABI failure → pure Python
    _fcc_core = None
    _NATIVE = False
    _BACKEND = "python"


def backend() -> str:
    """Return ``"rust"`` or ``"python"``."""
    return _BACKEND


def is_native() -> bool:
    return _NATIVE


def _pick(name: str):
    if _NATIVE and _fcc_core is not None and hasattr(_fcc_core, name):
        return getattr(_fcc_core, name)
    return getattr(_ultra_py, name)


BloomFilter = _pick("BloomFilter")
TtlLruCache = _ultra_py.TtlLruCache
SlidingWindow = _pick("SlidingWindow")
SecurityEventRing = _ultra_py.SecurityEventRing
security_events = _ultra_py.security_events
estimate_tokens_fast = _pick("estimate_tokens_fast")
validate_provider_id_fast = _pick("validate_provider_id_fast")
validate_model_ref_fast = _pick("validate_model_ref_fast")
validate_session_id_fast = _pick("validate_session_id_fast")
normalize_path_key = _pick("normalize_path_key")
fnv1a64 = _pick("fnv1a64")
sanitize_log_fast = _pick("sanitize_log_fast")
is_safe_asset_name = _pick("is_safe_asset_name")
json_dumps_compact = _pick("json_dumps_compact")
json_loads_object = _pick("json_loads_object")
json_loads_any = _pick("json_loads_any")
sha256_hex = _pick("sha256_hex")
hmac_sha256_hex = _pick("hmac_sha256_hex")
key_id16 = _pick("key_id16")
peer_url_ok = _pick("peer_url_ok")
content_digest_hex = _pick("content_digest_hex")
fccom1_encode_basic = _pick("fccom1_encode_basic")

try:
    from free_claude_code.native.ed25519_fast import backend as ed25519_backend
except Exception:  # pragma: no cover
    def ed25519_backend() -> str:  # type: ignore[misc]
        return "pure"

__all__ = [
    "BloomFilter",
    "TtlLruCache",
    "SlidingWindow",
    "SecurityEventRing",
    "security_events",
    "backend",
    "is_native",
    "estimate_tokens_fast",
    "validate_provider_id_fast",
    "validate_model_ref_fast",
    "validate_session_id_fast",
    "normalize_path_key",
    "fnv1a64",
    "sanitize_log_fast",
    "is_safe_asset_name",
    "json_dumps_compact",
    "json_loads_object",
    "json_loads_any",
    "sha256_hex",
    "hmac_sha256_hex",
    "key_id16",
    "peer_url_ok",
    "content_digest_hex",
    "fccom1_encode_basic",
    "ed25519_backend",
]
