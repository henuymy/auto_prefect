"""Prefect flow for the automated notification pipeline."""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path
from time import monotonic, sleep

PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from tasks.commit_tasks import commit_template_task
from tasks.compare_tasks import compare_report_task
from tasks.method_tasks import download_reports_task
from tasks.notify_tasks import build_message_package_task, send_notification_package_task
from tasks.session_tasks import prepare_session_task
from tasks.template_tasks import update_template_task
from services.compare_service import find_empty_download_sheet_mappings
from services.method_service import EmptyReportDataError
from services.session_retry_service import (
    RefreshBudget,
    is_session_expired_error,  # noqa: F401 - backward-compatible flow export
    run_with_session_refresh_once,
)
from services.session_business_failure_service import run_with_business_session_reporting
from services.runtime_paths import resolve_runtime_path, validate_runtime_path
from utils.config_loader import load_json_with_local_override
from utils.date_placeholders import resolve_dynamic_placeholders, resolve_dynamic_structure  # noqa: F401

try:
    from prefect import flow, get_run_logger, task
except ImportError as exc:  # pragma: no cover - runtime dependency guard
    raise RuntimeError("缺少 Prefect，请先安装: pip install prefect") from exc


DEFAULT_CONFIG_PATH = PROJECT_DIR / "config" / "tasks" / "local.json"
EXAMPLE_CONFIG_PATH = PROJECT_DIR / "config" / "tasks" / "example.json"
TASK_DEFAULTS_PATH = PROJECT_DIR / "config" / "task_defaults" / "notify.json"
HEALTHY_SESSION_STATUSES = {"reused", "reused_after_lock", "refreshed"}


def notify_session_result_is_healthy(result):
    return isinstance(result, dict) and result.get("status") in HEALTHY_SESSION_STATUSES


def run_notify_session_preparation(operation, recoverer=None):
    def validate_preparation_result():
        result = operation()
        if result.get("status") == "invalid":
            raise RuntimeError(f"会话不可用: {result.get('reason')}")
        return result

    return run_with_business_session_reporting(
        validate_preparation_result,
        trigger_source="auto-notify-flow",
        recover_on_success=True,
        recoverer=recoverer,
        recovery_predicate=notify_session_result_is_healthy,
    )


def run_notify_download_with_session_refresh(
    operation,
    refresh_session,
    refresh_budget,
    reporter=None,
):
    return run_with_business_session_reporting(
        lambda: run_with_session_refresh_once(
            operation,
            refresh_session,
            refresh_budget=refresh_budget,
        ),
        trigger_source="auto-notify-flow",
        reporter=reporter,
    )


def load_config(config_path=None):
    path = Path(config_path).resolve() if config_path else DEFAULT_CONFIG_PATH
    if not path.exists() and not config_path:
        path = EXAMPLE_CONFIG_PATH
    with TASK_DEFAULTS_PATH.open("r", encoding="utf-8") as f:
        defaults = json.load(f)
    with path.open("r", encoding="utf-8") as f:
        config = json.load(f)
    return deep_merge(defaults, config), path


def flow_runtime_path(flow_runtime_dir, area, *parts):
    path = Path(flow_runtime_dir) / area / Path(*parts)
    validate_runtime_path(path)
    return path


def materialize_runtime_paths(config, flow_runtime_dir):
    """Fill per-run output paths after task defaults and overrides are merged."""
    steps = config["steps"]
    compare = steps["compare"]
    compare.setdefault("generated_config_path", str(flow_runtime_path(flow_runtime_dir, "debug", "compare_config.json")))

    update = steps["update_template"]
    update.setdefault("generated_config_path", str(flow_runtime_path(flow_runtime_dir, "debug", "template_updater_config.json")))
    update.setdefault("output_dir", str(flow_runtime_path(flow_runtime_dir, "output", "templates")))
    update.setdefault("manifest_path", str(flow_runtime_path(flow_runtime_dir, "debug", "update_manifest.json")))

    send = steps["send_wecom"]
    send.setdefault("generated_config_path", str(flow_runtime_path(flow_runtime_dir, "debug", "excel_sender_config.json")))
    send.setdefault("runtime_dir", str(flow_runtime_path(flow_runtime_dir, "output", "wecom")))

    commit = steps["commit_template"]
    commit.setdefault("update_manifest_path", str(flow_runtime_path(flow_runtime_dir, "debug", "update_manifest.json")))
    commit.setdefault("send_result_path", str(flow_runtime_path(flow_runtime_dir, "output", "wecom", "send_result.json")))
    commit.setdefault("backup_dir", str(flow_runtime_path(flow_runtime_dir, "backup")))
    commit.setdefault("manifest_path", str(flow_runtime_path(flow_runtime_dir, "debug", "commit_manifest.json")))


