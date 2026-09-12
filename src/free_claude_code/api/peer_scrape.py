"""Active fan-in peer scrape helpers (SSRF-hardened)."""

from __future__ import annotations

import ipaddress
import os
import re
from typing import Any
from urllib.parse import urlparse

MAX_PEERS = 8
MAX_URL_LEN = 512
DEFAULT_PATH = "/admin/api/security/events/export"
_HOST_RE = re.compile(r"^[a-zA-Z0-9._\-\[\]]+$")


def parse_peer_allowlist(raw: str | None = None) -> set[str]:
    """Parse FCC_FANIN_PEER_ALLOWLIST (comma-separated hosts or host:port)."""
    text = raw if raw is not None else os.getenv("FCC_FANIN_PEER_ALLOWLIST", "")
    out: set[str] = set()
    for part in (text or "").split(","):
        host = part.strip().lower()
        if host:
            out.add(host[:253])
    return out


def _host_allowed(hostname: str, port: int | None, allowlist: set[str]) -> bool:
    if not allowlist:
        return True  # no allowlist = allow any host that passes other checks
    host = hostname.lower().strip("[]")
    candidates = {host}
    if port:
        candidates.add(f"{host}:{port}")
    # also bare allowlist entries
    return bool(candidates & allowlist) or host in allowlist


def _is_blocked_ip(hostname: str) -> bool:
    """Block obviously dangerous destinations (metadata / unspecified)."""
    try:
        ip = ipaddress.ip_address(hostname.strip("[]"))
    except ValueError:
        return False  # hostname — DNS resolved by httpx; allowlist is primary control
    # Block unspecified and multicast; link-local optional block
    if ip.is_unspecified or ip.is_multicast:
        return True
    # Cloud metadata common address
    if str(ip) in {"169.254.169.254", "fd00:ec2::254"}:
        return True
    return False


def normalize_peer_url(raw: str, *, allowlist: set[str] | None = None) -> str:
    """Validate and normalize a peer base or full export URL.

    Raises ValueError on rejection.
    """
    if not isinstance(raw, str):
        raise ValueError("url must be string")
    url = raw.strip()[:MAX_URL_LEN]
    if not url:
        raise ValueError("empty url")
    try:
        from free_claude_code.native import peer_url_ok as _peer_ok
        if not _peer_ok(url):
            raise ValueError("peer URL rejected by native gate")
    except ImportError:
        pass
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("only http/https allowed")
    if parsed.username or parsed.password:
        raise ValueError("credentials in URL not allowed")
    if parsed.fragment:
        raise ValueError("fragments not allowed")
    hostname = parsed.hostname
    if not hostname or not _HOST_RE.match(hostname.strip("[]")):
        raise ValueError("invalid host")
    if _is_blocked_ip(hostname):
        raise ValueError("blocked destination")
    port = parsed.port
    al = allowlist if allowlist is not None else parse_peer_allowlist()
    if not _host_allowed(hostname, port, al):
        raise ValueError("host not in FCC_FANIN_PEER_ALLOWLIST")

    path = parsed.path or ""
    if not path or path == "/":
        path = DEFAULT_PATH
    # only allow admin API paths under /admin/api/
    if not path.startswith("/admin/api/"):
        raise ValueError("path must be under /admin/api/")
    if ".." in path or "\\" in path:
        raise ValueError("invalid path")
    # rebuild without query params that could smuggle (allow none)
    netloc = parsed.netloc
    return f"{parsed.scheme}://{netloc}{path}"


def normalize_peer_list(peers: Any, *, allowlist: set[str] | None = None) -> list[str]:
    if not isinstance(peers, list):
        raise ValueError("peers must be a list")
    if len(peers) > MAX_PEERS:
        raise ValueError(f"max {MAX_PEERS} peers")
    out: list[str] = []
    seen: set[str] = set()
    for item in peers:
        url = normalize_peer_url(str(item), allowlist=allowlist)
        if url not in seen:
            seen.add(url)
            out.append(url)
    return out


def scrape_result(
    *,
    url: str,
    ok: bool,
    status_code: int | None = None,
    error: str | None = None,
    node_id: str | None = None,
    event_count: int = 0,
    ingested: bool = False,
) -> dict[str, Any]:
    return {
        "url": url[:MAX_URL_LEN],
        "ok": bool(ok),
        "status_code": status_code,
        "error": (error or None) and str(error)[:200],
        "node_id": node_id,
        "event_count": int(event_count or 0),
        "ingested": bool(ingested),
    }



def mtls_client_kwargs_from_env() -> dict:
    """Optional mTLS client certs for peer scrape (httpx).

    Env:
      FCC_FANIN_MTLS_CERT — path to client cert PEM
      FCC_FANIN_MTLS_KEY — path to client key PEM
      FCC_FANIN_MTLS_CA — path to CA bundle (verify)
    """
    import os
    from pathlib import Path as _P

    cert = (os.getenv("FCC_FANIN_MTLS_CERT") or "").strip()
    key = (os.getenv("FCC_FANIN_MTLS_KEY") or "").strip()
    ca = (os.getenv("FCC_FANIN_MTLS_CA") or "").strip()
    kwargs: dict = {}
    if cert and key and _P(cert).is_file() and _P(key).is_file():
        kwargs["cert"] = (cert, key)
    if ca and _P(ca).is_file():
        kwargs["verify"] = ca
    return kwargs
