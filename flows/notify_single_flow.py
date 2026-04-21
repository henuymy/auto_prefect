"""Prefect flow for the automated notification pipeline."""

from __future__ import annotations

import copy
import json
import sys
from datetime import datetime
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from tasks.commit_tasks import commit_template_task
from tasks.compare_tasks import compare_report_task
from tasks.method_tasks import download_reports_task
from tasks.notify_tasks import build_message_package_task, send_notification_package_task
from tasks.session_tasks import prepare_session_task
from tasks.template_tasks import update_template_task

try:
    from prefect import flow, get_run_logger, task
except ImportError as exc:  # pragma: no cover - runtime dependency guard
    raise RuntimeError("缺少 Prefect，请先安装: pip install prefect") from exc


DEFAULT_CONFIG_PATH = PROJECT_DIR / "config" / "tasks" / "local.json"
EXAMPLE_CONFIG_PATH = PROJECT_DIR / "config" / "tasks" / "example.json"


def load_config(config_path=None):
    path = Path(config_path).resolve() if config_path else DEFAULT_CONFIG_PATH
    if not path.exists() and not config_path:
        path = EXAMPLE_CONFIG_PATH
    with path.open("r", encoding="utf-8") as f:
        return json.load(f), path


def resolve_project_path(path):
    value = Path(path)
    if value.is_absolute():
        return value
    return (PROJECT_DIR / value).resolve()