def resolve_project_path(path):
    value = Path(path)
    if value.parts and value.parts[0].lower() == "runtime":
        return resolve_runtime_path(value, project_dir=PROJECT_DIR)
    if value.is_absolute() or ".." in value.parts:
        raise ValueError(f"不允许绝对路径或父目录路径: {path}")
    return (PROJECT_DIR / value).resolve()


def read_json(path):
    resolved = resolve_project_path(path)
    payload, _ = load_json_with_local_override(resolved)
    return payload


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


def assert_report_schema_contract(report_cfg):
    if "download" in report_cfg:
        raise ValueError("report config 仍包含旧字段 download，请保存为 schema 新结构")
    if "compare" in report_cfg:
        raise ValueError("report config 仍包含旧字段 compare，请使用 compare_sources[]")
    if "env" in report_cfg:
        raise ValueError("report config 仍包含旧字段 env，请从配置中移除")
    if not report_cfg.get("downloads"):
        raise ValueError("report config 缺少 downloads，React 配置中心要求使用 downloads[]")

    required_download_fields = ("name", "stage", "method", "url", "body_type", "response_mode")
    for index, item in enumerate(report_cfg.get("downloads") or [], start=1):
        if "csrf_headers_from_cookies" in item:
            raise ValueError(f"downloads[{index}] 包含旧字段 csrf_headers_from_cookies，请使用 headers_from_cookies 或动态认证字段")
        if item.get("source") == "tencent_sheet":
            if not item.get("name"):
                raise ValueError(f"downloads[{index}] 缺少必填字段 name")
            if not (item.get("doc_url") or item.get("file_id")):
                raise ValueError(f"downloads[{index}] 腾讯文档缺少 doc_url 或 file_id")
            sheets = item.get("sheets") or []
            if not sheets:
                raise ValueError(f"downloads[{index}] 腾讯文档至少需要一个 Sheet 范围")
            for sheet_index, sheet in enumerate(sheets, start=1):
                if not (sheet.get("sheet_id") or sheet.get("sheet_name")):
                    raise ValueError(f"downloads[{index}].sheets[{sheet_index}] 缺少 sheet_id 或 sheet_name")
            continue
        for field in required_download_fields:
            if not item.get(field):
                raise ValueError(f"downloads[{index}] 缺少必填字段 {field}")
        response_mode = item.get("response_mode")
        if response_mode in {"json_to_excel", "json_drilldown_to_excel"}:
            columns = ((item.get("excel") or {}).get("columns") or [])
            if not columns:
                raise ValueError(f"downloads[{index}] response_mode={response_mode} 时必须配置 excel.columns")
        if response_mode == "json_drilldown_to_excel":
            drilldown = item.get("drilldown") or {}
            for field in ("data_path", "request_area_field", "next_area_field"):
                if not drilldown.get(field):
                    raise ValueError(f"downloads[{index}] json_drilldown_to_excel 缺少 drilldown.{field}")

    for source_index, source in enumerate(report_cfg.get("compare_sources") or [], start=1):
        if not source.get("download_name"):
            raise ValueError(f"compare_sources[{source_index}] 缺少 download_name")
        for mapping_index, mapping in enumerate(source.get("sheet_mappings") or [], start=1):
            if not mapping.get("new_sheet_name"):
                raise ValueError(f"compare_sources[{source_index}].sheet_mappings[{mapping_index}] 缺少 new_sheet_name")
            if not mapping.get("template_sheet_name"):
                raise ValueError(f"compare_sources[{source_index}].sheet_mappings[{mapping_index}] 缺少 template_sheet_name")
    if report_cfg.get("enabled") is True and not report_cfg.get("compare_sources"):
        raise ValueError("启用的报表必须配置 compare_sources，避免调度运行后在比对阶段失败")


