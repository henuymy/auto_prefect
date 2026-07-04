from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from backend.services import config_store, prefect_runner, starter_template
from backend.services.run_log_store import append_log


router = APIRouter(prefix="/api/configs", tags=["configs"])


def _split_error_detail(error: str, fallback_message: str) -> tuple[str, str]:
    text = str(error or "").strip()
    if not text:
        return fallback_message, ""

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    summary = next((line for line in lines if line.startswith("RuntimeError(")), "") or lines[0]
    if len(summary) > 160:
        summary = f"{summary[:157]}..."
    return summary, text[-8000:]


@router.get("")
def list_configs():
    return config_store.list_configs()


@router.get("/{config_id}")
def get_config(config_id: str, source: str = Query("published", pattern="^(published|draft)$")):
    try:
        return config_store.get_config(config_id, source=source)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("")
def create_config(config: dict):
    try:
        saved = config_store.create_config(config)
        append_log("success", "新建配置", f"已创建 {saved.get('name', '')}")
        return saved
    except FileExistsError as exc:
        append_log("failed", "新建配置失败", str(exc))
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.put("/{config_id}")
def save_config(config_id: str, config: dict):
    saved = config_store.save_config(config_id, config)
    append_log("success", "保存配置", f"已写入 config/reports/{saved.get('name', '')}.json")
    return saved


@router.delete("/{config_id}")
def delete_config(config_id: str, source: str = Query("published", pattern="^(published|draft)$")):
    try:
        deleted = config_store.delete_config(config_id, source=source)
        append_log("success", "删除配置", f"已删除配置 {config_id}", "\n".join(deleted))
        return {"ok": True, "deleted": deleted}
    except FileNotFoundError as exc:
        append_log("failed", "删除配置失败", str(exc))
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{config_id}/draft")
def save_draft(config_id: str, config: dict):
    saved = config_store.save_draft(config_id, config)
    append_log("success", "保存草稿", f"已写入 runtime/drafts/{saved.get('name', '')}.json")
    return saved


@router.get("/{config_id}/versions")
def list_versions(config_id: str):
    return config_store.list_versions(config_id)


@router.get("/{config_id}/versions/{version_id}")
def get_version(config_id: str, version_id: str):
    try:
        return config_store.get_version(config_id, version_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{config_id}/versions/{version_id}/restore")
def restore_version(config_id: str, version_id: str):
    try:
        restored = config_store.restore_version(config_id, version_id)
        append_log("success", "恢复配置版本", f"已恢复 {config_id} -> {version_id}")
        return restored
    except FileNotFoundError as exc:
        append_log("failed", "恢复配置版本失败", str(exc))
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{config_id}/validate")
def validate_config(config_id: str, config: dict):
    issues = prefect_runner.validate_config(config)
    append_log("failed" if issues else "success", "配置校验", f"发现 {len(issues)} 个问题" if issues else "配置校验通过")
    return {"issues": issues}


@router.post("/{config_id}/starter-template")
def generate_starter_template(config_id: str, config: dict):
    try:
        def progress(title: str, message: str) -> None:
            append_log("running", f"生成新手模板 - {title}", message)

        append_log(
            "running",
            "生成新手模板 - 会话策略",
            "按 Prefect 会话策略执行：先探活本次抓取项需要的 stage；探活失败才重新登录；仅下载明确提示 session 已过期时强制刷新。",
        )
        result = starter_template.generate_starter_template(config, progress=progress)
        if result.get("status") == "failed":
            append_log("failed", "生成新手模板失败", f"配置校验失败，发现 {len(result.get('issues') or [])} 个问题")
            raise HTTPException(status_code=400, detail={"issues": result.get("issues") or []})
        append_log("success", "生成新手模板", f"已生成 {result.get('template_path', '')}")
        return result
    except RuntimeError as exc:
        message, details = _split_error_detail(str(exc), "生成新手模板失败")
        append_log("failed", "生成新手模板失败", message, details)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/{config_id}/test-run")
def test_run_config(config_id: str, config: dict):
    append_log("running", "安全测试 - 配置校验", "正在检查当前页面配置快照")
    issues = prefect_runner.validate_config(config)
    if issues:
        append_log("failed", "安全测试失败", f"配置校验失败，发现 {len(issues)} 个问题")
        raise HTTPException(status_code=400, detail={"issues": issues})
    try:
        def progress(title: str, message: str) -> None:
            append_log("running", f"安全测试 - {title}", message)

        append_log(
            "running",
            "安全测试 - 运行方式",
            "只做 dry-run 冒烟检查：不真实发送企业微信，不提交正式模板，不覆盖模板文件。",
        )
        result = prefect_runner.test_run_config(config, progress=progress)
        append_log("success", "安全测试", result.get("message", ""), result.get("output") or result.get("taskConfigPath"))
        return result
    except RuntimeError as exc:
        message, details = _split_error_detail(str(exc), "安全测试运行失败")
        append_log("failed", "安全测试失败", message, details)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/{config_id}/real-test-run")
def real_test_run_config(config_id: str, config: dict):
    append_log("running", "真实试跑 - 配置校验", "正在检查当前页面配置快照")
    issues = prefect_runner.validate_config(config)
    if issues:
        append_log("failed", "真实试跑失败", f"配置校验失败，发现 {len(issues)} 个问题")
        raise HTTPException(status_code=400, detail={"issues": issues})
    try:
        def progress(title: str, message: str) -> None:
            append_log("running", f"真实试跑 - {title}", message)

        append_log(
            "running",
            "真实试跑 - 会话策略",
            "先探活本次抓取项需要的 stage；探活失败才关闭旧自动登录浏览器并重新登录，登录成功后保留浏览器。",
        )
        append_log(
            "running",
            "真实试跑 - 运行方式",
            "会真实下载、比对、生成截图并发送企业微信；不会提交正式模板。",
        )
        result = prefect_runner.real_test_run_config(config, progress=progress)
        append_log("success", "真实试跑", result.get("message", ""), result.get("output") or result.get("taskConfigPath"))
        return result
    except RuntimeError as exc:
        message, details = _split_error_detail(str(exc), "真实试跑运行失败")
        append_log("failed", "真实试跑失败", message, details)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/{config_id}/publish")
def publish_config(config_id: str, config: dict):
    issues = prefect_runner.validate_config(config)
    if issues:
        append_log("failed", "发布到调度失败", f"配置校验失败，发现 {len(issues)} 个问题")
        raise HTTPException(status_code=400, detail={"issues": issues})
    try:
        previous = config_store.get_config(config_id, source="published")
    except FileNotFoundError:
        previous = None
    saved = config_store.save_config(config_id, config)
    try:
        result = None
        if previous and prefect_runner.can_fast_toggle_schedule(previous, saved):
            result = prefect_runner.fast_toggle_schedule(saved)
        if result is None:
            result = prefect_runner.publish_config(saved)
        append_log("success", "发布到调度", result.get("message", ""), result.get("output") or result.get("taskConfigPath"))
        return result
    except RuntimeError as exc:
        message, details = _split_error_detail(str(exc), "发布到调度失败")
        append_log("failed", "发布到调度失败", message, details)
        raise HTTPException(status_code=500, detail=str(exc)) from exc
