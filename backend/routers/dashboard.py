from __future__ import annotations

from collections import OrderedDict
from threading import Lock
from time import monotonic
from typing import Any, Callable

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.exc import SQLAlchemyError

from infrastructure.dashboard_mysql import get_dashboard_engine
from services.dashboard_custom_indicator_service import (
    delete_custom_indicator,
    list_custom_indicators,
    update_indicator_settings,
    upsert_custom_indicator,
)
from services.dashboard_query_service import (
    get_dashboard_overview,
    get_dashboard_matrix_page,
    get_drill_down,
    get_acc_wide_table,
    get_current_wide_table,
    get_current_with_changes,
    get_indicator_catalog,
    get_latest_dashboard_run,
    parse_change_window_minutes,
)


router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])
_DASHBOARD_CACHE_TTL_SECONDS = 20.0
_DASHBOARD_CACHE_MAX_ENTRIES = 64
_dashboard_cache: OrderedDict[tuple[Any, ...], tuple[float, dict[str, Any]]] = OrderedDict()
_dashboard_cache_lock = Lock()


class CustomIndicatorComponentPayload(BaseModel):
    source_code: str = Field(..., min_length=1, max_length=100)
    coefficient: float = 1
    source_storage_mode: str | None = Field(None, pattern="^(STORE|COMPONENT)$")


class CustomIndicatorPayload(BaseModel):
    code: str = Field(..., min_length=1, max_length=100)
    name: str = Field(..., min_length=1, max_length=200)
    enabled: bool = True
    sort_order: int | None = Field(None, ge=0)
    components: list[CustomIndicatorComponentPayload]


class IndicatorSettingsPayload(BaseModel):
    enabled: bool | None = None
    storage_mode: str | None = Field(None, pattern="^(STORE|COMPONENT)$")


def _parse_change_windows(value: str | None) -> list[int] | None:
    try:
        return parse_change_window_minutes(value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _parse_indicator_codes(value: str | None) -> list[str] | None:
    if not value:
        return None
    codes: list[str] = []
    for item in value.split(","):
        code = item.strip()
        if not code or code in codes:
            continue
        if len(code) > 100:
            raise HTTPException(status_code=400, detail="indicator_codes 包含超长指标编码")
        codes.append(code)
        if len(codes) >= 20:
            break
    return codes or None


def _cached_response(
    engine,
    cache_key: tuple[Any, ...],
    loader: Callable[[], dict[str, Any]],
) -> dict[str, Any]:
    latest_state = get_latest_dashboard_run(engine)
    data_version = latest_state.get("data_version")
    resolved_key = (data_version, *cache_key)
    now = monotonic()
    with _dashboard_cache_lock:
        cached = _dashboard_cache.get(resolved_key)
        if cached and now - cached[0] <= _DASHBOARD_CACHE_TTL_SECONDS:
            _dashboard_cache.move_to_end(resolved_key)
            return cached[1]
        if cached:
            _dashboard_cache.pop(resolved_key, None)

    result = loader()
    with _dashboard_cache_lock:
        stale_keys = [key for key in _dashboard_cache if key[0] != data_version]
        for key in stale_keys:
            _dashboard_cache.pop(key, None)
        _dashboard_cache[resolved_key] = (now, result)
        while len(_dashboard_cache) > _DASHBOARD_CACHE_MAX_ENTRIES:
            _dashboard_cache.popitem(last=False)
    return result


@router.get("/indicators")
def dashboard_indicators(
    include_archived: bool = Query(False),
    enabled_only: bool = Query(False),
):
    engine = get_dashboard_engine()
    try:
        return get_indicator_catalog(
            engine,
            include_archived=include_archived,
            enabled_only=enabled_only,
        )
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"驾驶舱指标目录查询失败: {type(exc).__name__}",
        ) from exc


@router.get("/custom-indicators")
def dashboard_custom_indicators():
    engine = get_dashboard_engine()
    try:
        return list_custom_indicators(engine)
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"自建指标查询失败: {type(exc).__name__}",
        ) from exc


