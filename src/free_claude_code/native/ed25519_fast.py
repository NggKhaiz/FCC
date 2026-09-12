"""Ed25519 sign/verify with optional ``cryptography`` fast-path.

Import order:
1. ``cryptography`` (OpenSSL-backed) when installed
2. Pure-Python RFC 8032 twin (``ed25519_pure``) always available

Callers should use this module only — never branch on backend themselves
unless they need ``backend()`` for diagnostics.
"""

from __future__ import annotations

from typing import Callable

_BACKEND = "pure"
_sign: Callable[[bytes, bytes], bytes]
_verify: Callable[[bytes, bytes, bytes], bool]
_public_from_seed: Callable[[bytes], bytes]
_generate: Callable[[bytes | None], tuple[bytes, bytes]]


def _load_cryptography() -> bool:
    global _BACKEND, _sign, _verify, _public_from_seed, _generate
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PrivateKey,
            Ed25519PublicKey,
        )
        from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
    except Exception:
        return False

    def public_key_from_seed(seed: bytes) -> bytes:
        if len(seed) != 32:
            raise ValueError("seed must be 32 bytes")
        sk = Ed25519PrivateKey.from_private_bytes(seed)
        return sk.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)

    def sign(message: bytes, seed: bytes) -> bytes:
        if len(seed) != 32:
            raise ValueError("seed must be 32 bytes")
        sk = Ed25519PrivateKey.from_private_bytes(seed)
        return sk.sign(message)

    def verify(message: bytes, signature: bytes, public_key: bytes) -> bool:
        try:
            if len(signature) != 64 or len(public_key) != 32:
                return False
            pk = Ed25519PublicKey.from_public_bytes(public_key)
            pk.verify(signature, message)
            return True
        except Exception:
            return False

    def generate_keypair(seed: bytes | None = None) -> tuple[bytes, bytes]:
        import os

        if seed is None:
            seed = os.urandom(32)
        if len(seed) != 32:
            raise ValueError("seed must be 32 bytes")
        return seed, public_key_from_seed(seed)

    _sign = sign
    _verify = verify
    _public_from_seed = public_key_from_seed
    _generate = generate_keypair
    _BACKEND = "cryptography"
    return True


def _load_pure() -> None:
    global _BACKEND, _sign, _verify, _public_from_seed, _generate
    from free_claude_code.native import ed25519_pure as pure

    _sign = pure.sign
    _verify = pure.verify
    _public_from_seed = pure.public_key_from_seed
    _generate = pure.generate_keypair
    _BACKEND = "pure"


if not _load_cryptography():
    _load_pure()


def backend() -> str:
    """Return ``\"cryptography\"`` or ``\"pure\"``."""
    return _BACKEND


def sign(message: bytes, seed: bytes) -> bytes:
    return _sign(message, seed)


def verify(message: bytes, signature: bytes, public_key: bytes) -> bool:
    return _verify(message, signature, public_key)


def public_key_from_seed(seed: bytes) -> bytes:
    return _public_from_seed(seed)


def generate_keypair(seed: bytes | None = None) -> tuple[bytes, bytes]:
    return _generate(seed)
