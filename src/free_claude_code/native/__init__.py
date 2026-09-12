"""Optional native acceleration layer for FCC hot paths.

Import order:
1. Try compiled ``fcc_core`` (Rust / PyO3) if installed.
2. Always expose pure-Python ultra implementations as the stable API.

Callers should import from ``free_claude_code.native`` only — never branch on
whether Rust is present. The Python path is production-grade and correct;
Rust is a drop-in speed boost when the wheel is built.
"""

from __future__ import annotations

from free_claude_code.native import ultra as _ultra_py

try:  # pragma: no cover - exercised when wheel is installed
    import fcc_core as _fcc_core  # type: ignore

    _NATIVE = True
    _BACKEND = "rust"
except Exception:  # noqa: BLE001 - any import/ABI failure → pure Python
    _fcc_core = None
    _NATIVE = False
    _BACKEND = "python"


def backend() -> str:
    """Return ``\"rust\"`` or ``\"python\"``."""
    return _BACKEND


def is_native() -> bool:
    return _NATIVE


# Re-export stable surface (Python implementations; Rust overrides below)
BloomFilter = _ultra_py.BloomFilter
TtlLruCache = _ultra_py.TtlLruCache
SlidingWindow = _ultra_py.SlidingWindow
SecurityEventRing = _ultra_py.SecurityEventRing
security_events = _ultra_py.security_events
estimate_tokens_fast = _ultra_py.estimate_tokens_fast
validate_provider_id_fast = _ultra_py.validate_provider_id_fast
validate_model_ref_fast = _ultra_py.validate_model_ref_fast
validate_session_id_fast = _ultra_py.validate_session_id_fast
normalize_path_key = _ultra_py.normalize_path_key
fnv1a64 = _ultra_py.fnv1a64
sanitize_log_fast = _ultra_py.sanitize_log_fast
is_safe_asset_name = _ultra_py.is_safe_asset_name
json_dumps_compact = _ultra_py.json_dumps_compact
json_loads_object = _ultra_py.json_loads_object
json_loads_any = _ultra_py.json_loads_any

if _NATIVE and _fcc_core is not None:  # pragma: no cover
    # Prefer Rust implementations when signatures match
    for _name in (
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
    ):
        if hasattr(_fcc_core, _name):
            globals()[_name] = getattr(_fcc_core, _name)
    if hasattr(_fcc_core, "BloomFilter"):
        BloomFilter = _fcc_core.BloomFilter  # type: ignore[misc,assignment]
    if hasattr(_fcc_core, "SlidingWindow"):
        SlidingWindow = _fcc_core.SlidingWindow  # type: ignore[misc,assignment]

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
]
