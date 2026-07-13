"""Read-only audit of Dashboard V2 preflight and cutover gates."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen


PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from prefect.client.orchestration import get_client
from prefect.client.schemas.filters import (
    FlowRunFilter,
    FlowRunFilterDeploymentId,
    FlowRunFilterState,
    FlowRunFilterStateType,
)
from prefect.client.schemas.objects import StateType

from infrastructure.dashboard_mysql import (
    DashboardMySQLConfigError,
    DashboardMySQLSettings,
    check_dashboard_mysql,
)
from services.dashboard_v2_trigger import load_dashboard_config
from services.dashboard_v2_readiness import check_dashboard_v2_schema


REQUIRED_DEPLOYMENTS = {
    "dashboard-collection",
    "dashboard-daily-acc",
    "dashboard-monthly",
    "dashboard-indicator-sync",
    "dashboard-v2-partition-maintenance",
}
ACTIVE_FLOW_STATE_TYPES = {
    StateType.SCHEDULED,
    StateType.PENDING,
    StateType.RUNNING,
    StateType.CANCELLING,
    StateType.PAUSED,
}


def _check(ok: bool, message: str, **details: Any) -> dict[str, Any]:
    return {"ok": bool(ok), "message": message, **details}


def _load_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def _git_check() -> dict[str, Any]:
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    changes = [line for line in result.stdout.splitlines() if line.strip()]
    return _check(
        result.returncode == 0 and not changes,
        "Git 工作区干净" if not changes else "Git 工作区存在未提交变更",
        change_count=len(changes),
        changes=changes[:20],
    )


def _bundle_check(bundle: Path, *, require_pk: bool) -> dict[str, Any]:
    summary = _load_json(bundle / "summary.json")
    if summary is None:
        return _check(False, "V2 配置迁移包 summary.json 不存在或无效")
    required_files = {
        "hierarchy.csv",
        "indicator_settings.json",
        "custom_indicators.json",
        "normal_day_target.json",
        "normal_month_target.json",
    }
    missing = sorted(name for name in required_files if not (bundle / name).is_file())
    normal_ok = (
        int(summary.get("hierarchy_node_count", 0)) > 0
        and int(summary.get("indicator_count", 0)) > 0
        and int(summary.get("normal_day_target_count", 0)) > 0
        and int(summary.get("normal_month_target_count", 0)) > 0
    )
    pk_day = int(summary.get("pk_day_target_count", 0))
    pk_month = int(summary.get("pk_month_target_count", 0))
    pk_ok = not require_pk or (pk_day > 0 and pk_month > 0)
    return _check(
        not missing and normal_ok and pk_ok,
        "V2 配置迁移包有效" if not missing and normal_ok and pk_ok else "V2 配置迁移包不完整",
        missing_files=missing,
        hierarchy_node_count=summary.get("hierarchy_node_count"),
        indicator_count=summary.get("indicator_count"),
        normal_day_target_count=summary.get("normal_day_target_count"),
        normal_month_target_count=summary.get("normal_month_target_count"),
        pk_day_target_count=pk_day,
        pk_month_target_count=pk_month,
        pk_required=require_pk,
    )


def _approval_check(path: Path) -> dict[str, Any]:
    approval = _load_json(path)
    if approval is None:
        return _check(False, "切换审批证据文件不存在或无效", path=str(path))
    required = (
        "root_password_rotated_at",
        "backup_restore_verified_at",
        "backup_restore_database",
        "approved_by",
    )
    missing = [name for name in required if not str(approval.get(name) or "").strip()]
    return _check(
        not missing,
        "root 轮换和备份恢复证据已登记" if not missing else "切换审批证据不完整",
        path=str(path),
        missing_fields=missing,
        backup_restore_database=approval.get("backup_restore_database"),
        approved_by=approval.get("approved_by"),
    )


def _mysql_checks(expected_database: str) -> tuple[dict[str, Any], dict[str, Any]]:
    connection = check_dashboard_mysql()
    try:
        settings = DashboardMySQLSettings.from_env()
    except DashboardMySQLConfigError as exc:
        target = _check(False, str(exc))
        return connection, target
    database_ok = settings.database == expected_database
    if not database_ok:
        target = _check(
            False,
            "当前环境未指向独立 V2 库",
            configured_database=settings.database,
            expected_database=expected_database,
        )
    elif not connection.get("ok"):
        target = _check(False, "V2 MySQL 连接检查失败")
    else:
        target = check_dashboard_v2_schema()
    return connection, target


def _prefect_health(api_url: str) -> dict[str, Any]:
    health_url = api_url.rstrip("/") + "/health"
    try:
        with urlopen(health_url, timeout=5) as response:
            body = response.read().decode("utf-8", errors="replace")
        return _check(response.status == 200, "Prefect API 可用", url=health_url, body=body[:100])
    except (OSError, URLError) as exc:
        return _check(False, f"Prefect API 不可用: {type(exc).__name__}", url=health_url)


async def _prefect_state_check(api_url: str, *, require_worker: bool) -> dict[str, Any]:
    previous = os.environ.get("PREFECT_API_URL")
    os.environ["PREFECT_API_URL"] = api_url
    try:
        async with get_client() as client:
            deployments = await client.read_deployments()
            by_name = {row.name: row for row in deployments}
            missing = sorted(REQUIRED_DEPLOYMENTS - by_name.keys())
            unpaused = sorted(
                name for name in REQUIRED_DEPLOYMENTS & by_name.keys()
                if not bool(by_name[name].paused)
            )
            target_ids = {
                row.id for name, row in by_name.items()
                if name in REQUIRED_DEPLOYMENTS
            }
            active_runs = []
            for run in await _read_target_flow_runs(client, target_ids):
                state_type = run.state.type.value if run.state else ""
                active_runs.append({
                    "id": str(run.id),
                    "state": state_type,
                    "state_name": run.state.name if run.state else None,
                    "name": run.name,
                })
            online_workers = []
            dashboard_pool = os.environ.get("PREFECT_DASHBOARD_POOL_NAME") or "windows-dashboard-pool"
            for worker in await client.read_workers_for_work_pool(dashboard_pool):
                if str(worker.status).endswith("ONLINE"):
                    online_workers.append(worker.name)
            worker_ok = bool(online_workers) if require_worker else True
            ok = not missing and not unpaused and not active_runs and worker_ok
            return _check(
                ok,
                "Prefect 切换状态满足门槛" if ok else "Prefect 切换状态未满足门槛",
                missing_deployments=missing,
                unpaused_deployments=unpaused,
                active_flow_runs=active_runs,
                online_workers=online_workers,
                worker_required=require_worker,
            )
    except Exception as exc:
        return _check(
            False,
            f"Prefect 状态检查失败: {type(exc).__name__}",
            detail=str(exc)[:300],
        )
    finally:
        if previous is None:
            os.environ.pop("PREFECT_API_URL", None)
        else:
            os.environ["PREFECT_API_URL"] = previous


async def _read_target_flow_runs(client: Any, deployment_ids: set[Any]) -> list[Any]:
    """Read every active target run without a global recent-run truncation."""
    if not deployment_ids:
        return []
    flow_run_filter = FlowRunFilter(
        deployment_id=FlowRunFilterDeploymentId(any_=list(deployment_ids)),
        state=FlowRunFilterState(
            type=FlowRunFilterStateType(any_=list(ACTIVE_FLOW_STATE_TYPES)),
        ),
    )
    rows: list[Any] = []
    offset = 0
    while True:
        page = await client.read_flow_runs(
            flow_run_filter=flow_run_filter,
            limit=200,
            offset=offset,
        )
        rows.extend(page)
        if len(page) < 200:
            return rows
        offset += len(page)


async def audit(args: argparse.Namespace) -> dict[str, Any]:
    connection, schema = _mysql_checks(args.expected_database)
    config, resolved_config = load_dashboard_config(args.config)
    schema_version = int(config.get("schema_version", 1) or 1)
    expected_schema_version = 2 if args.phase == "cutover" else 1
    checks = {
        "git": _git_check(),
        "mysql_connection": connection,
        "dashboard_v2_schema": schema,
        "migration_bundle": _bundle_check(Path(args.bundle), require_pk=args.require_pk_targets),
        "approval_evidence": _approval_check(Path(args.approvals)),
        "dashboard_config": _check(
            schema_version == expected_schema_version,
            f"schema_version={schema_version}",
            config_path=str(resolved_config),
            expected_schema_version=expected_schema_version,
        ),
        "prefect_health": _prefect_health(args.prefect_api_url),
        "prefect_state": await _prefect_state_check(
            args.prefect_api_url,
            require_worker=args.phase == "cutover",
        ),
    }
    ok = all(bool(item.get("ok")) for item in checks.values())
    return {
        "ok": ok,
        "phase": args.phase,
        "checked_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "checks": checks,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="只读审计驾驶舱 V2 切换门槛")
    parser.add_argument("--phase", choices=["preflight", "cutover"], default="preflight")
    parser.add_argument("--expected-database", default="dashboard_v2")
    parser.add_argument("--config", default="config/dashboard/session.json")
    parser.add_argument("--bundle", default="runtime/dashboard_v2_migration")
    parser.add_argument(
        "--approvals",
        default="runtime/dashboard_v2_migration/cutover_approvals.json",
    )
    parser.add_argument("--prefect-api-url", default="http://127.0.0.1:4200/api")
    parser.add_argument("--require-pk-targets", action="store_true")
    return parser.parse_args()


def main() -> int:
    result = asyncio.run(audit(parse_args()))
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