def build_download_config(base_config, report_cfg):
    assert_report_schema_contract(report_cfg)
    report_overrides = report_cfg.get("downloads") or []
    merged_reports = []
    for index, report_override in enumerate(report_overrides, start=1):
        merged_report = copy.deepcopy(report_override)
        merged_report["name"] = (
            merged_report.get("name")
            or report_cfg.get("name")
            or f"report-{index}"
        )
        merged_reports.append(resolve_dynamic_structure(merged_report))
    config = {k: v for k, v in base_config.items() if k != "report_defaults"}
    config["reports"] = merged_reports
    return config


def enabled_downloads(report_cfg):
    return [
        item
        for item in (report_cfg.get("downloads") or [])
        if item.get("enabled", True) is not False
    ]


def required_stages_for_report(report_cfg):
    stages = []
    seen = set()
    for item in enabled_downloads(report_cfg):
        if item.get("source") == "tencent_sheet":
            continue
        stage = str(item.get("stage") or "").strip()
        if stage and stage not in seen:
            stages.append(stage)
            seen.add(stage)
    return stages


def build_login_config(base_config, report_cfg):
    config = copy.deepcopy(base_config)
    stages = required_stages_for_report(report_cfg)
    if stages:
        config["required_stages"] = stages
    return config


def build_compare_config(base_config, report_cfg):
    raise ValueError("旧 compare 配置已不再支持，请使用 compare_sources[]")


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
    if send.get("webhook_url"):
        config.setdefault("wecom", {})["webhook_url"] = send["webhook_url"]
    return config


def get_update_condition(report_cfg):
    template_update = report_cfg.get("template_update") or {}
    return template_update.get("update_condition", "any_changed")


def should_send_when_same(report_cfg):
    template_update = report_cfg.get("template_update") or {}
    return bool(template_update.get("send_when_same", False))


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


def downloaded_report_path_map(download_manifest):
    mapping = {}
    for item in download_manifest.get("results") or []:
        name = item.get("name")
        output_path = item.get("output_path")
        if name and output_path:
            mapping[name] = output_path
    return mapping


def build_compare_source_configs(base_config, report_cfg, download_manifest, compare_step, flow_runtime_dir):
    assert_report_schema_contract(report_cfg)
    template_path = report_cfg.get("template_path")
    compare_sources = report_cfg.get("compare_sources") or []
    if not compare_sources:
        raise ValueError("report config 缺少 compare_sources，React 配置中心要求明确配置比对源")

    paths_by_name = downloaded_report_path_map(download_manifest or {})
    configs = []
    for index, source in enumerate(compare_sources, start=1):
        download_name = source.get("download_name")
        if not download_name:
            raise ValueError(f"compare_sources[{index}] 缺少 download_name")
        if download_name not in paths_by_name:
            raise RuntimeError(f"下载结果中找不到 compare_sources 对应报表: {download_name}")
        source_override = {k: v for k, v in source.items() if k != "download_name"}
        compare_config = deep_merge(base_config, {
            "template_path": template_path,
            "new_report_path": paths_by_name[download_name],
            **source_override,
        })
        compare_config["output_path"] = str(flow_runtime_path(flow_runtime_dir, "tmp", "compare_results", f"{download_name}.json"))
        configs.append({
            "name": source.get("name") or download_name,
            "download_name": download_name,
            "source_report_path": paths_by_name[download_name],
            "config": compare_config,
        })
    return configs


def aggregate_compare_results(compare_runs, update_condition="any_changed"):
    sheets = []
    for run in compare_runs:
        for sheet in run["result"].get("sheets", []):
            item = dict(sheet)
            item["download_name"] = run.get("download_name")
            item["source_report_path"] = run.get("source_report_path") or run["config"].get("new_report_path")
            sheets.append(item)

    summary = {
        "same": sum(1 for item in sheets if item.get("result") == "same"),
        "changed": sum(1 for item in sheets if item.get("result") == "changed"),
        "invalid": sum(1 for item in sheets if item.get("result") == "invalid"),
        "total": len(sheets),
    }
    if summary["invalid"]:
        result = "invalid"
        message = "存在无法比对的工作表"
    elif update_condition == "all_changed":
        result = "changed" if summary["total"] > 0 and summary["changed"] == summary["total"] else "same"
        message = "所有参与比较的工作表均有变化" if result == "changed" else "未满足全部 changed 的更新条件"
    elif update_condition == "any_changed":
        result = "changed" if summary["changed"] else "same"
        message = "至少一个工作表数据有变化" if result == "changed" else "所有工作表数据一致"
    else:
        raise ValueError("template_update.update_condition 只支持 any_changed 或 all_changed")

    return {
        "result": result,
        "sheets": sheets,
        "summary": summary,
        "message": message,
        "update_condition": update_condition,
    }


