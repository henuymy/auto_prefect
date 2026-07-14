"""Shared incident reporting for business-triggered session failures."""

from __future__ import annotations

from collections.abc import Callable
import logging
from pathlib import Path
from typing import TypeVar

from prefect.runtime import flow_run

from services.session_alert_service import (
    dispatch_session_notification,
    notify_session_failure,
    notify_session_recovery,
)
from services.session_manager import (
    SessionInfrastructureError,
    SessionLoginError,
    summarize_login_failure,
)
from services.session_retry_service import is_session_expired_error
from utils.config_loader import load_json_with_local_override


PROJECT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_KEEPER_CONFIG_PATH = "config/modules/session_keeper.json"
T = TypeVar("T")
LOGGER = logging.getLogger(__name__)

FAILURE_REASON_SUMMARIES = {
    "authentication": (
        "认证失效：探活或下载响应已明确表明会话无效。"
        "已在共享登录锁内自动刷新一次，并且只重试原失败请求一次。"
    ),
    "infrastructure": (
        "基础设施异常：请求未能可靠完成，可能涉及网络、DNS、TLS、代理、限流或上游服务。"
        "不会自动重登，请检查连接和上游服务。"
    ),
}


def _is_reportable_session_failure(exc):
    return isinstance(exc, (SessionLoginError, SessionInfrastructureError)) or (
        isinstance(exc, RuntimeError) and is_session_expired_error(exc)
    )


def build_business_session_incident(
    exc,
    *,
    trigger_source,
    flow_name,
    flow_run_name,
    flow_run_id,
    affected_stage,
):
    if isinstance(exc, SessionLoginError):
        category = "authentication"
        errors = exc.errors
        attempt_count = exc.attempt_count
    elif isinstance(exc, SessionInfrastructureError):
        category = "infrastructure"
        errors = [str(exc)]
        attempt_count = 0
    elif isinstance(exc, RuntimeError) and is_session_expired_error(exc):
        category = "authentication"
        errors = [summarize_login_failure(exc)]
        attempt_count = 0
    else:
        raise TypeError("unsupported session failure")
    error_summary = "；".join(errors) or "未提供错误摘要"
    return {
        "incident_key": f"{category}:shared-session",
        "trigger_source": trigger_source,
        "failure_category": category,
        "failure_reason": f"{FAILURE_REASON_SUMMARIES[category]} 原始判定: {error_summary}",
        "failed_stages": [affected_stage],
        "attempt_count": attempt_count,
        "errors": errors,
        "flow_name": flow_name,
        "flow_run_name": flow_run_name,
        "flow_run_id": flow_run_id,
        "next_scheduled_at": "next session-keeper schedule",
    }


def report_business_session_failure(
    exc,
    *,
    trigger_source,
    flow_name,
    flow_run_name,
    flow_run_id,
    affected_stage,
    base_dir=PROJECT_DIR,
    keeper_config_path=DEFAULT_KEEPER_CONFIG_PATH,
):
    keeper, _ = load_json_with_local_override(base_dir / keeper_config_path)
    alert_source, _ = load_json_with_local_override(base_dir / keeper["alert_config_path"])
    alert_config = {
        "webhook_url": alert_source["wecom"]["webhook_url"],
        "incident_state_path": keeper["incident_state_path"],
    }
    incident = build_business_session_incident(
        exc,
        trigger_source=trigger_source,
        flow_name=flow_name,
        flow_run_name=flow_run_name,
        flow_run_id=flow_run_id,
        affected_stage=affected_stage,
    )
    return notify_session_failure(alert_config, incident, base_dir=base_dir)


def recover_business_session_incidents(
    alert_config,
    *,
    recovery,
    base_dir=PROJECT_DIR,
    notifier=notify_session_recovery,
):
    return notifier(alert_config, recovery, base_dir=base_dir)


def report_business_session_recovery(
    *,
    trigger_source,
    flow_name,
    flow_run_name,
    flow_run_id,
    base_dir=PROJECT_DIR,
    keeper_config_path=DEFAULT_KEEPER_CONFIG_PATH,
):
    keeper, _ = load_json_with_local_override(base_dir / keeper_config_path)
    alert_source, _ = load_json_with_local_override(base_dir / keeper["alert_config_path"])
    return recover_business_session_incidents(
        {
            "webhook_url": alert_source["wecom"]["webhook_url"],
            "incident_state_path": keeper["incident_state_path"],
        },
        recovery={
            "flow_run_id": flow_run_id,
            "flow_name": flow_name,
            "flow_run_name": flow_run_name,
            "trigger_source": trigger_source,
        },
        base_dir=base_dir,
    )


def _current_flow_run_context(flow_name):
    return {
        "flow_name": flow_name or str(getattr(flow_run, "flow_name", None) or "unknown-flow"),
        "flow_run_name": str(getattr(flow_run, "name", None) or "manual"),
        "flow_run_id": str(flow_run.id or "manual"),
    }


def _dispatch_without_affecting_flow(dispatcher, notification, *, notification_type):
    try:
        return dispatcher(notification, notification_type=notification_type)
    except Exception as exc:
        LOGGER.warning(
            "企业微信会话%s通知派发失败，保留原始业务结果: notification_error_type=%s",
            notification_type,
            type(exc).__name__,
        )
        return None


def run_with_business_session_reporting(
    operation: Callable[[], T],
    *,
    trigger_source: str,
    flow_name: str,
    affected_stage: str,
    reporter=None,
    recover_on_success=False,
    recoverer=None,
    recovery_predicate=None,
    notification_dispatcher=dispatch_session_notification,
) -> T:
    flow_context = _current_flow_run_context(flow_name)
    try:
        result = operation()
    except RuntimeError as exc:
        if not _is_reportable_session_failure(exc):
            raise
        primary_failure = exc
        active_reporter = reporter or report_business_session_failure
        _dispatch_without_affecting_flow(
            notification_dispatcher,
            lambda: active_reporter(
                primary_failure,
                trigger_source=trigger_source,
                affected_stage=affected_stage,
                **flow_context,
            ),
            notification_type="failure",
        )
        raise
    should_recover = recovery_predicate(result) if recovery_predicate else True
    if recover_on_success and should_recover:
        active_recoverer = recoverer or report_business_session_recovery
        _dispatch_without_affecting_flow(
            notification_dispatcher,
            lambda: active_recoverer(trigger_source=trigger_source, **flow_context),
            notification_type="recovery",
        )
    return result
