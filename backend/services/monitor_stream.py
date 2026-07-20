"""Single-process WebSocket fan-out for monitor updates."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime, timezone
from threading import Lock
from typing import Any, Protocol


class MonitorWebSocket(Protocol):
    async def accept(self) -> None: ...
    async def send_json(self, payload: dict[str, Any]) -> None: ...


class MonitorRealtimeStatus:
    def __init__(self) -> None:
        self._lock = Lock()
        self._last_accepted_at: str | None = None
        self._last_reconciled_at: str | None = None
        self._last_error_category: str | None = None

    def record_accepted(self) -> None:
        with self._lock:
            self._last_accepted_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
            self._last_error_category = None

    def record_reconciled(self) -> None:
        with self._lock:
            self._last_reconciled_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
            self._last_error_category = None

    def record_error(self, category: str) -> None:
        with self._lock:
            self._last_error_category = category

    def as_dict(self) -> dict[str, str | None]:
        with self._lock:
            return {
                "lastAcceptedAt": self._last_accepted_at,
                "lastReconciledAt": self._last_reconciled_at,
                "lastErrorCategory": self._last_error_category,
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

    def publish_from_thread(self, run_id: str) -> None:
        if self._loop is None:
            return
        asyncio.run_coroutine_threadsafe(
            self.publish_from_async(run_id),
            self._loop,
        )
