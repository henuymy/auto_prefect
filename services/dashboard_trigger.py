"""Dashboard trigger, batch lifecycle, and session preparation."""

from __future__ import annotations

import copy
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from infrastructure.dashboard_run_store import (
    CollectionRunStore,
    JsonCollectionRunStore,
    MySQLCollectionRunStore,
)
from services.session_manager import file_lock, prepare_session


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "dashboard" / "session.json"
SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
VALID_TRIGGER_TYPES = {"MANUAL", "SCHEDULED"}
SENSITIVE_PATTERN = re.compile(
    r"(?i)(password|token|cookie|authorization|uaptoken)(\s*[=:]\s*)([^,\s;]+)"
)


def resolve_project_path(value: str | Path, base_dir: Path = PROJECT_ROOT) -> Path:
    path = Path(value)
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
    return read_json(resolved), resolved


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


def build_run_store(dashboard_config: dict[str, Any]) -> CollectionRunStore:
    store_type = str(dashboard_config.get("run_store_type") or "mysql").strip().lower()
    if store_type == "mysql":
        return MySQLCollectionRunStore()
    if store_type == "json":
        run_store_dir = resolve_project_path(
            dashboard_config.get("run_store_dir", "runtime/dashboard/collection_runs")
        )
        return JsonCollectionRunStore(run_store_dir, now_provider=now_shanghai)
    raise ValueError(f"run_store_type 只支持 mysql/json: {store_type!r}")


def execute_session_phase(
    config_path: str | Path = DEFAULT_CONFIG_PATH,
    trigger_type: str = "SCHEDULED",
    force_refresh: bool = False,
    batch_no: str | None = None,
    event_logger: Any = None,
) -> dict[str, Any]:
    dashboard_config, resolved_config_path = load_dashboard_config(config_path)
    normalized_trigger = normalize_trigger_type(trigger_type)
    batch_no = batch_no or generate_batch_no()
    store = build_run_store(dashboard_config)
    record_created = False
    try:
        store.create(batch_no, normalized_trigger)
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
                "runtime/locks/dashboard_collection.lock",
            )
        )
        login_config = build_city_ops_login_config(
            dashboard_config,
            resolved_config_path.parent,
        )
        with file_lock(
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
        ) as lock_result:
            session_result = prepare_session(
                login_config,
                base_dir=PROJECT_ROOT,
                force_refresh=force_refresh,
                event_logger=event_logger,
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
            "cookie_dump_path": session_result.get("cookie_dump_path"),
            "lock": lock_result,
            "run_record": record,
        }
    except Exception as exc:
        if record_created:
            finished_at = now_shanghai()
            try:
                store.update(
                    batch_no,
                    status="FAILED",
                    phase="SESSION",
                    finished_at=finished_at,
                    error_type=type(exc).__name__,
                    error_message=sanitize_error(exc),
                )
            except Exception:
                pass
        raise
    finally:
        store.close()
