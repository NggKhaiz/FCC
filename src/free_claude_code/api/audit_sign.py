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


ALG = "HMAC-SHA256"
SIG_FORMAT = "fcc-audit-signature"
SIG_VERSION = 1
SIG_NAME = "signature.hmac.json"


def signing_key_from_env(env_key: str = "FCC_AUDIT_SIGNING_KEY") -> bytes | None:
    raw = (os.getenv(env_key) or "").strip()
    if not raw:
        return None
    return raw.encode("utf-8")


def key_id_for(key: bytes) -> str:
    """Non-secret key fingerprint for operators to select verify keys."""
    return hashlib.sha256(key).hexdigest()[:16]


def canonical_content_digest(zip_bytes: bytes) -> str:
    """SHA-256 over sorted ``name\\0hexdigest\\n`` lines (excludes signature file)."""
    lines: list[str] = []
    with zipfile.ZipFile(io.BytesIO(zip_bytes), "r") as zf:
        for name in sorted(zf.namelist()):
            if name == SIG_NAME or name.endswith("/"):
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
