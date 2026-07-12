"""Bounded business-operation retry after an explicit session failure."""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar


T = TypeVar("T")

SESSION_EXPIRED_MARKERS = (
    "session expired",
    "session_expired",
    "session 已过期",
    "http 302",
    "http 401",
    "http 403",
    "recode=1101",
    "登录超时",
    "请重新登录",
    "登录页",
)


def is_session_expired_error(exc: Exception) -> bool:
    message = str(exc or "").lower()
    return any(marker in message for marker in SESSION_EXPIRED_MARKERS)


def run_with_session_refresh_once(
    operation: Callable[[], T],
    refresh_session: Callable[[], dict],
) -> T:
    try:
        return operation()
    except RuntimeError as exc:
        if not is_session_expired_error(exc):
            raise
        refresh_result = refresh_session()
        if refresh_result.get("status") == "invalid":
            raise RuntimeError(f"重新登录失败: {refresh_result.get('reason')}") from exc
        return operation()