def parse_wait_for_change_config(config):
    wait_cfg = config.get("wait_for_change") or {}
    enabled = bool(wait_cfg.get("enabled", False))
    poll_interval_raw = wait_cfg.get("poll_interval_seconds")
    poll_interval_seconds = 300 if poll_interval_raw is None else int(poll_interval_raw)
    if poll_interval_seconds <= 0:
        raise ValueError("wait_for_change.poll_interval_seconds 必须大于 0")

    max_wait_minutes = wait_cfg.get("max_wait_minutes")
    max_wait_seconds = None
    if max_wait_minutes is not None:
        max_wait_seconds = int(float(max_wait_minutes) * 60)
        if max_wait_seconds <= 0:
            raise ValueError("wait_for_change.max_wait_minutes 必须大于 0")

    return {
        "enabled": enabled,
        "poll_interval_seconds": poll_interval_seconds,
        "max_wait_seconds": max_wait_seconds,
    }


@task
def prepare_template_config(base_config_path, output_config_path, compare_config_path, overrides=None):
    template_config = read_json(base_config_path)
    compare_config = read_json(compare_config_path)
    template_config["compare_config_path"] = str(compare_config_path)
    if compare_config.get("compare_result_path"):
        template_config["compare_result_path"] = compare_config.get("compare_result_path")
    template_config["source_report_path"] = compare_config.get("new_report_path")
    template_config["template_path"] = compare_config.get("template_path")
    if overrides:
        template_config.update(overrides)
    return str(write_json(output_config_path, template_config))


