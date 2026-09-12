"""HMAC-SHA256 signing and verification for audit bundles.

Signature covers a *canonical content digest* of ZIP members (sorted by
filename, sha256 of each file body) — not the raw ZIP bytes — so adding
``signature.hmac.json`` does not invalidate the signature.
"""

from __future__ import annotations

import hashlib
import hmac
import io
import json
import os
import time
import zipfile
from typing import Any

try:
    from free_claude_code.native import hmac_sha256_hex as _native_hmac_hex
    from free_claude_code.native import key_id16 as _native_key_id16
    from free_claude_code.native import sha256_hex as _native_sha256_hex
except Exception:  # pragma: no cover
    _native_hmac_hex = None  # type: ignore[assignment]
    _native_key_id16 = None  # type: ignore[assignment]
    _native_sha256_hex = None  # type: ignore[assignment]


ALG = "HMAC-SHA256"
ALG_ED25519 = "Ed25519"
SIG_FORMAT = "fcc-audit-signature"
SIG_FORMAT_ED25519 = "fcc-audit-signature-ed25519"
SIG_VERSION = 1
SIG_NAME = "signature.hmac.json"
SIG_NAME_ED25519 = "signature.ed25519.json"


def signing_key_from_env(env_key: str = "FCC_AUDIT_SIGNING_KEY") -> bytes | None:
    raw = (os.getenv(env_key) or "").strip()
    if not raw:
        return None
    return raw.encode("utf-8")


def key_id_for(key: bytes) -> str:
    """Non-secret key fingerprint for operators to select verify keys."""
    if _native_key_id16 is not None:
        try:
            return _native_key_id16(key)
        except Exception:
            pass
    return hashlib.sha256(key).hexdigest()[:16]


def canonical_content_digest(zip_bytes: bytes) -> str:
    """SHA-256 over sorted name/hex lines (excludes signature files)."""
    lines: list[str] = []
    skip = {SIG_NAME, SIG_NAME_ED25519}
    with zipfile.ZipFile(io.BytesIO(zip_bytes), "r") as zf:
        for name in sorted(zf.namelist()):
            if name in skip or name.endswith("/"):
                continue
            body = zf.read(name)
            digest = hashlib.sha256(body).hexdigest()
            lines.append(f"{name}\0{digest}")
    blob = "\n".join(lines).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()

def sign_bundle(
    zip_bytes: bytes,
    *,
    key: bytes,
    node_id: str = "",
    version: str = "",
) -> dict[str, Any]:
    """Return signature metadata object (embed as signature.hmac.json)."""
    digest = canonical_content_digest(zip_bytes)
    msg = f"fcc-audit-v1|{digest}".encode("utf-8")
    if _native_hmac_hex is not None:
        try:
            sig = _native_hmac_hex(key, msg)
        except Exception:
            sig = hmac.new(key, msg, hashlib.sha256).hexdigest()
    else:
        sig = hmac.new(key, msg, hashlib.sha256).hexdigest()
    return {
        "format": SIG_FORMAT,
        "format_version": SIG_VERSION,
        "alg": ALG,
        "key_id": key_id_for(key),
        "content_digest_sha256": digest,
        "signature": sig,
        "signed_at": time.time(),
        "node_id": str(node_id or "")[:128],
        "version": str(version or "")[:64],
    }


def attach_signature_to_zip(zip_bytes: bytes, signature_obj: dict[str, Any]) -> bytes:
    """Return a new ZIP with signature.hmac.json added."""
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(zip_bytes), "r") as src:
        with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as dst:
            for info in src.infolist():
                if info.filename == SIG_NAME:
                    continue
                dst.writestr(info.filename, src.read(info.filename))
            dst.writestr(
                SIG_NAME,
                json.dumps(signature_obj, indent=2, sort_keys=True).encode("utf-8"),
            )
    return out.getvalue()


