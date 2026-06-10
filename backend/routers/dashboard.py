from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, status

from backend.services import dashboard_runner
from backend.services.run_log_store import append_log


router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.post("/collect", status_code=status.HTTP_202_ACCEPTED)
def collect_dashboard(
    force_refresh: bool = Query(
        False,
        description="是否要求会话阶段进入登录锁复检",
    ),
):
    try:
        result = dashboard_runner.submit_dashboard_collection(
            force_refresh=force_refresh
        )
        append_log(
            "running",
            "数据驾驶舱 - 触发与会话",
            result["message"],
            result.get("flow_run_id") or result["deployment"],
        )
        return result
    except RuntimeError as exc:
        append_log(
            "failed",
            "数据驾驶舱 - 提交失败",
            str(exc)[:500],
        )
        raise HTTPException(status_code=503, detail=str(exc)) from exc