@router.post("/custom-indicators")
def save_dashboard_custom_indicator(payload: CustomIndicatorPayload):
    engine = get_dashboard_engine()
    try:
        return upsert_custom_indicator(
            engine,
            code=payload.code,
            name=payload.name,
            enabled=payload.enabled,
            sort_order=payload.sort_order,
            components=[
                component.model_dump()
                for component in payload.components
            ],
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"自建指标保存失败: {type(exc).__name__}",
        ) from exc


@router.delete("/custom-indicators/{code}")
def remove_dashboard_custom_indicator(code: str):
    engine = get_dashboard_engine()
    try:
        return delete_custom_indicator(engine, code)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"自建指标删除失败: {type(exc).__name__}",
        ) from exc


@router.patch("/indicators/{code}")
def update_dashboard_indicator(code: str, payload: IndicatorSettingsPayload):
    engine = get_dashboard_engine()
    try:
        return update_indicator_settings(
            engine,
            code,
            enabled=payload.enabled,
            storage_mode=payload.storage_mode,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"指标设置保存失败: {type(exc).__name__}",
        ) from exc


@router.get("/latest-run")
def dashboard_latest_run():
    engine = get_dashboard_engine()
    try:
        return get_latest_dashboard_run(engine)
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"驾驶舱最新批次查询失败: {type(exc).__name__}",
        ) from exc


@router.get("/matrix")
def dashboard_matrix(
    level_type: str = Query(...),
    scope_mode: str = Query("default"),
    parent_id: int | None = Query(None, ge=1),
    parent_level: str | None = Query(None),
    branch_code: str | None = Query("AQ"),
    indicator_codes: str | None = Query(None),
    change_window: int = Query(60, ge=5, le=1440),
    search: str | None = Query(None, max_length=100),
    sort_indicator: str | None = Query(None, max_length=100),
    sort_mode: str = Query("doneDesc"),
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=200),
):
    engine = get_dashboard_engine()
    try:
        parsed_codes = _parse_indicator_codes(indicator_codes)
        return _cached_response(
            engine,
            (
                "matrix",
                level_type,
                scope_mode,
                parent_id,
                parent_level,
                branch_code,
                tuple(parsed_codes or ()),
                change_window,
                search,
                sort_indicator,
                sort_mode,
                page,
                page_size,
            ),
            lambda: get_dashboard_matrix_page(
                engine,
                level_type=level_type,
                scope_mode=scope_mode,
                parent_id=parent_id,
                parent_level=parent_level,
                branch_code=branch_code,
                indicator_codes=parsed_codes,
                change_window=change_window,
                search=search,
                sort_indicator=sort_indicator,
                sort_mode=sort_mode,
                page=page,
                page_size=page_size,
            ),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"驾驶舱矩阵查询失败: {type(exc).__name__}",
        ) from exc


@router.get("/overview")
def dashboard_overview(
    branch_id: int | None = Query(None, ge=1),
    branch_code: str | None = Query(None),
    period_type: str = Query("DAY_ACC"),
    change_windows: str | None = Query(
        None,
        description="逗号分隔的变化窗口分钟数，最多 4 个，例如 5,15,30,60",
    ),
    indicator_codes: str | None = Query(
        None,
        description="逗号分隔的指标编码；不传则查询全部启用指标",
    ),
    include_acc: bool = Query(True),
):
    engine = get_dashboard_engine()
    try:
        parsed_windows = _parse_change_windows(change_windows)
        parsed_codes = _parse_indicator_codes(indicator_codes)
        return _cached_response(
            engine,
            (
                "overview",
                branch_id,
                branch_code,
                period_type,
                tuple(parsed_windows or ()),
                tuple(parsed_codes or ()),
                include_acc,
            ),
            lambda: get_dashboard_overview(
                engine,
                branch_id=branch_id,
                branch_code=branch_code,
                period_type=period_type,
                change_windows=parsed_windows,
                indicator_codes=parsed_codes,
                include_acc=include_acc,
            ),
        )
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"驾驶舱概览查询失败: {type(exc).__name__}",
        ) from exc


