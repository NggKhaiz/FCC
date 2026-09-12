"""Continuous multi-hub mesh sync scheduler (in-process asyncio task)."""

from __future__ import annotations

import asyncio
import os
import time
from typing import Any

try:
    from loguru import logger
except Exception:  # pragma: no cover - tests without deps
    class _L:
        def info(self, *a, **k): pass
        def warning(self, *a, **k): pass
    logger = _L()  # type: ignore[assignment]

MIN_INTERVAL_SEC = 15.0
MAX_INTERVAL_SEC = 3600.0
DEFAULT_INTERVAL_SEC = 60.0


class MeshSyncScheduler:
    """Periodically pull registered mesh hubs into the local fan-in hub."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._task: asyncio.Task[None] | None = None
        self._enabled = False
        self._interval = DEFAULT_INTERVAL_SEC
        self._last_run_at: float | None = None
        self._last_ok = 0
        self._last_total = 0
        self._last_error: str | None = None
        self._runs = 0

    def status(self) -> dict[str, Any]:
        return {
            "format": "fcc-mesh-sync",
            "format_version": 1,
            "enabled": self._enabled,
            "running": self._task is not None and not self._task.done(),
            "interval_seconds": self._interval,
            "last_run_at": self._last_run_at,
            "last_ok": self._last_ok,
            "last_total": self._last_total,
            "last_error": self._last_error,
            "runs": self._runs,
            "ts": time.time(),
        }

    async def start(self, *, interval_seconds: float | None = None) -> dict[str, Any]:
        async with self._lock:
            if interval_seconds is not None:
                self._interval = float(
                    max(MIN_INTERVAL_SEC, min(MAX_INTERVAL_SEC, float(interval_seconds)))
                )
            else:
                env_iv = os.getenv("FCC_MESH_SYNC_INTERVAL", "").strip()
                if env_iv:
                    try:
                        self._interval = float(
                            max(MIN_INTERVAL_SEC, min(MAX_INTERVAL_SEC, float(env_iv)))
                        )
                    except ValueError:
                        pass
            self._enabled = True
            if self._task is None or self._task.done():
                self._task = asyncio.create_task(self._loop(), name="fcc-mesh-sync")
            return self.status()

    async def stop(self) -> dict[str, Any]:
        async with self._lock:
            self._enabled = False
            task = self._task
            self._task = None
        if task is not None:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        return self.status()

    async def run_once(self) -> dict[str, Any]:
        """Execute one pull cycle (also used by the loop)."""
        from .console_fanin import fanin_hub
        from .hub_mesh import hub_mesh
        from .peer_scrape import mtls_client_kwargs_from_env, normalize_peer_url, scrape_result

        try:
            import httpx
        except ImportError:
            self._last_run_at = time.time()
            self._last_error = "httpx missing"
            self._runs += 1
            return {
                "ok": False,
                "results": [],
                "message": "httpx not installed",
                "mesh": hub_mesh.snapshot(),
                "fanin": fanin_hub.summary(),
            }

        path = "/admin/api/security/events/export"
        targets = hub_mesh.pull_targets(limit=16)
        results: list[dict[str, Any]] = []
        if not targets:
            self._last_run_at = time.time()
            self._last_ok = 0
            self._last_total = 0
            self._last_error = "no targets"
            self._runs += 1
            return {
                "ok": True,
                "results": [],
                "message": "no targets",
                "mesh": hub_mesh.snapshot(),
                "fanin": fanin_hub.summary(),
            }

        default_token = os.getenv("FCC_ADMIN_API_TOKEN", "").strip()
        _mtls = mtls_client_kwargs_from_env()
        async with httpx.AsyncClient(timeout=3.0, follow_redirects=False, **_mtls) as client:
            for t in targets:
                base = str(t.get("base_url") or "").rstrip("/")
                hub_id = str(t.get("hub_id") or "")
                try:
                    if "/admin/api/" in base:
                        url = normalize_peer_url(base)
                    else:
                        url = normalize_peer_url(base + path)
                except ValueError as exc:
                    results.append(
                        scrape_result(url=base, ok=False, error=str(exc)[:200], node_id=hub_id)
                    )
                    continue
                headers: dict[str, str] = {}
                token = str(t.get("token") or "").strip() or default_token
                if token:
                    headers["X-FCC-Admin-Token"] = token
                try:
                    resp = await client.get(url, headers=headers)
                    if resp.status_code != 200:
                        results.append(
                            scrape_result(
                                url=url,
                                ok=False,
                                status_code=resp.status_code,
                                error=f"HTTP {resp.status_code}",
                                node_id=hub_id,
                            )
                        )
                        continue
                    payload = resp.json()
                    if not isinstance(payload, dict):
                        results.append(
                            scrape_result(
                                url=url, ok=False, status_code=resp.status_code, error="JSON object required", node_id=hub_id
                            )
                        )
                        continue
                    if not payload.get("node_id"):
                        payload["node_id"] = hub_id or "peer"
                    try:
                        ing = fanin_hub.ingest(payload)
                    except ValueError as exc:
                        results.append(
                            scrape_result(url=url, ok=False, error=str(exc)[:200], node_id=hub_id)
                        )
                        continue
                    # refresh mesh entry without clobbering token
                    hub_mesh.register(
                        {
                            "hub_id": hub_id or payload.get("node_id"),
                            "base_url": base,
                            "version": payload.get("version"),
                            "nodes_tracked": int(ing.get("event_count") or 0),
                            "summary": {"source": "mesh_sync"},
                        }
                    )
                    results.append(
                        scrape_result(
                            url=url,
                            ok=True,
                            status_code=resp.status_code,
                            node_id=ing.get("node_id"),
                            event_count=int(ing.get("event_count") or 0),
                            ingested=True,
                        )
                    )
                except httpx.TimeoutException:
                    results.append(scrape_result(url=url, ok=False, error="timeout", node_id=hub_id))
                except Exception as exc:
                    results.append(
                        scrape_result(url=url, ok=False, error=type(exc).__name__, node_id=hub_id)
                    )

        ok = sum(1 for r in results if r.get("ok"))
        self._last_run_at = time.time()
        self._last_ok = ok
        self._last_total = len(results)
        self._last_error = None if ok else "all failed" if results else "no targets"
        self._runs += 1
        return {
            "ok": True,
            "results": results,
            "mesh": hub_mesh.snapshot(),
            "fanin": fanin_hub.summary(),
        }

    async def _loop(self) -> None:
        logger.info("mesh sync scheduler started interval={}s", self._interval)
        try:
            while self._enabled:
                try:
                    await self.run_once()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    self._last_error = type(exc).__name__
                    logger.warning("mesh sync cycle error: {}", exc)
                # sleep in chunks so stop() is responsive
                remaining = self._interval
                while remaining > 0 and self._enabled:
                    step = min(1.0, remaining)
                    await asyncio.sleep(step)
                    remaining -= step
        except asyncio.CancelledError:
            logger.info("mesh sync scheduler cancelled")
            raise
        finally:
            logger.info("mesh sync scheduler stopped")


mesh_scheduler = MeshSyncScheduler()


def autostart_from_env() -> bool:
    """Return True if FCC_MESH_SYNC_AUTO=1."""
    return os.getenv("FCC_MESH_SYNC_AUTO", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