def sign_and_attach(
    zip_bytes: bytes,
    *,
    key: bytes,
    node_id: str = "",
    version: str = "",
) -> tuple[bytes, dict[str, Any]]:
    """Sign unsigned ZIP and return (signed_zip, signature_obj)."""
    sig = sign_bundle(zip_bytes, key=key, node_id=node_id, version=version)
    return attach_signature_to_zip(zip_bytes, sig), sig


def extract_signature(zip_bytes: bytes) -> dict[str, Any] | None:
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes), "r") as zf:
            if SIG_NAME not in zf.namelist():
                return None
            return json.loads(zf.read(SIG_NAME))
    except (OSError, zipfile.BadZipFile, json.JSONDecodeError, UnicodeDecodeError):
        return None


def verify_signed_zip(zip_bytes: bytes, *, key: bytes) -> dict[str, Any]:
    """Verify a signed audit ZIP against the given key."""
    sig_obj = extract_signature(zip_bytes)
    if not sig_obj:
        return {"ok": False, "reason": "missing signature.hmac.json"}
    return verify_signature(zip_bytes, sig_obj, key=key)


def verify_signature(
    zip_bytes: bytes,
    signature_obj: dict[str, Any],
    *,
    key: bytes,
) -> dict[str, Any]:
    """Verify HMAC over canonical content digest of the ZIP members."""
    if not isinstance(signature_obj, dict):
        return {"ok": False, "reason": "signature not object"}
    if signature_obj.get("format") != SIG_FORMAT:
        return {"ok": False, "reason": "unknown signature format"}
    if signature_obj.get("alg") != ALG:
        return {"ok": False, "reason": "unsupported alg"}
    expected_kid = key_id_for(key)
    kid = str(signature_obj.get("key_id") or "")
    if kid and kid != expected_kid:
        return {"ok": False, "reason": "key_id mismatch", "expected_key_id": expected_kid}
    digest = canonical_content_digest(zip_bytes)
    claimed = str(
        signature_obj.get("content_digest_sha256")
        or signature_obj.get("digest_sha256")
        or ""
    )
    if not claimed or not hmac.compare_digest(claimed, digest):
        return {"ok": False, "reason": "digest mismatch"}
    msg = f"fcc-audit-v1|{digest}".encode("utf-8")
    if _native_hmac_hex is not None:
        try:
            expected_sig = _native_hmac_hex(key, msg)
        except Exception:
            expected_sig = hmac.new(key, msg, hashlib.sha256).hexdigest()
    else:
        expected_sig = hmac.new(key, msg, hashlib.sha256).hexdigest()
    got = str(signature_obj.get("signature") or "")
    if not got or not hmac.compare_digest(got, expected_sig):
        return {"ok": False, "reason": "bad signature"}
    return {
        "ok": True,
        "reason": "valid",
        "key_id": expected_kid,
        "content_digest_sha256": digest,
        "signed_at": signature_obj.get("signed_at"),
        "node_id": signature_obj.get("node_id"),
    }



def ed25519_seed_from_env(env_key: str = "FCC_AUDIT_ED25519_SEED") -> bytes | None:
    """32-byte seed from hex (64 chars) or raw utf-8 padded/hashed to 32 bytes."""
    raw = (os.getenv(env_key) or "").strip()
    if not raw:
        return None
    if len(raw) == 64 and all(c in "0123456789abcdefABCDEF" for c in raw):
        return bytes.fromhex(raw)
    # derive stable 32-byte seed from passphrase
    return hashlib.sha256(raw.encode("utf-8")).digest()


def sign_bundle_ed25519(
    zip_bytes: bytes,
    *,
    seed: bytes,
    node_id: str = "",
    version: str = "",
) -> dict[str, Any]:
    from free_claude_code.native.ed25519_fast import public_key_from_seed, sign

    digest = canonical_content_digest(zip_bytes)
    msg = f"fcc-audit-ed25519-v1|{digest}".encode("utf-8")
    pub = public_key_from_seed(seed)
    sig = sign(msg, seed)
    return {
        "format": SIG_FORMAT_ED25519,
        "format_version": SIG_VERSION,
        "alg": ALG_ED25519,
        "public_key_hex": pub.hex(),
        "content_digest_sha256": digest,
        "signature_hex": sig.hex(),
        "signed_at": time.time(),
        "node_id": str(node_id or "")[:128],
        "version": str(version or "")[:64],
    }


