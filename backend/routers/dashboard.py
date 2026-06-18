from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy.exc import SQLAlchemyError

from infrastructure.dashboard_mysql import create_dashboard_engine
from services.dashboard_query_service import (
    get_dashboard_overview_fast,
    get_drill_down,
    get_acc_wide_table,
    get_current_wide_table,
    get_current_with_changes,
    get_snapshot_trend,
)


router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


def _parse_change_windows(value: str | None) -> list[int] | None:
    if not value:
        return None
    windows: list[int] = []
    for item in value.split(","):
        item = item.strip()
        if not item:
            continue
        try:
            minutes = int(item)
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail=f"change_windows 只支持逗号分隔分钟数: {value!r}",
            ) from exc
        if minutes <= 0 or minutes > 1440 or minutes % 5 != 0:
            raise HTTPException(
                status_code=400,
                detail="change_windows 只支持 5 分钟粒度，范围 5-1440",
            )
        windows.append(minutes)
    return windows or None


@router.get("/overview")
def dashboard_overview(
    branch_id: int | None = Query(None, ge=1),
    branch_code: str | None = Query(None),
    period_type: str = Query("DAY_ACC"),
    change_windows: str | None = Query(
        None,
        description="逗号分隔的变化窗口分钟数，最多 4 个，例如 5,15,30,60",
    ),
):
    engine = create_dashboard_engine()
    try:
        return get_dashboard_overview_fast(
            engine,
            branch_id=branch_id,
            branch_code=branch_code,
            period_type=period_type,
            change_windows=_parse_change_windows(change_windows),
        )
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"驾驶舱概览查询失败: {type(exc).__name__}",
        ) from exc
    finally:
        engine.dispose()


@router.get("/drill-down")
def dashboard_drill_down(
    parent_id: int = Query(..., ge=1),
    parent_level: str = Query(..., description="父级层级: CITY/BRANCH/GRID"),
    period_type: str = Query("DAY_ACC"),
    change_windows: str | None = Query(
        None,
        description="逗号分隔的变化窗口分钟数，最多 4 个，例如 5,15,30,60",
    ),
):
    """Lightweight drill-down: only returns children of the given parent."""
    engine = create_dashboard_engine()
    try:
        return get_drill_down(
            engine,
            parent_id=parent_id,
            parent_level=parent_level,
            period_type=period_type,
            change_windows=_parse_change_windows(change_windows),
        )
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"驾驶舱下钻查询失败: {type(exc).__name__}",
        ) from exc
    finally:
        engine.dispose()


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


@router.get("/current-with-changes")
def current_with_changes(
    level_type: str | None = Query(None),
    parent_id: int | None = Query(None, ge=1),
    change_windows: str | None = Query(
        None,
        description="逗号分隔的变化窗口分钟数，最多 4 个，例如 5,15,30,60",
    ),
):
    engine = create_dashboard_engine()
    try:
        return get_current_with_changes(
            engine,
            level_type=level_type,
            parent_id=parent_id,
            change_windows=_parse_change_windows(change_windows),
        )
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"驾驶舱变化量查询失败: {type(exc).__name__}",
        ) from exc
    finally:
        engine.dispose()


@router.get("/acc")
def acc_dashboard(
    period_type: str = Query("DAY_ACC"),
    level_type: str | None = Query(None),
    parent_id: int | None = Query(None, ge=1),
    stat_date: str | None = Query(None),
):
    normalized = str(period_type or "").strip().upper()
    if normalized not in {"DAY_ACC", "MONTH"}:
        raise HTTPException(
            status_code=400,
            detail=f"period_type 只支持 DAY_ACC/MONTH: {period_type!r}",
        )
    engine = create_dashboard_engine()
    try:
        return get_acc_wide_table(
            engine,
            period_type=normalized,
            level_type=level_type,
            parent_id=parent_id,
            stat_date=stat_date,
        )
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"驾驶舱累计查询失败: {type(exc).__name__}",
        ) from exc
    finally:
        engine.dispose()


@router.get("/trend")
def trend_dashboard(
    area_id: int = Query(..., ge=1, description="区域 ID"),
    indicator_code: str = Query(..., description="指标编码"),
    minutes: int = Query(
        1440, ge=10, le=10080, description="回溯分钟数（默认 1440 = 24 小时）",
    ),
):
    engine = create_dashboard_engine()
    try:
        return get_snapshot_trend(
            engine,
            area_id=area_id,
            indicator_code=indicator_code,
            minutes=minutes,
        )
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"驾驶舱趋势查询失败: {type(exc).__name__}",
        ) from exc
    finally:
        engine.dispose()
