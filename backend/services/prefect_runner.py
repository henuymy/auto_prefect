from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from services.runtime_paths import display_path, runtime_path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_NOTIFY_WORK_POOL = "windows-notify-pool"
FLOW_ENTRYPOINT = "flows/notify_single_flow.py:auto_notify_flow"
FLOW_NAME = "auto-notify-flow"
TENCENT_DOCUMENT_SOURCES = {"tencent_sheet", "tencent_smartbook"}


def document_only_downloads(config: dict[str, Any]) -> bool:
    downloads = [item for item in config.get("downloads") or [] if item.get("enabled", True) is not False]
    return bool(downloads) and all((item.get("source") or "http_api") in TENCENT_DOCUMENT_SOURCES for item in downloads)


def _safe_name(value: str) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|]+', "_", value).strip()
    return cleaned or "未命名配置"


def _write_json(path: Path, payload: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _display_path(path: Path) -> str:
    return display_path(path, project_dir=PROJECT_ROOT)


def get_prefect_api_url() -> str:
    return os.environ.get("PREFECT_API_URL") or "http://127.0.0.1:4200/api"


def get_notify_work_pool() -> str:
    return os.environ.get("PREFECT_NOTIFY_POOL_NAME") or DEFAULT_NOTIFY_WORK_POOL


def check_prefect_status(timeout: float = 2.0) -> dict[str, Any]:
    api_url = get_prefect_api_url().rstrip("/")
    health_url = f"{api_url}/health"
    try:
        with urllib.request.urlopen(health_url, timeout=timeout) as response:
            body = response.read().decode("utf-8", errors="replace")
        return {
            "ok": True,
            "api_url": api_url,
            "health_url": health_url,
            "message": body or "Prefect API 可访问",
        }
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return {
            "ok": False,
            "api_url": api_url,
            "health_url": health_url,
            "message": str(exc),
        }


def _schedule_action_already_done(schedule_action: str, output: str) -> bool:
    lowered = output.lower()
    if schedule_action == "resume":
        return "already active" in lowered
    if schedule_action == "pause":
        return "already paused" in lowered or "already inactive" in lowered
    return False


def _deployment_crons(deployment: dict[str, Any]) -> list[str]:
    raw_crons = deployment.get("crons") if isinstance(deployment.get("crons"), list) else []
    return [str(item or "").strip() for item in raw_crons if str(item or "").strip()]


def can_fast_toggle_schedule(previous: dict[str, Any], current: dict[str, Any]) -> bool:
    """Return whether publishing only needs to reconcile the schedule active state."""
    ignored_keys = {"enabled", "updatedAt", "source", "has_draft", "lastRun"}

    def publish_content(config: dict[str, Any]) -> dict[str, Any]:
        return {key: value for key, value in config.items() if key not in ignored_keys}

    return publish_content(previous) == publish_content(current)


def _prefect_api_request(method: str, path: str, payload: Any | None = None) -> Any:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        f"{get_prefect_api_url().rstrip('/')}/{path.lstrip('/')}",
        data=body,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        response_body = response.read().decode("utf-8", errors="replace")
    return json.loads(response_body) if response_body else None


def delete_deployment(config: dict[str, Any]) -> dict[str, Any]:
    """Delete the Prefect deployment matching the current report name."""
    report_name = _safe_name(config.get("name") or config.get("id") or "未命名配置")
    deployment_name = f"notify-{report_name}"
    deployment_path = "/".join(
        urllib.parse.quote(part, safe="") for part in ["deployments", "name", FLOW_NAME, deployment_name]
    )
    try:
        deployment_response = _prefect_api_request("GET", deployment_path)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return {
                "deleted": False,
                "deploymentId": "",
                "deploymentName": f"{FLOW_NAME}/{deployment_name}",
                "message": f"未找到 Prefect Deployment: {FLOW_NAME}/{deployment_name}",
            }
        raise RuntimeError(f"Prefect deployment 查询失败: HTTP {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Prefect deployment 查询失败: {exc}") from exc

    deployment_id = (deployment_response or {}).get("id")
    if not deployment_id:
        raise RuntimeError(f"Prefect deployment 查询结果缺少 ID: {FLOW_NAME}/{deployment_name}")

    try:
        _prefect_api_request("DELETE", f"deployments/{urllib.parse.quote(str(deployment_id), safe='')}")
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return {
                "deleted": False,
                "deploymentId": str(deployment_id),
                "deploymentName": f"{FLOW_NAME}/{deployment_name}",
                "message": f"Prefect Deployment 已不存在: {FLOW_NAME}/{deployment_name}",
            }
        raise RuntimeError(f"Prefect deployment 删除失败: HTTP {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Prefect deployment 删除失败: {exc}") from exc

    return {
        "deleted": True,
        "deploymentId": str(deployment_id),
        "deploymentName": f"{FLOW_NAME}/{deployment_name}",
        "message": f"已删除 Prefect Deployment: {FLOW_NAME}/{deployment_name}",
    }


def fast_toggle_schedule(config: dict[str, Any]) -> dict[str, Any] | None:
    """Toggle existing matching schedules via the Prefect API, without redeploying."""
    deployment = config.get("deployment") or {}
    crons = _deployment_crons(deployment)
    if not crons:
        return None

    report_name = _safe_name(config.get("name") or config.get("id") or "未命名配置")
    deployment_name = f"notify-{report_name}"
    deployment_path = "/".join(
        urllib.parse.quote(part, safe="") for part in ["deployments", "name", FLOW_NAME, deployment_name]
    )
    try:
        deployment_response = _prefect_api_request("GET", deployment_path)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise RuntimeError(f"Prefect deployment 查询失败: HTTP {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Prefect deployment 查询失败: {exc}") from exc

    deployment_id = (deployment_response or {}).get("id")
    if not deployment_id:
        return None
    try:
        schedules = _prefect_api_request("GET", f"deployments/{deployment_id}/schedules") or []
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Prefect 调度列表读取失败: {exc}") from exc

    timezone = deployment.get("timezone") or "Asia/Shanghai"
    expected_schedules = sorted((cron, timezone) for cron in crons)
    actual_schedules = sorted(
        (str((item.get("schedule") or {}).get("cron") or ""), str((item.get("schedule") or {}).get("timezone") or "UTC"))
        for item in schedules
    )
    if actual_schedules != expected_schedules or any(not item.get("id") for item in schedules):
        return None

    enabled = config.get("enabled", True) is not False
    changed_count = 0
    try:
        for item in schedules:
            if bool(item.get("active", True)) == enabled:
                continue
            _prefect_api_request(
                "PATCH",
                f"deployments/{deployment_id}/schedules/{item['id']}",
                {"active": enabled},
            )
            changed_count += 1
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        action_label = "启用" if enabled else "停用"
        raise RuntimeError(f"Prefect 调度{action_label}失败: {exc}") from exc

    schedule_status = "enabled" if enabled else "disabled"
    action_label = "启用" if enabled else "停用"
    return {
        "deploymentId": deployment_id,
        "status": "success",
        "message": f"已快速{action_label} Prefect 调度: {config.get('name', '')}",
        "taskConfigPath": "",
        "crons": crons,
        "timezone": timezone,
        "scheduleStatus": schedule_status,
        "publishMode": "schedule-state-only",
        "output": f"快速切换完成：{len(schedules)} 条调度，实际更新 {changed_count} 条；未重新部署。",
    }


def build_task_config(
    report_name: str,
    report_config_path: str,
    dry_run: bool = False,
    send_dry_run: bool | None = None,
    commit_enabled: bool | None = None,
    login_enabled: bool | None = None,
) -> dict[str, Any]:
    slug = _safe_name(report_name).replace(" ", "_")
    send_dry_run = dry_run if send_dry_run is None else send_dry_run
    commit_enabled = (not dry_run) if commit_enabled is None else commit_enabled
    login_enabled = (not dry_run) if login_enabled is None else login_enabled
    task_config: dict[str, Any] = {
        "flow_name": f"auto-notify-{slug}",
        "report_config_path": report_config_path,
    }
    step_overrides: dict[str, Any] = {}
    if login_enabled is not True:
        step_overrides["login"] = {"enabled": login_enabled}
    if dry_run:
        step_overrides["download"] = {"dry_run": True, "debug": True}
    if send_dry_run is not False:
        step_overrides["send_wecom"] = {"dry_run": send_dry_run}
    if commit_enabled is not True:
        step_overrides["commit_template"] = {"enabled": commit_enabled}
    if step_overrides:
        task_config["steps"] = step_overrides
    return task_config


def write_task_config(
    config: dict[str, Any],
    draft: bool = False,
    dry_run: bool = False,
    send_dry_run: bool | None = None,
    commit_enabled: bool | None = None,
    login_enabled: bool | None = None,
    suffix: str | None = None,
) -> Path:
    report_name = _safe_name(config.get("name") or config.get("id") or "未命名配置")
    report_path = f"config/reports/{report_name}.json"
    if draft:
        report_suffix = suffix if suffix is not None else (".dry_run" if dry_run else "")
        report_config_path = runtime_path(
            f"config/drafts/{report_name}{report_suffix}.report.json"
        )
        _write_json(report_config_path, config)
        report_path = f"config/drafts/{report_name}{report_suffix}.report.json"
    if login_enabled is None and document_only_downloads(config):
        login_enabled = False
    task_config = build_task_config(
        report_name,
        report_path,
        dry_run=dry_run,
        send_dry_run=send_dry_run,
        commit_enabled=commit_enabled,
        login_enabled=login_enabled,
    )
    if draft:
        task_config["runtime_dir"] = f"flow/{report_name}"
    wait_for_change = config.get("wait_for_change") or {}
    if wait_for_change.get("enabled"):
        wait_override = {"enabled": True}
        for key, default_value in (("poll_interval_seconds", 300), ("max_wait_minutes", 180)):
            if key in wait_for_change and wait_for_change[key] != default_value:
                wait_override[key] = wait_for_change[key]
        task_config["wait_for_change"] = wait_override
    target_dir = (
        runtime_path("config/drafts") if draft else PROJECT_ROOT / "config/tasks"
    )
    assert target_dir is not None
    file_suffix = suffix if suffix is not None else (".dry_run.task.json" if dry_run else ".json")
    return _write_json(target_dir / f"{report_name}{file_suffix}", task_config)


def _project_path(path_value: str | None) -> Path | None:
    if not path_value:
        return None
    path = Path(path_value)
    return path if path.is_absolute() else PROJECT_ROOT / path


def _known_stages() -> set[str]:
    stages = set()
    for path in [
        PROJECT_ROOT / "config/modules/login_config.json",
        runtime_path("session/cookie_dump.json"),
    ]:
        if not path.exists():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        for item in payload.get("usm_cookie_apps", []) or []:
            if item.get("stage"):
                stages.add(str(item["stage"]))
        for item in payload.get("stages", []) or []:
            if item.get("stage"):
                stages.add(str(item["stage"]))
    return stages


def validate_config(config: dict[str, Any]) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    for legacy_key in ("download", "compare", "env"):
        if legacy_key in config:
            issues.append({"path": f"/{legacy_key}", "message": f"旧字段 {legacy_key} 已不再支持，请使用新 Schema"})
    if not config.get("name"):
        issues.append({"path": "/name", "message": "配置名称不能为空"})
    if not config.get("template_path"):
        issues.append({"path": "/template_path", "message": "模板路径不能为空"})
    else:
        template_path = _project_path(config.get("template_path"))
        if template_path and not template_path.exists():
            issues.append({"path": "/template_path", "message": f"模板文件不存在: {config.get('template_path')}"})

    if not config.get("downloads"):
        issues.append({"path": "/downloads", "message": "至少需要一个数据抓取项"})
    if config.get("enabled") is True and not config.get("compare_sources"):
        issues.append({"path": "/compare_sources", "message": "启用调度前必须配置至少一个比对源"})
    known_stages = _known_stages()
    download_names = set()
    allowed_methods = {"GET", "POST", "PUT", "PATCH", "DELETE"}
    allowed_body_types = {"form", "json", "raw"}
    allowed_response_modes = {"file", "json_to_excel", "json_drilldown_to_excel"}
    for index, item in enumerate(config.get("downloads") or []):
        if "csrf_headers_from_cookies" in item:
            issues.append({"path": f"/downloads/{index}/csrf_headers_from_cookies", "message": "旧动态 CSRF 字段已移除"})
        if not item.get("name"):
            issues.append({"path": f"/downloads/{index}/name", "message": "下载标识不能为空"})
        else:
            download_names.add(str(item["name"]))
        source = item.get("source") or "http_api"
        if source in TENCENT_DOCUMENT_SOURCES:
            if not (str(item.get("file_id") or "").strip() or str(item.get("doc_url") or "").strip()):
                issues.append({"path": f"/downloads/{index}/file_id", "message": "腾讯文档必须填写 file_id 或 doc_url"})
            sheets = item.get("sheets") or []
            if not sheets:
                message = "腾讯智能表格至少需要一个子表" if source == "tencent_smartbook" else "腾讯文档至少需要一个 Sheet 范围"
                issues.append({"path": f"/downloads/{index}/sheets", "message": message})
            for sheet_index, sheet in enumerate(sheets):
                if source == "tencent_smartbook" and "range" in sheet:
                    issues.append({"path": f"/downloads/{index}/sheets/{sheet_index}/range", "message": "智能表格不支持 range"})
                if not (str(sheet.get("sheet_id") or "").strip() or str(sheet.get("sheet_name") or "").strip()):
                    issues.append({"path": f"/downloads/{index}/sheets/{sheet_index}/sheet_id", "message": "Sheet 必须填写 sheet_id 或 Sheet 名称"})
            continue
        if source not in {"http_api", ""}:
            issues.append({"path": f"/downloads/{index}/source", "message": f"不支持的数据源: {source}"})
        if item.get("stage") and known_stages and item.get("stage") not in known_stages:
            issues.append({"path": f"/downloads/{index}/stage", "message": f"Cookie Stage 不存在: {item.get('stage')}"})
        if not item.get("url"):
            issues.append({"path": f"/downloads/{index}/url", "message": "下载 URL 不能为空"})
        method = str(item.get("method") or "").upper()
        if not method:
            issues.append({"path": f"/downloads/{index}/method", "message": "请求方法 method 不能为空"})
        elif method not in allowed_methods:
            issues.append({"path": f"/downloads/{index}/method", "message": f"不支持的请求方法: {item.get('method')}"})
        body_type = item.get("body_type")
        if not body_type:
            issues.append({"path": f"/downloads/{index}/body_type", "message": "载体类型 body_type 不能为空"})
        elif body_type not in allowed_body_types:
            issues.append({"path": f"/downloads/{index}/body_type", "message": f"不支持的载体类型: {body_type}"})
        response_mode = item.get("response_mode")
        if not response_mode:
            issues.append({"path": f"/downloads/{index}/response_mode", "message": "响应模式 response_mode 不能为空"})
        elif response_mode not in allowed_response_modes:
            issues.append({"path": f"/downloads/{index}/response_mode", "message": f"不支持的响应模式: {response_mode}"})
        if response_mode in {"json_to_excel", "json_drilldown_to_excel"}:
            columns = ((item.get("excel") or {}).get("columns") or [])
            if not columns:
                issues.append({"path": f"/downloads/{index}/excel/columns", "message": "JSON 转 Excel 必须配置 excel.columns"})
        if response_mode == "json_drilldown_to_excel":
            drilldown = item.get("drilldown") or {}
            for field in ["data_path", "request_area_field", "next_area_field"]:
                if not drilldown.get(field):
                    issues.append({"path": f"/downloads/{index}/drilldown/{field}", "message": f"级联下钻缺少 {field}"})

    for source_index, source in enumerate(config.get("compare_sources") or []):
        engine = source.get("engine")
        if not engine:
            issues.append({"path": f"/compare_sources/{source_index}/engine", "message": "比对引擎不能为空"})
        elif engine not in {"openpyxl", "com"}:
            issues.append({"path": f"/compare_sources/{source_index}/engine", "message": f"不支持的比对引擎: {engine}"})
        max_workers = source.get("max_workers")
        if max_workers is None:
            issues.append({"path": f"/compare_sources/{source_index}/max_workers", "message": "并发 Sheet 数不能为空"})
        else:
            try:
                if int(max_workers) < 1:
                    issues.append({"path": f"/compare_sources/{source_index}/max_workers", "message": "并发 Sheet 数必须大于等于 1"})
            except (TypeError, ValueError):
                issues.append({"path": f"/compare_sources/{source_index}/max_workers", "message": "并发 Sheet 数必须是数字"})
        download_name = source.get("download_name")
        if download_name and download_name not in download_names:
            issues.append({"path": f"/compare_sources/{source_index}/download_name", "message": f"比对源找不到对应下载项: {download_name}"})
        for mapping_index, mapping in enumerate(source.get("sheet_mappings") or []):
            if not mapping.get("new_sheet_name"):
                issues.append({"path": f"/compare_sources/{source_index}/sheet_mappings/{mapping_index}/new_sheet_name", "message": "源 sheet 不能为空"})
            if not mapping.get("template_sheet_name"):
                issues.append({"path": f"/compare_sources/{source_index}/sheet_mappings/{mapping_index}/template_sheet_name", "message": "模板 sheet 不能为空"})

    send = config.get("send") or {}
    if not send.get("items"):
        issues.append({"path": "/send/items", "message": "至少需要一个发送项"})
    for item_index, item in enumerate(send.get("items") or []):
        item_type = item.get("type")
        range_config = item.get("capture") if item_type == "image" else item.get("text")
        range_config = range_config or {}
        if range_config.get("mode") == "explicit_range" and not str(range_config.get("range") or "").strip():
            config_key = "capture" if item_type == "image" else "text"
            issues.append({
                "path": f"/send/items/{item_index}/{config_key}/range",
                "message": "自定义范围不能为空，例如 A1:H20",
            })
    return issues


def test_run_config(config: dict[str, Any], progress: Callable[[str, str], None] | None = None) -> dict[str, Any]:
    if progress:
        progress("生成临时配置", "正在写入安全测试专用的 dry-run 配置")
    task_config_path = write_task_config(config, draft=True, dry_run=True)
    if progress:
        progress("启动 dry-run", f"即将执行安全测试流程: {_display_path(task_config_path)}")
    command = [
        sys.executable,
        "-c",
        (
            "from flows.notify_single_flow import auto_notify_flow; "
            f"print(auto_notify_flow(r'{task_config_path.as_posix()}'))"
        ),
    ]
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PREFECT_API_URL"] = get_prefect_api_url()
    completed = subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        env=env,
        text=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
    )
    output = "\n".join(part for part in [completed.stdout, completed.stderr] if part)
    if completed.returncode != 0:
        if progress:
            progress("安全测试失败", "dry-run 流程返回失败，详情见日志")
        raise RuntimeError(output or "测试运行失败")
    if progress:
        progress("安全测试完成", "配置 dry-run 已完成，不会发送企业微信，也不会提交模板")
    return {
        "flowRunId": f"manual-{int(datetime.now().timestamp())}",
        "status": "success",
        "message": f"安全测试运行完成: {config.get('name', '')}",
        "taskConfigPath": _display_path(task_config_path),
        "output": output[-4000:],
    }