def read_json(path):
    resolved = resolve_project_path(path)
    with resolved.open("r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path, payload):
    resolved = resolve_project_path(path)
    resolved.parent.mkdir(parents=True, exist_ok=True)
    with resolved.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return resolved


def deep_merge(base, override):
    result = copy.deepcopy(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def build_download_config(base_config, report_cfg):
    download_defaults = base_config.get("report_defaults", {})
    report_override = report_cfg.get("download", {})
    merged_report = deep_merge(download_defaults, report_override)
    merged_report["name"] = merged_report.get("name") or report_cfg.get("name")
    config = {k: v for k, v in base_config.items() if k != "report_defaults"}
    config["reports"] = [merged_report]
    return config


def build_compare_config(base_config, report_cfg):
    return deep_merge(base_config, {
        "template_path": report_cfg.get("template_path"),
        **report_cfg.get("compare", {}),
    })


def build_send_config(base_config, report_cfg, workbook_file):
    send = report_cfg.get("send", {})
    config = copy.deepcopy(base_config)
    workbooks = config.get("workbooks") or []
    if not workbooks:
        raise RuntimeError("企业微信发送配置缺少 workbooks")
    workbooks[0]["file"] = str(workbook_file)
    workbooks[0]["name"] = send.get("workbook_name") or workbooks[0].get("name")
    if send.get("items"):
        workbooks[0]["reports"] = [{"name": send.get("workbook_name"), "items": send["items"]}]
    return config


def select_downloaded_report_path(download_manifest, report_name=None):
    results = download_manifest.get("results") or []
    if not results:
        raise RuntimeError("下载结果为空，无法确定新报表路径")
    candidates = [
        item for item in results
        if item.get("output_path") and (not report_name or item.get("name") == report_name)
    ]
    if not candidates:
        names = [item.get("name") for item in results]
        raise RuntimeError(f"下载结果中找不到可用 output_path，report_name={report_name!r}，已下载: {names}")
    return candidates[0]["output_path"]


@task
def prepare_template_config(base_config_path, output_config_path, compare_config_path):
    template_config = read_json(base_config_path)
    compare_config = read_json(compare_config_path)
    template_config["compare_config_path"] = str(compare_config_path)
    template_config["source_report_path"] = compare_config.get("new_report_path")
    template_config["template_path"] = compare_config.get("template_path")
    return str(write_json(output_config_path, template_config))


@flow(name="auto-notify-flow")
def auto_notify_flow(config_path=None):
    logger = get_run_logger()
    config, resolved_config_path = load_config(config_path)
    logger.info("读取流程配置: %s", resolved_config_path)

    steps = config["steps"]
    flow_runtime_dir = resolve_project_path(
        config.get("runtime_dir", f"runtime/flow/{datetime.now().strftime('%Y%m%d_%H%M%S')}")
    )

    report_cfg = read_json(config["report_config_path"]) if config.get("report_config_path") else {}

    if steps.get("login", {}).get("enabled", False):
        session_result = prepare_session_task(
            read_json(steps["login"]["config_path"]),
            force_refresh=bool(steps["login"].get("force_refresh", False)),
        )
        if session_result.get("status") == "invalid":
            raise RuntimeError(f"会话不可用: {session_result.get('reason')}")

    download_manifest = None
    if steps.get("download", {}).get("enabled", True):
        download_config = build_download_config(read_json(steps["download"]["config_path"]), report_cfg)
        try:
            download_manifest = download_reports_task(
                download_config,
                dry_run=bool(steps["download"].get("dry_run", False)),
                debug=bool(steps["download"].get("debug", False)),
            )
        except RuntimeError as exc:
            if "session 已过期" not in str(exc) or not steps.get("login", {}).get("enabled", False):
                raise
            logger.warning("下载失败（session 过期），强制重新登录后重试")
            session_result = prepare_session_task(
                read_json(steps["login"]["config_path"]),
                force_refresh=True,
            )
            if session_result.get("status") == "invalid":
                raise RuntimeError(f"重新登录失败: {session_result.get('reason')}") from exc
            download_manifest = download_reports_task(
                download_config,
                dry_run=bool(steps["download"].get("dry_run", False)),
                debug=bool(steps["download"].get("debug", False)),
            )
        if download_manifest.get("dry_run"):
            logger.info("下载 dry-run 完成，停止后续比对和发送")
            return {"status": "skipped", "reason": "download_dry_run", "download_manifest": download_manifest}

    compare_config = build_compare_config(read_json(steps["compare"]["config_path"]), report_cfg)
    if download_manifest:
        compare_config["new_report_path"] = select_downloaded_report_path(
            download_manifest, report_name=steps["compare"].get("download_report_name")
        )
    compare_config_path = write_json(
        steps["compare"].get("generated_config_path", str(flow_runtime_dir / "compare_config.json")),
        compare_config,
    )
    compare_result = compare_report_task(read_json(compare_config_path))
    if compare_result.get("result") == "invalid":
        raise RuntimeError("比对结果 invalid，停止流程")
    if compare_result.get("result") == "same":
        logger.info("数据一致，无需更新和发送")
        return {"status": "skipped", "reason": "compare_same"}

    template_config_path = prepare_template_config(
        steps["update_template"]["config_path"],
        steps["update_template"].get("generated_config_path", str(flow_runtime_dir / "template_updater_config.json")),
        compare_config_path,
    )
    update_manifest = update_template_task(read_json(template_config_path))
    if update_manifest.get("status") == "skipped":
        logger.info("模板更新跳过: %s", update_manifest.get("reason"))
        return {"status": "skipped", "reason": update_manifest.get("reason")}

    send_config = build_send_config(read_json(steps["send_wecom"]["base_config_path"]), report_cfg, update_manifest["output_path"])
    send_config_path = write_json(
        steps["send_wecom"].get("generated_config_path", str(flow_runtime_dir / "excel_sender_config.json")),
        send_config,
    )
    send_config = read_json(send_config_path)
    package, package_file = build_message_package_task(send_config)
    send_notification_package_task(
        send_config,
        package,
        package_file=package_file,
        dry_run=bool(steps["send_wecom"].get("dry_run", False)),
        timeout=int(steps["send_wecom"].get("timeout", 30)),
    )
    if steps.get("commit_template", {}).get("enabled", True):
        commit_template_task(read_json(steps["commit_template"]["config_path"]))
    return {"status": "completed", "updated_template_path": update_manifest["output_path"]}


if __name__ == "__main__":
    auto_notify_flow()
