"""Translate generic Prefect failures for the monitor's business view."""

from __future__ import annotations

from dataclasses import dataclass

from backend.services.monitor_event_service import (
    BUSINESS_ERROR_SUMMARY_MAX_CHARS,
    TECHNICAL_ERROR_SUMMARY_MAX_CHARS,
    sanitize_monitor_text,
)


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
    sanitized_message = sanitize_monitor_text(message).strip()
    technical_summary = sanitize_monitor_text(
        sanitized_message,
        max_chars=TECHNICAL_ERROR_SUMMARY_MAX_CHARS,
    ).strip() or None
    normalized = sanitized_message.lower()
    is_startup_failure = any(marker in normalized for marker in STARTUP_FAILURE_MARKERS)
    return PrefectFailure(
        business_summary=(
            STARTUP_FAILURE_SUMMARY
            if is_startup_failure
            else sanitize_monitor_text(
                sanitized_message,
                max_chars=BUSINESS_ERROR_SUMMARY_MAX_CHARS,
            ).strip() or None
        ),
        technical_summary=technical_summary,
        current_step="调度初始化" if is_startup_failure else "运行失败",
        is_startup_failure=is_startup_failure,
    )