def real_test_run_config(config: dict[str, Any], progress: Callable[[str, str], None] | None = None) -> dict[str, Any]:
    if progress:
        progress("生成临时配置", "正在写入真实试跑专用配置：真实下载和发送，跳过正式模板提交")
    task_config_path = write_task_config(
        config,
        draft=True,
        dry_run=False,
        send_dry_run=False,
        commit_enabled=False,
        suffix=".real_test.task.json",
    )
    if progress:
        progress("启动真实流程", f"即将执行真实试跑流程: {_display_path(task_config_path)}")
    command = [
        sys.executable,
        "-c",
        (
            "from flows.notify_single_flow import auto_notify_flow; "
            f"print(auto_notify_flow(r'{task_config_path.as_posix()}'))"
        ),
    ]
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PREFECT_API_URL"] = get_prefect_api_url()
    completed = subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        env=env,
        text=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=900,
    )
    output = "\n".join(part for part in [completed.stdout, completed.stderr] if part)
    if completed.returncode != 0:
        if progress:
            progress("真实试跑失败", "真实流程返回失败，详情见日志")
        raise RuntimeError(output or "真实试跑失败")
    if progress:
        progress("真实试跑完成", "已完成真实下载、比对、截图和企业微信发送；正式模板未提交")
    return {
        "flowRunId": f"real-test-{int(datetime.now().timestamp())}",
        "status": "success",
        "message": f"真实试跑完成（已发送企业微信，未提交正式模板）: {config.get('name', '')}",
        "taskConfigPath": _display_path(task_config_path),
        "output": output[-4000:],
    }