def attach_ed25519_to_zip(zip_bytes: bytes, signature_obj: dict[str, Any]) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(zip_bytes), "r") as src:
        with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as dst:
            for info in src.infolist():
                if info.filename in {SIG_NAME_ED25519}:
                    continue
                dst.writestr(info.filename, src.read(info.filename))
            dst.writestr(
                SIG_NAME_ED25519,
                json.dumps(signature_obj, indent=2, sort_keys=True).encode("utf-8"),
            )
    return out.getvalue()


def sign_and_attach_ed25519(
    zip_bytes: bytes,
    *,
    seed: bytes,
    node_id: str = "",
    version: str = "",
) -> tuple[bytes, dict[str, Any]]:
    sig = sign_bundle_ed25519(zip_bytes, seed=seed, node_id=node_id, version=version)
    return attach_ed25519_to_zip(zip_bytes, sig), sig


def verify_ed25519_zip(zip_bytes: bytes, *, public_key: bytes | None = None) -> dict[str, Any]:
    """Verify Ed25519 signature; public key from file or argument."""
    from free_claude_code.native.ed25519_fast import verify

    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes), "r") as zf:
            if SIG_NAME_ED25519 not in zf.namelist():
                return {"ok": False, "reason": "missing signature.ed25519.json"}
            sig_obj = json.loads(zf.read(SIG_NAME_ED25519))
    except Exception as exc:
        return {"ok": False, "reason": f"read error: {type(exc).__name__}"}
    if not isinstance(sig_obj, dict) or sig_obj.get("format") != SIG_FORMAT_ED25519:
        return {"ok": False, "reason": "unknown signature format"}
    if sig_obj.get("alg") != ALG_ED25519:
        return {"ok": False, "reason": "unsupported alg"}
    digest = canonical_content_digest(zip_bytes)
    claimed = str(sig_obj.get("content_digest_sha256") or "")
    if not claimed or not hmac.compare_digest(claimed, digest):
        return {"ok": False, "reason": "digest mismatch"}
    pk_hex = str(sig_obj.get("public_key_hex") or "")
    try:
        pk = public_key if public_key is not None else bytes.fromhex(pk_hex)
        sig = bytes.fromhex(str(sig_obj.get("signature_hex") or ""))
    except ValueError:
        return {"ok": False, "reason": "bad hex"}
    msg = f"fcc-audit-ed25519-v1|{digest}".encode("utf-8")
    if not verify(msg, sig, pk):
        return {"ok": False, "reason": "bad signature"}
    return {
        "ok": True,
        "reason": "valid",
        "alg": ALG_ED25519,
        "public_key_hex": pk.hex(),
        "content_digest_sha256": digest,
        "signed_at": sig_obj.get("signed_at"),
        "node_id": sig_obj.get("node_id"),
    }


def verify_any_signed_zip(zip_bytes: bytes, *, hmac_key: bytes | None = None) -> dict[str, Any]:
    """Try Ed25519 first, then HMAC if key provided."""
    ed = verify_ed25519_zip(zip_bytes)
    if ed.get("ok"):
        return ed
    if hmac_key:
        return verify_signed_zip(zip_bytes, key=hmac_key)
    # If only ed missing and no hmac key
    if ed.get("reason") != "missing signature.ed25519.json":
        return ed
    return {"ok": False, "reason": "no verifiable signature present"}


def ed25519_backend() -> str:
    """Return active Ed25519 implementation: cryptography | pure."""
    try:
        from free_claude_code.native.ed25519_fast import backend

        return backend()
    except Exception:
        return "pure"
