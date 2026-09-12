"""Pure-Python ultra core — always available, zero extra dependencies.

Designed as a behavioral twin of ``crates/fcc_core`` so Rust can replace
individual functions without call-site changes.

Performance notes (CPython 3.11+, typical small inputs):
- provider/model/session validators: tight loops, no regex engine
- BloomFilter: double-hash bitset, no false negatives
- SlidingWindow: deque + monotonic clock
- token approx: O(n) char scan, ~4 chars/token heuristic with CJK weight
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict, deque
from typing import Hashable, TypeVar

T = TypeVar("T")

# ---------------------------------------------------------------------------
# Hashing
# ---------------------------------------------------------------------------

_FNV_OFFSET = 0xCBF29CE484222325
_FNV_PRIME = 0x100000001B3


def fnv1a64(data: str | bytes) -> int:
    """FNV-1a 64-bit hash (stable across runs, fast for short keys)."""
    if isinstance(data, str):
        data = data.encode("utf-8", errors="surrogatepass")
    h = _FNV_OFFSET
    for b in data:
        h ^= b
        h = (h * _FNV_PRIME) & 0xFFFFFFFFFFFFFFFF
    return h


# ---------------------------------------------------------------------------
# Validators (no regex — predictable latency)
# ---------------------------------------------------------------------------


def validate_provider_id_fast(provider_id: str) -> bool:
    """Return True if provider_id matches ``^[a-z][a-z0-9_]*$`` and len≤64."""
    if not provider_id or len(provider_id) > 64:
        return False
    first = provider_id[0]
    if first < "a" or first > "z":
        return False
    for ch in provider_id:
        if not (("a" <= ch <= "z") or ("0" <= ch <= "9") or ch == "_"):
            return False
    return True


def validate_model_ref_fast(model_ref: str) -> bool:
    """Return True for ``provider/model...`` with safe charset, len≤512."""
    if not model_ref or len(model_ref) > 512:
        return False
    slash = model_ref.find("/")
    if slash <= 0 or slash == len(model_ref) - 1:
        return False
    provider = model_ref[:slash]
    model = model_ref[slash + 1 :]
    if not validate_provider_id_fast(provider):
        return False
    for ch in model:
        o = ord(ch)
        if ch in "/_-.:" or ("a" <= ch <= "z") or ("A" <= ch <= "Z") or ("0" <= ch <= "9"):
            continue
        # allow common model id punctuation already listed; reject others
        if o < 33 or o > 126:
            return False
        if ch in " \\\t\n\r\"'`$<>|(){}[]":
            return False
    return True


def validate_session_id_fast(session_id: str) -> bool:
    """Return True for ``^[a-zA-Z0-9_-]{1,128}$``."""
    if not session_id or len(session_id) > 128:
        return False
    for ch in session_id:
        if not (
            ("a" <= ch <= "z")
            or ("A" <= ch <= "Z")
            or ("0" <= ch <= "9")
            or ch in "_-"
        ):
            return False
    return True


def is_safe_asset_name(filename: str) -> bool:
    """Admin static asset filename: no path separators or traversal."""
    if not filename or len(filename) > 128:
        return False
    if filename in {".", ".."} or filename.startswith("."):
        return False
    if "/" in filename or "\\" in filename or "\x00" in filename:
        return False
    for ch in filename:
        if not (
            ("a" <= ch <= "z")
            or ("A" <= ch <= "Z")
            or ("0" <= ch <= "9")
            or ch in "._-"
        ):
            return False
    return True


def normalize_path_key(path: str) -> str:
    """Collapse dynamic segments for metrics aggregation (mirrors metrics.py)."""
    if not path:
        return "/"
    # strip query
    q = path.find("?")
    if q >= 0:
        path = path[:q]
    parts = path.split("/")
    out: list[str] = []
    for part in parts:
        if not part:
            continue
        if len(part) > 40 or part.isdigit() or _looks_like_id(part):
            out.append(":id")
        else:
            out.append(part[:64])
    return "/" + "/".join(out) if out else "/"


def _looks_like_id(value: str) -> bool:
    if len(value) < 8:
        return False
    hexish = True
    has_digit = False
    for c in value:
        if c in "0123456789":
            has_digit = True
        elif c in "abcdefABCDEF-_":
            continue
        else:
            hexish = False
            break
    return hexish and has_digit


# ---------------------------------------------------------------------------
# Token approximation (stream hot path)
# ---------------------------------------------------------------------------


def estimate_tokens_fast(text: str) -> int:
    """Fast token estimate without tiktoken.

    Heuristic:
    - ASCII runs ≈ 4 chars / token
    - CJK / fullwidth ≈ 1 char / token
    - never returns 0 for non-empty text
    """
    if not text:
        return 0
    ascii_chars = 0
    wide_chars = 0
    for ch in text:
        o = ord(ch)
        # CJK Unified + Hiragana/Katakana + Hangul + fullwidth
        if (
            0x4E00 <= o <= 0x9FFF
            or 0x3040 <= o <= 0x30FF
            or 0xAC00 <= o <= 0xD7AF
            or 0xFF00 <= o <= 0xFFEF
        ):
            wide_chars += 1
        else:
            ascii_chars += 1
    tokens = wide_chars + (ascii_chars + 3) // 4
    return tokens if tokens > 0 else 1


# ---------------------------------------------------------------------------
# Log sanitize
# ---------------------------------------------------------------------------


def sanitize_log_fast(value: str, max_length: int = 200) -> str:
    """Strip control chars / newlines for safe logging."""
    if not isinstance(value, str):
        value = str(value)
    out_chars: list[str] = []
    for ch in value:
        o = ord(ch)
        if ch == "\n":
            out_chars.append("\\n")
        elif ch == "\r":
            out_chars.append("\\r")
        elif o >= 32 or ch == "\t":
            out_chars.append(ch)
        # else drop control
        if len(out_chars) >= max_length:
            break
    s = "".join(out_chars)
    if len(value) > max_length or len(s) >= max_length:
        return s[:max_length] + "..."
    return s


# ---------------------------------------------------------------------------
# Bloom filter (no false negatives)
# ---------------------------------------------------------------------------


class BloomFilter:
    """Simple double-hash Bloom filter.

    False positives possible; false negatives never.
    Ideal for negative-cache of unknown provider ids / blocked paths.
    """

    __slots__ = ("_bits", "_m", "_k", "_count")

    def __init__(self, capacity: int = 10_000, error_rate: float = 0.01) -> None:
        if capacity < 1:
            capacity = 1
        # m = -n ln(p) / (ln2)^2 ; k = m/n ln2
        import math

        m = int(-capacity * math.log(error_rate) / (math.log(2) ** 2))
        m = max(64, m)
        # round up to byte multiple of 64 bits for word friendliness
        m = (m + 63) // 64 * 64
        k = max(1, int((m / capacity) * math.log(2)))
        k = min(k, 16)
        self._m = m
        self._k = k
        self._bits = bytearray(m // 8)
        self._count = 0

    @property
    def count(self) -> int:
        return self._count

    def _indexes(self, key: str | bytes) -> list[int]:
        h1 = fnv1a64(key)
        # second hash: mix
        h2 = fnv1a64(str(h1)) if isinstance(key, str) else fnv1a64(h1.to_bytes(8, "little"))
        m = self._m
        return [((h1 + i * h2) & 0xFFFFFFFFFFFFFFFF) % m for i in range(self._k)]

    def add(self, key: str | bytes) -> None:
        bits = self._bits
        for idx in self._indexes(key):
            bits[idx >> 3] |= 1 << (idx & 7)
        self._count += 1

    def __contains__(self, key: object) -> bool:
        if not isinstance(key, (str, bytes)):
            return False
        bits = self._bits
        for idx in self._indexes(key):
            if not (bits[idx >> 3] & (1 << (idx & 7))):
                return False
        return True

    def might_contain(self, key: str | bytes) -> bool:
        return key in self

    def clear(self) -> None:
        for i in range(len(self._bits)):
            self._bits[i] = 0
        self._count = 0


# ---------------------------------------------------------------------------
# TTL + LRU cache
# ---------------------------------------------------------------------------


class TtlLruCache:
    """Thread-safe LRU cache with optional per-entry TTL."""

    __slots__ = ("_data", "_maxsize", "_default_ttl", "_lock", "_hits", "_misses")

    def __init__(self, maxsize: int = 1024, default_ttl: float | None = 60.0) -> None:
        self._data: OrderedDict[Hashable, tuple[object, float | None]] = OrderedDict()
        self._maxsize = max(1, maxsize)
        self._default_ttl = default_ttl
        self._lock = threading.RLock()
        self._hits = 0
        self._misses = 0

    def get(self, key: Hashable, default: object = None) -> object:
        with self._lock:
            item = self._data.get(key)
            if item is None:
                self._misses += 1
                return default
            value, expires = item
            if expires is not None and time.monotonic() >= expires:
                del self._data[key]
                self._misses += 1
                return default
            self._data.move_to_end(key)
            self._hits += 1
            return value

    def set(self, key: Hashable, value: object, ttl: float | None = None) -> None:
        with self._lock:
            exp: float | None
            use_ttl = self._default_ttl if ttl is None else ttl
            if use_ttl is None:
                exp = None
            else:
                exp = time.monotonic() + float(use_ttl)
            if key in self._data:
                self._data.move_to_end(key)
            self._data[key] = (value, exp)
            while len(self._data) > self._maxsize:
                self._data.popitem(last=False)

    def delete(self, key: Hashable) -> None:
        with self._lock:
            self._data.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()

    def stats(self) -> dict[str, int]:
        with self._lock:
            return {
                "size": len(self._data),
                "maxsize": self._maxsize,
                "hits": self._hits,
                "misses": self._misses,
            }


# ---------------------------------------------------------------------------
# Sliding window rate limiter
# ---------------------------------------------------------------------------


class SlidingWindow:
    """Per-key sliding window limiter (monotonic clock)."""

    __slots__ = ("_windows", "_blocked", "_lock")

    def __init__(self) -> None:
        self._windows: dict[str, deque[float]] = {}
        self._blocked: dict[str, float] = {}
        self._lock = threading.Lock()

    def allow(
        self,
        key: str,
        *,
        max_requests: int,
        window_seconds: float,
        block_seconds: float = 60.0,
    ) -> tuple[bool, int]:
        """Return ``(allowed, retry_after_seconds)``."""
        now = time.monotonic()
        with self._lock:
            blocked_until = self._blocked.get(key, 0.0)
            if now < blocked_until:
                return False, max(1, int(blocked_until - now))

            q = self._windows.get(key)
            if q is None:
                q = deque()
                self._windows[key] = q

            start = now - window_seconds
            while q and q[0] < start:
                q.popleft()

            if len(q) >= max_requests:
                self._blocked[key] = now + block_seconds
                return False, max(1, int(block_seconds))

            q.append(now)
            return True, 0

    def cleanup(self, stale_after: float = 3600.0) -> int:
        """Drop idle keys; return number removed."""
        now = time.monotonic()
        removed = 0
        with self._lock:
            stale = [
                k
                for k, q in self._windows.items()
                if not q or (now - q[-1]) > stale_after
            ]
            for k in stale:
                del self._windows[k]
                self._blocked.pop(k, None)
                removed += 1
            # also drop expired blocks
            expired_blocks = [k for k, t in self._blocked.items() if t <= now]
            for k in expired_blocks:
                self._blocked.pop(k, None)
        return removed

    def is_blocked(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            until = self._blocked.get(key, 0.0)
            return now < until

    def block(self, key: str, seconds: float) -> None:
        with self._lock:
            self._blocked[key] = time.monotonic() + max(0.0, float(seconds))

    def clear_block(self, key: str) -> None:
        with self._lock:
            self._blocked.pop(key, None)


# ---------------------------------------------------------------------------
# Compact JSON helpers (SSE / tool-call hot path)
# ---------------------------------------------------------------------------

_COMPACT_SEPARATORS = (",", ":")


def json_dumps_compact(value: object) -> str:
    """Fast compact JSON for wire/tool payloads (no spaces, ASCII safe default off)."""
    import json

    return json.dumps(
        value,
        ensure_ascii=False,
        separators=_COMPACT_SEPARATORS,
        allow_nan=False,
        default=str,
    )


def json_loads_object(text: str) -> dict:
    """Parse JSON object; raise ValueError if not an object."""
    import json

    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON: {exc.msg}") from exc
    if not isinstance(parsed, dict):
        raise ValueError("JSON value must be an object")
    return parsed


def json_loads_any(text: str) -> object:
    """Parse JSON any; on failure return the original string (stream-tolerant)."""
    import json

    if not text:
        return text
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


# ---------------------------------------------------------------------------
# Security event ring (for live admin tail)
# ---------------------------------------------------------------------------


class SecurityEventRing:
    """Thread-safe ring buffer of recent security/audit events."""

    __slots__ = ("_buf", "_lock", "_seq")

    def __init__(self, maxlen: int = 500) -> None:
        self._buf: deque[dict] = deque(maxlen=max(10, maxlen))
        self._lock = threading.Lock()
        self._seq = 0

    def append(self, event: str, **fields: object) -> dict:
        with self._lock:
            self._seq += 1
            item = {
                "seq": self._seq,
                "ts": time.time(),
                "event": str(event)[:128],
            }
            for k, v in fields.items():
                if v is None:
                    continue
                key = str(k)[:64]
                if isinstance(v, (int, float, bool)):
                    item[key] = v
                else:
                    item[key] = sanitize_log_fast(str(v), 200)
            self._buf.appendleft(item)
            return item

    def snapshot(self, *, after_seq: int = 0, limit: int = 100) -> list[dict]:
        limit = max(1, min(int(limit), 500))
        with self._lock:
            items = list(self._buf)
        if after_seq > 0:
            items = [i for i in items if int(i.get("seq", 0)) > after_seq]
        # newest first already; keep limit
        return items[:limit]

    def latest_seq(self) -> int:
        with self._lock:
            return self._seq


# Process-wide security ring for admin live tail
security_events = SecurityEventRing(maxlen=500)
