"""Dashboard V2 trigger, batch lifecycle, and session preparation."""

from __future__ import annotations

import copy
import json
import logging
import re
from contextlib import nullcontext
from datetime import datetime
from pathlib import Path
from time import perf_counter
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from infrastructure.dashboard_v2_run_store import MySQLV2CollectionRunStore
from services.dashboard_failure_report import (
    DEFAULT_FAILURE_DIRECTORY,
    write_dashboard_failure_report,
)
from services.runtime_paths import is_runtime_relative_path, resolve_runtime_relative_path
from services.session_broker import StageSessionBroker
from services.session_manager import file_lock, prepare_session
from utils.config_loader import load_json_with_local_override


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "dashboard" / "session.json"
SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
VALID_TRIGGER_TYPES = {"MANUAL", "SCHEDULED"}
SENSITIVE_PATTERN = re.compile(
    r"(?i)(password|token|cookie|authorization|uaptoken)(\s*[=:]\s*)([^,\s;]+)"
)


def resolve_project_path(value: str | Path, base_dir: Path = PROJECT_ROOT) -> Path:
    path = Path(value)
    if is_runtime_relative_path(path):
        return resolve_runtime_relative_path(path)
    return path if path.is_absolute() else (base_dir / path).resolve()


def read_json(path: str | Path) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as file:
        return json.load(file)


def now_shanghai() -> datetime:
    return datetime.now(SHANGHAI_TZ)


def generate_batch_no(now: datetime | None = None) -> str:
    current = now or now_shanghai()
    return f"dashboard-{current.strftime('%Y%m%d-%H%M%S')}-{uuid4().hex[:8]}"


def normalize_trigger_type(value: str) -> str:
    normalized = str(value or "").strip().upper()
    if normalized not in VALID_TRIGGER_TYPES:
        allowed = ", ".join(sorted(VALID_TRIGGER_TYPES))
        raise ValueError(f"trigger_type 只支持 {allowed}: {value!r}")
    return normalized


def sanitize_error(value: object, limit: int = 2000) -> str:
    text = SENSITIVE_PATTERN.sub(r"\1\2***", str(value or ""))
    return text[-limit:]


def load_dashboard_config(config_path: str | Path = DEFAULT_CONFIG_PATH) -> tuple[dict[str, Any], Path]:
    resolved = resolve_project_path(config_path)
    dashboard_config, _ = load_json_with_local_override(resolved)
    if dashboard_config.get("schema_version") != 2:
        raise ValueError("dashboard schema_version 只支持 2")
    return dashboard_config, resolved


def build_city_ops_login_config(
    dashboard_config: dict[str, Any],
    config_dir: Path,
) -> dict[str, Any]:
    autologin_path = resolve_project_path(
        dashboard_config.get("autologin_config_path", "config/modules/autologin.json"),
        PROJECT_ROOT,
    )
    autologin_config = copy.deepcopy(read_json(autologin_path))
    required_stage = str(dashboard_config.get("required_stage") or "city_ops").strip()
    stage_probe = (autologin_config.get("stage_probes") or {}).get(required_stage)
    if not stage_probe:
        raise ValueError(f"自动登录配置缺少 {required_stage!r} 探活定义: {autologin_path}")
    autologin_config["required_stages"] = [required_stage]
    autologin_config["stage_probes"] = {required_stage: stage_probe}
    return autologin_config


def build_run_store(dashboard_config: dict[str, Any]) -> MySQLV2CollectionRunStore:
    return MySQLV2CollectionRunStore()


def execute_session_phase(
    config_path: str | Path = DEFAULT_CONFIG_PATH,
    trigger_type: str = "SCHEDULED",
    force_refresh: bool = False,
    batch_no: str | None = None,
    event_logger: Any = None,
    acquire_collection_lock: bool = True,
    run_type: str = "REALTIME",
) -> dict[str, Any]:
    dashboard_config, resolved_config_path = load_dashboard_config(config_path)
    normalized_trigger = normalize_trigger_type(trigger_type)
    batch_no = batch_no or generate_batch_no()
    store = build_run_store(dashboard_config)
    record_created = False
    try:
        store.create(batch_no, normalized_trigger, run_type=run_type)
        record_created = True
        started_at = now_shanghai()
        store.update(
            batch_no,
            status="RUNNING",
            phase="SESSION",
            started_at=started_at,
        )

        lock_path = resolve_project_path(
            dashboard_config.get(
                "collection_lock_path",
                "session/locks/dashboard_collection.lock",
            )
        )
        login_config = build_city_ops_login_config(
            dashboard_config,
            resolved_config_path.parent,
        )
        lock_context = (
            file_lock(
                lock_path,
                wait_seconds=float(
                    dashboard_config.get("collection_lock_wait_seconds", 5) or 5
                ),
                poll_seconds=float(
                    dashboard_config.get("collection_lock_poll_seconds", 1) or 1
                ),
                stale_seconds=float(
                    dashboard_config.get("collection_lock_stale_seconds", 1800) or 1800
                ),
                lock_label="驾驶舱采集锁",
            )
            if acquire_collection_lock
            else nullcontext({"lock_path": str(lock_path), "owned_by": "pipeline"})
        )
        logger = event_logger or logging.getLogger(__name__)
        with lock_context as lock_result:
            prepare_started = perf_counter()
            session_result = StageSessionBroker(
                preparer=prepare_session,
                base_dir=PROJECT_ROOT,
            ).ensure(
                login_config,
                force_refresh=force_refresh,
                event_logger=event_logger,
            )
            prepare_seconds = perf_counter() - prepare_started
            logger.info(
                "驾驶舱会话阶段耗时 batch_no=%s prepare=%.3fs session_status=%s force_refresh=%s",
                batch_no,
                prepare_seconds,
                session_result.get("status"),
                force_refresh,
            )
            if session_result.get("status") == "invalid":
                raise RuntimeError(
                    f"驾驶舱 city_ops 会话不可用: {session_result.get('reason')}"
                )

        finished_at = now_shanghai()
        record = store.update(
            batch_no,
            status="SUCCESS",
            phase="SESSION_READY",
            finished_at=finished_at,
            session_status=session_result.get("status"),
        )
        return {
            "batch_no": batch_no,
            "status": "SUCCESS",
            "phase": "SESSION_READY",
            "trigger_type": normalized_trigger,
            "session_status": session_result.get("status"),
            "stage_data": session_result.get("stage_data") or {},
            "lock": lock_result,
            "run_record": record,
        }
    except Exception as exc:
        if record_created:
            finished_at = now_shanghai()
            try:
                failure_directory = resolve_project_path(
                    dashboard_config.get(
                        "failure_report_directory",
                        DEFAULT_FAILURE_DIRECTORY,
                    )
                )
                report_path = write_dashboard_failure_report(
                    failure_directory,
                    batch_no,
                    phase="SESSION",
                    error_type=type(exc).__name__,
                    message=sanitize_error(exc),
                    details={
                        "trigger_type": normalized_trigger,
                        "run_type": run_type,
                    },
                    now_provider=now_shanghai,
                )
                store.update(
                    batch_no,
                    status="FAILED",
                    phase="SESSION",
                    finished_at=finished_at,
                    error_type=type(exc).__name__,
                    error_message=json.dumps(
                        {
                            "message": sanitize_error(exc),
                            "report_path": str(report_path),
                        },
                        ensure_ascii=False,
                    ),
                )
            except Exception:
                pass
        raise
    finally:
        store.close()