@router.get("/drill-down")
def dashboard_drill_down(
    parent_id: int = Query(..., ge=1),
    parent_level: str = Query(..., description="父级层级: CITY/BRANCH/GRID"),
    period_type: str = Query("DAY_ACC"),
    change_windows: str | None = Query(
        None,
        description="逗号分隔的变化窗口分钟数，最多 4 个，例如 5,15,30,60",
    ),
    indicator_codes: str | None = Query(
        None,
        description="逗号分隔的指标编码；不传则查询全部启用指标",
    ),
    include_acc: bool = Query(True),
):
    """Lightweight drill-down: only returns children of the given parent."""
    engine = get_dashboard_engine()
    try:
        parsed_windows = _parse_change_windows(change_windows)
        parsed_codes = _parse_indicator_codes(indicator_codes)
        return _cached_response(
            engine,
            (
                "drill-down",
                parent_id,
                parent_level,
                period_type,
                tuple(parsed_windows or ()),
                tuple(parsed_codes or ()),
                include_acc,
            ),
            lambda: get_drill_down(
                engine,
                parent_id=parent_id,
                parent_level=parent_level,
                period_type=period_type,
                change_windows=parsed_windows,
                indicator_codes=parsed_codes,
                include_acc=include_acc,
            ),
        )
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"驾驶舱下钻查询失败: {type(exc).__name__}",
        ) from exc


@router.get("/current")
def current_dashboard(
    level_type: str | None = Query(None),
    parent_id: int | None = Query(None, ge=1),
    indicator_codes: str | None = Query(None),
):
    engine = get_dashboard_engine()
    try:
        return get_current_wide_table(
            engine,
            level_type=level_type,
            parent_id=parent_id,
            indicator_codes=_parse_indicator_codes(indicator_codes),
        )
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"驾驶舱查询失败: {type(exc).__name__}",
        ) from exc


@router.get("/current-with-changes")
def current_with_changes(
    level_type: str | None = Query(None),
    parent_id: int | None = Query(None, ge=1),
    change_windows: str | None = Query(
        None,
        description="逗号分隔的变化窗口分钟数，最多 4 个，例如 5,15,30,60",
    ),
    indicator_codes: str | None = Query(None),
):
    engine = get_dashboard_engine()
    try:
        parsed_windows = _parse_change_windows(change_windows)
        parsed_codes = _parse_indicator_codes(indicator_codes)
        return _cached_response(
            engine,
            (
                "current-with-changes",
                level_type,
                parent_id,
                tuple(parsed_windows or ()),
                tuple(parsed_codes or ()),
            ),
            lambda: get_current_with_changes(
                engine,
                level_type=level_type,
                parent_id=parent_id,
                change_windows=parsed_windows,
                indicator_codes=parsed_codes,
            ),
        )
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"驾驶舱变化量查询失败: {type(exc).__name__}",
        ) from exc


@router.get("/acc")
def acc_dashboard(
    period_type: str = Query("DAY_ACC"),
    level_type: str | None = Query(None),
    parent_id: int | None = Query(None, ge=1),
    stat_date: str | None = Query(None),
    indicator_codes: str | None = Query(None),
):
    normalized = str(period_type or "").strip().upper()
    if normalized not in {"DAY_ACC", "MONTH"}:
        raise HTTPException(
            status_code=400,
            detail=f"period_type 只支持 DAY_ACC/MONTH: {period_type!r}",
        )
    engine = get_dashboard_engine()
    try:
        parsed_codes = _parse_indicator_codes(indicator_codes)
        return _cached_response(
            engine,
            (
                "acc",
                normalized,
                level_type,
                parent_id,
                stat_date,
                tuple(parsed_codes or ()),
            ),
            lambda: get_acc_wide_table(
                engine,
                period_type=normalized,
                level_type=level_type,
                parent_id=parent_id,
                stat_date=stat_date,
                indicator_codes=parsed_codes,
            ),
        )
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"驾驶舱累计查询失败: {type(exc).__name__}",
        ) from exc