@flow(name="auto-notify-flow")
def auto_notify_flow(config_path=None):
    logger = get_run_logger()
    config, resolved_config_path = load_config(config_path)
    logger.info("读取流程配置: %s", resolved_config_path)

    steps = config["steps"]
    flow_runtime_dir = Path(config.get("runtime_dir", f"runtime/flow/{resolved_config_path.stem}"))
    validate_runtime_path(flow_runtime_path(flow_runtime_dir, "output"))
    materialize_runtime_paths(config, flow_runtime_dir)

    report_cfg = read_json(config["report_config_path"]) if config.get("report_config_path") else {}
    assert_report_schema_contract(report_cfg)
    login_config = None
    if steps.get("login", {}).get("enabled", False):
        login_config = build_login_config(read_json(steps["login"]["config_path"]), report_cfg)

    if steps.get("login", {}).get("enabled", False):
        initial_force_refresh = bool(steps["login"].get("force_refresh", False))
        run_notify_session_preparation(
            lambda: prepare_session_task(
                login_config,
                force_refresh=initial_force_refresh,
            ),
        )
    else:
        initial_force_refresh = False

    refresh_budget = RefreshBudget(consumed=initial_force_refresh)

    wait_cfg = parse_wait_for_change_config(config)
    wait_started_at = monotonic()

    def run_download_with_session_retry():
        if not steps.get("download", {}).get("enabled", True):
            return None
        download_config = build_download_config(read_json(steps["download"]["config_path"]), report_cfg)
        download_config["output_dir"] = str(flow_runtime_path(flow_runtime_dir, "tmp", "downloads"))
        download_config["manifest_path"] = str(flow_runtime_path(flow_runtime_dir, "debug", "download_manifest.json"))

        def download_operation():
            return download_reports_task(
                download_config,
                dry_run=bool(steps["download"].get("dry_run", False)),
                debug=bool(steps["download"].get("debug", False)),
            )

        if not steps.get("login", {}).get("enabled", False):
            return download_operation()

        def refresh_session():
            logger.warning("下载失败（session 过期），强制重新登录后重试")
            return run_notify_session_preparation(
                lambda: prepare_session_task(login_config, force_refresh=True)
            )

        return run_notify_download_with_session_refresh(
            download_operation,
            refresh_session,
            refresh_budget,
        )

    attempt = 0
    compare_result = None
    update_manifest = None
    while True:
        attempt += 1
        try:
            download_manifest = run_download_with_session_retry()
        except EmptyReportDataError as exc:
            if not wait_cfg["enabled"]:
                raise
            compare_result = {
                "result": "same",
                "reason": "report_data_not_generated",
                "message": str(exc),
                "sheets": [],
                "summary": {
                    "same": 1,
                    "changed": 0,
                    "invalid": 0,
                    "total": 1,
                },
                "update_condition": get_update_condition(report_cfg),
            }
            write_json(str(flow_runtime_path(flow_runtime_dir, "debug", "compare_result.json")), compare_result)
            elapsed_seconds = int(monotonic() - wait_started_at)
            max_wait_seconds = wait_cfg["max_wait_seconds"]
            if max_wait_seconds is not None and elapsed_seconds >= max_wait_seconds:
                logger.warning("报表数据持续未生成，已超时（%s 秒），停止重试", max_wait_seconds)
                return {
                    "status": "timeout_no_change",
                    "reason": "report_data_not_generated_timeout",
                    "attempts": attempt,
                    "elapsed_seconds": elapsed_seconds,
                    "message": str(exc),
                }
            logger.info(
                "报表数据暂未生成（第 %s 次），%s 秒后重试下载",
                attempt,
                wait_cfg["poll_interval_seconds"],
            )
            sleep(wait_cfg["poll_interval_seconds"])
            continue
        if download_manifest and download_manifest.get("dry_run"):
            logger.info("下载 dry-run 完成，停止后续比对和发送")
            return {"status": "skipped", "reason": "download_dry_run", "download_manifest": download_manifest}

        compare_base_config = read_json(steps["compare"]["config_path"])
        compare_configs = build_compare_source_configs(
            compare_base_config,
            report_cfg,
            download_manifest,
            steps["compare"],
            flow_runtime_dir,
        )
        if wait_cfg["enabled"]:
            empty_sheet_mappings = find_empty_download_sheet_mappings(
                download_manifest,
                report_cfg.get("compare_sources") or [],
                base_dir=PROJECT_DIR,
            )
            if empty_sheet_mappings:
                compare_result = {
                    "result": "same",
                    "reason": "download_sheet_empty_below_header",
                    "message": "下载文件存在参与比对的 Sheet 数据区为空，等待下一轮",
                    "sheets": [
                        {
                            "name": item.get("new_sheet_name"),
                            "new_sheet_name": item.get("new_sheet_name"),
                            "template_sheet_name": item.get("template_sheet_name"),
                            "download_name": item.get("download_name"),
                            "source_report_path": item.get("source_report_path"),
                            "header_row": item.get("header_row"),
                            "result": "same",
                            "reason": item.get("reason"),
                        }
                        for item in empty_sheet_mappings
                    ],
                    "summary": {
                        "same": len(empty_sheet_mappings),
                        "changed": 0,
                        "invalid": 0,
                        "total": len(empty_sheet_mappings),
                    },
                    "update_condition": get_update_condition(report_cfg),
                }
                write_json(str(flow_runtime_path(flow_runtime_dir, "debug", "compare_result.json")), compare_result)
                elapsed_seconds = int(monotonic() - wait_started_at)
                max_wait_seconds = wait_cfg["max_wait_seconds"]
                if max_wait_seconds is not None and elapsed_seconds >= max_wait_seconds:
                    logger.warning(
                        "下载数据区持续为空，已超时（%s 秒），停止重试",
                        max_wait_seconds,
                    )
                    return {
                        "status": "timeout_no_change",
                        "reason": "download_sheet_empty_below_header_timeout",
                        "attempts": attempt,
                        "elapsed_seconds": elapsed_seconds,
                        "empty_sheets": empty_sheet_mappings,
                    }
                logger.info(
                    "下载数据区为空（第 %s 次），%s 秒后重试下载",
                    attempt,
                    wait_cfg["poll_interval_seconds"],
                )
                sleep(wait_cfg["poll_interval_seconds"])
                continue
        compare_runs = []
        for index, compare_item in enumerate(compare_configs, start=1):
            compare_config_path = write_json(
                str(flow_runtime_path(flow_runtime_dir, "debug", "compare_configs", f"compare_{index}.json")),
                compare_item["config"],
            )
            compare_result_item = compare_report_task(read_json(compare_config_path))
            compare_runs.append({
                **compare_item,
                "config_path": str(compare_config_path),
                "result": compare_result_item,
            })
        compare_result = aggregate_compare_results(compare_runs, get_update_condition(report_cfg))
        compare_config_path = write_json(
            steps["compare"].get("generated_config_path", str(flow_runtime_path(flow_runtime_dir, "debug", "compare_config.json"))),
            {
                "template_path": report_cfg.get("template_path"),
                "compare_result_path": str(flow_runtime_path(flow_runtime_dir, "debug", "compare_result.json")),
                "compare_runs": [
                    {
                        "name": item.get("name"),
                        "download_name": item.get("download_name"),
                        "source_report_path": item.get("source_report_path") or item["config"].get("new_report_path"),
                        "config_path": item.get("config_path"),
                    }
                    for item in compare_runs
                ],
            },
        )
        write_json(str(flow_runtime_path(flow_runtime_dir, "debug", "compare_result.json")), compare_result)
        compare_status = compare_result.get("result")
        if compare_status == "invalid":
            raise RuntimeError("比对结果 invalid，停止流程")
        if compare_status == "changed":
            break
        if compare_status != "same":
            raise RuntimeError(f"不支持的比对结果: {compare_status!r}")
        if should_send_when_same(report_cfg):
            logger.info("比对结果 same，但配置为继续发送通报")
            break
        if not wait_cfg["enabled"]:
            logger.info("数据一致，无需更新和发送")
            return {"status": "skipped", "reason": "compare_same", "attempts": attempt}

        elapsed_seconds = int(monotonic() - wait_started_at)
        max_wait_seconds = wait_cfg["max_wait_seconds"]
        if max_wait_seconds is not None and elapsed_seconds >= max_wait_seconds:
            logger.warning(
                "比对结果持续 same，已超时（%s 秒），停止重试",
                max_wait_seconds,
            )
            return {
                "status": "timeout_no_change",
                "reason": "compare_same_timeout",
                "attempts": attempt,
                "elapsed_seconds": elapsed_seconds,
            }

        logger.info(
            "比对结果 same（第 %s 次），%s 秒后重试下载与比对",
            attempt,
            wait_cfg["poll_interval_seconds"],
        )
        sleep(wait_cfg["poll_interval_seconds"])

    template_config_path = prepare_template_config(
        steps["update_template"]["config_path"],
        steps["update_template"].get("generated_config_path", str(flow_runtime_path(flow_runtime_dir, "debug", "template_updater_config.json"))),
        compare_config_path,
        overrides={
            **{k: steps["update_template"][k] for k in ("output_dir", "manifest_path") if k in steps["update_template"]},
            **(report_cfg.get("template_update") or {}),
            **({
                "allow_same_update": True,
                "write_sheets": "all_compared",
            } if compare_result and compare_result.get("result") == "same" and should_send_when_same(report_cfg) else {}),
        },
    )
    update_manifest = update_template_task(read_json(template_config_path))
    if update_manifest.get("status") == "skipped":
        logger.info("模板更新跳过: %s", update_manifest.get("reason"))
        return {"status": "skipped", "reason": update_manifest.get("reason")}

    send_step = steps["send_wecom"]
    send_config = build_send_config(read_json(send_step["base_config_path"]), report_cfg, update_manifest["output_path"])
    if send_step.get("runtime_dir"):
        send_config.setdefault("output", {})["runtime_dir"] = send_step["runtime_dir"]
    send_config_path = write_json(
        send_step.get("generated_config_path", str(flow_runtime_path(flow_runtime_dir, "debug", "excel_sender_config.json"))),
        send_config,
    )
    send_config = read_json(send_config_path)
    package, package_file = build_message_package_task(send_config)
    send_notification_package_task(
        send_config,
        package,
        package_file=package_file,
        dry_run=bool(send_step.get("dry_run", False)),
        timeout=int(send_step.get("timeout", 30)),
    )
    if steps.get("commit_template", {}).get("enabled", True):
        commit_step = steps["commit_template"]
        commit_config = read_json(commit_step["config_path"])
        for key in ("update_manifest_path", "send_result_path", "backup_dir", "manifest_path"):
            if key in commit_step:
                commit_config[key] = commit_step[key]
        commit_template_task(commit_config)
    return {"status": "completed", "updated_template_path": update_manifest["output_path"]}


if __name__ == "__main__":
    auto_notify_flow()
