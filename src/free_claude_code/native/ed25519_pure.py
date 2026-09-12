"""Pure-Python Ed25519 (RFC 8032) — no external crypto deps.

Used for audit-bundle asymmetric signatures when ``cryptography`` is absent.
Performance is secondary to correctness; prefer system cryptography when present.
"""

from __future__ import annotations

import hashlib
import os

# Curve25519 prime
_P = 2**255 - 19
_L = 2**252 + 27742317777372353535851937790883648493
_D = -121665 * pow(121666, _P - 2, _P) % _P
_I = pow(2, (_P - 1) // 4, _P)


def _inv(x: int) -> int:
    return pow(x, _P - 2, _P)


def _xrecover(y: int) -> int:
    xx = (y * y - 1) * _inv(_D * y * y + 1)
    x = pow(xx, (_P + 3) // 8, _P)
    if (x * x - xx) % _P != 0:
        x = (x * _I) % _P
    if x % 2 != 0:
        x = _P - x
    return x


_BY = 4 * _inv(5) % _P
_BX = _xrecover(_BY)
_B = (_BX % _P, _BY % _P, 1, (_BX * _BY) % _P)


def _edwards_add(p, q):  # noqa: ANN001
    x1, y1, z1, t1 = p
    x2, y2, z2, t2 = q
    a = (y1 - x1) * (y2 - x2) % _P
    b = (y1 + x1) * (y2 + x2) % _P
    c = t1 * 2 * _D * t2 % _P
    d = z1 * 2 * z2 % _P
    e = b - a
    f = d - c
    g = d + c
    h = b + a
    return (e * f % _P, g * h % _P, f * g % _P, e * h % _P)


def _scalarmult(p, e: int):  # noqa: ANN001
    if e == 0:
        return (0, 1, 1, 0)
    q = _scalarmult(p, e // 2)
    q = _edwards_add(q, q)
    if e & 1:
        q = _edwards_add(q, p)
    return q


def _encodeint(y: int) -> bytes:
    return y.to_bytes(32, "little")


def _encodepoint(p) -> bytes:  # noqa: ANN001
    x, y, z, _t = p
    zi = _inv(z)
    x = (x * zi) % _P
    y = (y * zi) % _P
    bits = bytearray(_encodeint(y))
    bits[31] = (bits[31] & 0x7F) | ((x & 1) << 7)
    return bytes(bits)


def _bit(h: bytes, i: int) -> int:
    return (h[i // 8] >> (i % 8)) & 1


def _hint(m: bytes) -> int:
    return int.from_bytes(hashlib.sha512(m).digest(), "little")


def _decodeint(s: bytes) -> int:
    return int.from_bytes(s, "little")


def _is_on_curve(p) -> bool:  # noqa: ANN001
    x, y, z, t = p
    return (
        z % _P != 0
        and x * y % _P == z * t % _P
        and (y * y - x * x - z * z - _D * t * t) % _P == 0
    )


def _decodepoint(s: bytes) -> tuple:
    if len(s) != 32:
        raise ValueError("bad point length")
    y = _decodeint(s) & ((1 << 255) - 1)
    x = _xrecover(y)
    if x & 1 != _bit(s, 255):
        x = _P - x
    p = (x, y, 1, (x * y) % _P)
    if not _is_on_curve(p):
        raise ValueError("point off curve")
    return p


def generate_keypair(seed: bytes | None = None) -> tuple[bytes, bytes]:
    """Return (private_seed_32, public_key_32)."""
    if seed is None:
        seed = os.urandom(32)
    if len(seed) != 32:
        raise ValueError("seed must be 32 bytes")
    h = hashlib.sha512(seed).digest()
    a = int.from_bytes(h[:32], "little")
    a &= (1 << 254) - 8
    a |= 1 << 254
    pub = _encodepoint(_scalarmult(_B, a))
    return seed, pub


def public_key_from_seed(seed: bytes) -> bytes:
    _, pub = generate_keypair(seed)
    return pub


def sign(message: bytes, seed: bytes) -> bytes:
    """Detached 64-byte signature."""
    if len(seed) != 32:
        raise ValueError("seed must be 32 bytes")
    h = hashlib.sha512(seed).digest()
    a = int.from_bytes(h[:32], "little")
    a &= (1 << 254) - 8
    a |= 1 << 254
    pub = _encodepoint(_scalarmult(_B, a))
    r = _hint(h[32:] + message)
    r_point = _scalarmult(_B, r)
    R = _encodepoint(r_point)
    s = (r + _hint(R + pub + message) * a) % _L
    return R + _encodeint(s)


def verify(message: bytes, signature: bytes, public_key: bytes) -> bool:
    """Verify detached signature. Returns False on any failure."""
    try:
        if len(signature) != 64 or len(public_key) != 32:
            return False
        R = _decodepoint(signature[:32])
        A = _decodepoint(public_key)
        s = _decodeint(signature[32:])
        if s >= _L:
            return False
        h = _hint(signature[:32] + public_key + message)
        sB = _scalarmult(_B, s)
        hA = _scalarmult(A, h)
        return _edwards_add(R, hA) == sB or _encodepoint(_edwards_add(R, hA)) == _encodepoint(
            sB
        )
    except Exception:
        return False
