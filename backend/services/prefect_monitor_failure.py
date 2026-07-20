"""Translate generic Prefect failures for the monitor's business view."""

from __future__ import annotations

from dataclasses import dataclass

from backend.services.monitor_event_service import sanitize_monitor_text


STARTUP_FAILURE_MARKERS = (
    "flow run could not start",
    "unhandled errors in a taskgroup",
)
STARTUP_FAILURE_SUMMARY = "调度服务未能启动本次通报，未开始执行。"


@dataclass(frozen=True)
class PrefectFailure:
    business_summary: str | None
    technical_summary: str | None
    current_step: str
    is_startup_failure: bool


def classify_prefect_failure(message: str | None) -> PrefectFailure:
    """Keep sanitized technical text while translating generic startup failures."""
    technical_summary = sanitize_monitor_text(message).strip() or None
    normalized = (technical_summary or "").lower()
    is_startup_failure = any(marker in normalized for marker in STARTUP_FAILURE_MARKERS)
    return PrefectFailure(
        business_summary=STARTUP_FAILURE_SUMMARY if is_startup_failure else technical_summary,
        technical_summary=technical_summary,
        current_step="调度初始化" if is_startup_failure else "运行失败",
        is_startup_failure=is_startup_failure,
    )