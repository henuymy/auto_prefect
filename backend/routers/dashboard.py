from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy.exc import SQLAlchemyError

from backend.services import dashboard_runner
from backend.services.run_log_store import append_log
from infrastructure.dashboard_mysql import create_dashboard_engine
from services.dashboard_query_service import get_current_wide_table


router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("/current")
def current_dashboard(
    level_type: str | None = Query(None),
    parent_id: int | None = Query(None, ge=1),
):
    engine = create_dashboard_engine()
    try:
        return get_current_wide_table(
            engine,
            level_type=level_type,
            parent_id=parent_id,
        )
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"驾驶舱查询失败: {type(exc).__name__}",
        ) from exc
    finally:
        engine.dispose()


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
            "数据驾驶舱 - 完整采集",
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
