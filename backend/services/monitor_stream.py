"""Single-process WebSocket fan-out for monitor updates."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime, timezone
from threading import Lock
from typing import Any, Protocol

from backend.services.monitor_event_service import sanitize_monitor_text

MAX_MONITOR_ERROR_DETAIL_LENGTH = 320


class MonitorWebSocket(Protocol):
    async def accept(self) -> None: ...
    async def send_json(self, payload: dict[str, Any]) -> None: ...


def build_upstream_update(upstream: dict[str, str | None]) -> dict[str, Any]:
    return {
        "type": "upstream.updated",
        "updatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "upstream": dict(upstream),
    }


def _monitor_error_detail(error: Exception | None) -> str | None:
    if error is None:
        return None
    detail = sanitize_monitor_text(str(error)).strip()
    if len(detail) <= MAX_MONITOR_ERROR_DETAIL_LENGTH:
        return detail or None
    return f"{detail[:MAX_MONITOR_ERROR_DETAIL_LENGTH - 3].rstrip()}..."


class MonitorRealtimeStatus:
    def __init__(self) -> None:
        self._lock = Lock()
        self._last_accepted_at: str | None = None
        self._last_reconciled_at: str | None = None
        self._last_error_category: str | None = None
        self._last_error_at: str | None = None
        self._last_error_detail: str | None = None

    def record_accepted(self) -> None:
        with self._lock:
            self._last_accepted_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
            if self._last_error_category != "RECONCILIATION_FAILED":
                self._last_error_category = None
                self._last_error_at = None
                self._last_error_detail = None

    def record_reconciled(self) -> None:
        with self._lock:
            self._last_reconciled_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
            self._last_error_category = None
            self._last_error_at = None
            self._last_error_detail = None

    def record_error(self, category: str, error: Exception | None = None) -> None:
        with self._lock:
            self._last_error_category = category
            self._last_error_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
            self._last_error_detail = _monitor_error_detail(error)

    def as_dict(self) -> dict[str, str | None]:
        with self._lock:
            return {
                "lastAcceptedAt": self._last_accepted_at,
                "lastReconciledAt": self._last_reconciled_at,
                "lastErrorCategory": self._last_error_category,
                "lastErrorAt": self._last_error_at,
                "lastErrorDetail": self._last_error_detail,
            }


class MonitorStreamHub:
    def __init__(
        self,
        *,
        load_update: Callable[[str], dict[str, Any] | None],
        send_timeout_seconds: float = 1.0,
    ) -> None:
        self._load_update = load_update
        self._send_timeout_seconds = send_timeout_seconds
        self._connections: set[MonitorWebSocket] = set()
        self._loop: asyncio.AbstractEventLoop | None = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    async def connect(
        self,
        websocket: MonitorWebSocket,
        snapshot: dict[str, Any],
    ) -> None:
        await websocket.accept()
        self._connections.add(websocket)
        await websocket.send_json(snapshot)

    def disconnect(self, websocket: MonitorWebSocket) -> None:
        self._connections.discard(websocket)

    async def publish_from_async(self, run_id: str) -> None:
        payload = await asyncio.to_thread(self._load_update, run_id)
        if payload is None:
            return
        await self.publish_payload_async(payload)

    async def publish_payload_async(self, payload: dict[str, Any]) -> None:
        stale: list[MonitorWebSocket] = []
        for websocket in self._connections.copy():
            try:
                await asyncio.wait_for(
                    websocket.send_json(payload),
                    timeout=self._send_timeout_seconds,
                )
            except (asyncio.TimeoutError, OSError, RuntimeError):
                stale.append(websocket)
        for websocket in stale:
            self._connections.discard(websocket)

    def publish_background(self, run_id: str) -> asyncio.Task[None]:
        return asyncio.create_task(self.publish_from_async(run_id))

    def publish_payload_background(self, payload: dict[str, Any]) -> asyncio.Task[None]:
        return asyncio.create_task(self.publish_payload_async(payload))

    def publish_from_thread(self, run_id: str) -> None:
        if self._loop is None:
            return
        asyncio.run_coroutine_threadsafe(
            self.publish_from_async(run_id),
            self._loop,
        )

    def publish_payload_from_thread(self, payload: dict[str, Any]) -> None:
        if self._loop is None:
            return
        asyncio.run_coroutine_threadsafe(
            self.publish_payload_async(payload),
            self._loop,
        )