def publish_config(config: dict[str, Any]) -> dict[str, Any]:
    task_config_path = write_task_config(config, draft=False, dry_run=False)
    deployment = config.get("deployment") or {}
    report_name = _safe_name(config.get("name") or config.get("id") or "未命名配置")
    deployment_name = f"notify-{report_name}"
    crons = _deployment_crons(deployment)
    schedule_enabled = config.get("enabled", True) is not False
    work_pool = get_notify_work_pool()
    command = [
        sys.executable,
        "-m",
        "prefect",
        "deploy",
        FLOW_ENTRYPOINT,
        "--name",
        deployment_name,
        "--pool",
        work_pool,
        "--param",
        f"config_path={task_config_path.as_posix()}",
    ]

    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PREFECT_API_URL"] = get_prefect_api_url()
    completed = subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        env=env,
        text=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
    )
    output = "\n".join(part for part in [completed.stdout, completed.stderr] if part)
    if completed.returncode != 0:
        raise RuntimeError(output or "Prefect 发布失败")
    schedule_output = ""
    schedule_status = "none"
    deployment_full_name = f"{FLOW_NAME}/{deployment_name}"
    if crons:
        schedule_outputs = []
        for index, cron in enumerate(crons):
            schedule_command = [
                sys.executable,
                "-m",
                "prefect",
                "deployment",
                "schedule",
                "create",
                deployment_full_name,
                "--cron",
                cron,
                "--accept-yes",
            ]
            if deployment.get("timezone"):
                schedule_command.extend(["--timezone", deployment["timezone"]])
            if index == 0:
                schedule_command.append("--replace")
            schedule_completed = subprocess.run(
                schedule_command,
                cwd=PROJECT_ROOT,
                env=env,
                text=True,
                capture_output=True,
                encoding="utf-8",
                errors="replace",
                timeout=180,
            )
            schedule_item_output = "\n".join(part for part in [schedule_completed.stdout, schedule_completed.stderr] if part)
            schedule_outputs.append(schedule_item_output)
            if schedule_completed.returncode != 0:
                raise RuntimeError("\n".join(part for part in [output, *schedule_outputs, "Prefect 调度创建失败"] if part))

        schedule_action = "resume" if schedule_enabled else "pause"
        schedule_list_command = [
            sys.executable,
            "-m",
            "prefect",
            "deployment",
            "schedule",
            "ls",
            deployment_full_name,
            "-o",
            "json",
        ]
        schedule_list_completed = subprocess.run(
            schedule_list_command,
            cwd=PROJECT_ROOT,
            env=env,
            text=True,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=180,
        )
        schedule_list_output = "\n".join(
            part for part in [schedule_list_completed.stdout, schedule_list_completed.stderr] if part
        )
        schedule_outputs.append(schedule_list_output)
        if schedule_list_completed.returncode != 0:
            raise RuntimeError(
                "\n".join(part for part in [output, *schedule_outputs, "Prefect 调度列表读取失败"] if part)
            )
        try:
            schedules = json.loads(schedule_list_completed.stdout or "[]")
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                "\n".join(part for part in [output, *schedule_outputs, "Prefect 调度列表解析失败"] if part)
            ) from exc
        schedule_ids = [item.get("id") for item in schedules if item.get("id")]
        if not schedule_ids:
            action_label = "启用" if schedule_enabled else "停用"
            raise RuntimeError(
                "\n".join(
                    part
                    for part in [output, *schedule_outputs, f"Prefect 调度{action_label}失败: 未找到 schedule ID"]
                    if part
                )
            )

        for schedule_id in schedule_ids:
            schedule_command = [
                sys.executable,
                "-m",
                "prefect",
                "deployment",
                "schedule",
                schedule_action,
                deployment_full_name,
                schedule_id,
            ]
            schedule_completed = subprocess.run(
                schedule_command,
                cwd=PROJECT_ROOT,
                env=env,
                text=True,
                capture_output=True,
                encoding="utf-8",
                errors="replace",
                timeout=180,
            )
            schedule_item_output = "\n".join(
                part for part in [schedule_completed.stdout, schedule_completed.stderr] if part
            )
            schedule_outputs.append(schedule_item_output)
            if schedule_completed.returncode != 0 and not _schedule_action_already_done(
                schedule_action, schedule_item_output
            ):
                action_label = "启用" if schedule_enabled else "停用"
                raise RuntimeError(
                    "\n".join(
                        part for part in [output, *schedule_outputs, f"Prefect 调度{action_label}失败"] if part
                    )
                )
        schedule_output = "\n".join(part for part in schedule_outputs if part)
        schedule_status = "enabled" if schedule_enabled else "disabled"
        output = "\n".join(part for part in [output, schedule_output] if part)
    else:
        schedule_list_command = [
            sys.executable,
            "-m",
            "prefect",
            "deployment",
            "schedule",
            "ls",
            deployment_full_name,
            "-o",
            "json",
        ]
        schedule_list_completed = subprocess.run(
            schedule_list_command,
            cwd=PROJECT_ROOT,
            env=env,
            text=True,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=180,
        )
        schedule_list_output = "\n".join(part for part in [schedule_list_completed.stdout, schedule_list_completed.stderr] if part)
        if schedule_list_completed.returncode != 0:
            raise RuntimeError("\n".join(part for part in [output, schedule_list_output, "Prefect 调度列表读取失败"] if part))
        try:
            schedules = json.loads(schedule_list_completed.stdout or "[]")
        except json.JSONDecodeError as exc:
            raise RuntimeError("\n".join(part for part in [output, schedule_list_output, "Prefect 调度列表解析失败"] if part)) from exc
        schedule_ids = [item.get("id") for item in schedules if item.get("id")]
        schedule_outputs = [schedule_list_output]
        for schedule_id in schedule_ids:
            schedule_command = [
                sys.executable,
                "-m",
                "prefect",
                "deployment",
                "schedule",
                "delete",
                deployment_full_name,
                schedule_id,
                "--accept-yes",
            ]
            schedule_completed = subprocess.run(
                schedule_command,
                cwd=PROJECT_ROOT,
                env=env,
                text=True,
                capture_output=True,
                encoding="utf-8",
                errors="replace",
                timeout=180,
            )
            schedule_item_output = "\n".join(part for part in [schedule_completed.stdout, schedule_completed.stderr] if part)
            schedule_outputs.append(schedule_item_output)
            if schedule_completed.returncode != 0:
                raise RuntimeError("\n".join(part for part in [output, *schedule_outputs, "Prefect 调度清理失败"] if part))
        schedule_output = "\n".join(part for part in schedule_outputs if part)
        output = "\n".join(part for part in [output, schedule_output] if part)
    return {
        "deploymentId": f"deployment-{int(datetime.now().timestamp())}",
        "status": "success",
        "message": f"已发布到 Prefect 调度: {config.get('name', '')}",
        "taskConfigPath": _display_path(task_config_path),
        "crons": crons,
        "timezone": deployment.get("timezone", "Asia/Shanghai"),
        "scheduleStatus": schedule_status,
        "workPool": work_pool,
        "output": output[-4000:],
    }
