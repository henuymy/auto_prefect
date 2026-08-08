"""Track active dashboard browser connections by heartbeat."""

from __future__ import annotations

from re import fullmatch
from threading import Lock
from time import monotonic


PRESENCE_TTL_SECONDS = 45.0
_CONNECTION_ID_PATTERN = r"[A-Za-z0-9_-]{16,128}"
_presence_lock = Lock()
_active_connections: dict[str, float] = {}


def _normalize_connection_id(value: str) -> str:
    connection_id = value.strip()
    if not fullmatch(_CONNECTION_ID_PATTERN, connection_id):
        raise ValueError("connection_id 格式无效")
    return connection_id


def _remove_expired(now: float, ttl_seconds: float) -> None:
    cutoff = now - ttl_seconds
    for connection_id, last_seen in list(_active_connections.items()):
        if last_seen <= cutoff:
            _active_connections.pop(connection_id, None)


def record_dashboard_presence(
    connection_id: str,
    *,
    now: float | None = None,
    ttl_seconds: float = PRESENCE_TTL_SECONDS,
) -> dict[str, int]:
    """Record a heartbeat and return the number of currently active connections."""
    normalized_id = _normalize_connection_id(connection_id)
    current_time = monotonic() if now is None else now
    with _presence_lock:
        _remove_expired(current_time, ttl_seconds)
        _active_connections[normalized_id] = current_time
        return {"active_connections": len(_active_connections)}


def clear_dashboard_presence() -> None:
    """Clear in-memory state for tests and controlled process lifecycle resets."""
    with _presence_lock:
        _active_connections.clear()
