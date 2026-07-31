from __future__ import annotations

from collections import OrderedDict
from datetime import date, datetime
import json
from io import BytesIO
from threading import Event, Lock
from time import monotonic
from typing import Any, Callable
from zoneinfo import ZoneInfo

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.exc import SQLAlchemyError

from infrastructure.dashboard_mysql import get_dashboard_engine
from services.dashboard_v2_custom_indicator_service import (
    delete_custom_indicator,
    list_custom_indicators,
    update_indicator_settings,
    upsert_custom_indicator,
)
from services.dashboard_v2_query_service import (
    get_dashboard_overview,
    get_dashboard_matrix_page,
    get_drill_down,
    get_acc_options,
    get_acc_wide_table,
    get_current_wide_table,
    get_current_with_changes,
    get_indicator_catalog,
    get_historical_matrix_page,
    get_historical_run_id,
    get_historical_with_changes,
    get_history_options,
    get_history_range,
    get_latest_dashboard_run,
    parse_change_window_minutes,
)
from services.dashboard_v2_target_admin_service import (
    activate_target_plan,
    build_target_template,
    clone_target_plan,
    create_target_plan,
    get_target_values,
    import_target_template,
    list_target_plans,
    save_target_values,
    set_target_plan_realtime,
)
from services.dashboard_v2_target_service import TargetPlanError


router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])
_SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
_DASHBOARD_CACHE_TTL_SECONDS = 120.0
_HISTORY_CACHE_TTL_SECONDS = 10 * 60.0
_CACHE_FAILURE_TTL_SECONDS = 2.0
_DASHBOARD_CACHE_MAX_ENTRIES = 128
_DASHBOARD_CACHE_MAX_BYTES = 64 * 1024 * 1024
_VERSION_STATE_TTL_SECONDS = 2.0
_dashboard_cache: OrderedDict[
    tuple[Any, ...],
    tuple[float, dict[str, Any], int],
] = OrderedDict()
_dashboard_cache_lock = Lock()
_dashboard_cache_inflight: dict[tuple[Any, ...], Event] = {}
_dashboard_cache_failures: OrderedDict[
    tuple[Any, ...],
    tuple[float, Exception],
] = OrderedDict()
_dashboard_cache_total_bytes = 0
_dashboard_cache_stats = {
    "hits": 0,
    "misses": 0,
    "waits": 0,
    "loads": 0,
    "evictions": 0,
    "failures": 0,
}
_version_state_lock = Lock()
_version_state_cached_at = 0.0
_version_state_cache: dict[str, Any] | None = None


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


class TargetPlanPayload(BaseModel):
    plan_name: str = Field(..., min_length=1, max_length=200)
    scenario: str = Field(..., pattern="^(NORMAL|PK|normal|pk)$")
    period_type: str = Field(..., pattern="^(DAY|MONTH|day|month)$")
    effective_from: date
    priority: int = 0


class TargetValuePayload(BaseModel):
    node_id: int = Field(..., ge=1)
    indicator_id: int = Field(..., ge=1)
    target_value: float | str


class TargetValueSavePayload(BaseModel):
    values: list[TargetValuePayload]


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


def _history_local_time(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(_SHANGHAI_TZ).replace(tzinfo=None)


def _get_cached_version_state(engine) -> dict[str, Any]:
    global _version_state_cached_at, _version_state_cache
    now = monotonic()
    with _version_state_lock:
        if (
            _version_state_cache is not None
            and now - _version_state_cached_at <= _VERSION_STATE_TTL_SECONDS
        ):
            return _version_state_cache
        state = get_latest_dashboard_run(engine)
        _version_state_cache = state
        _version_state_cached_at = monotonic()
        return state


def _invalidate_version_state() -> None:
    global _version_state_cached_at, _version_state_cache
    with _version_state_lock:
        _version_state_cache = None
        _version_state_cached_at = 0.0


def _cache_result_size(result: dict[str, Any]) -> int:
    return len(json.dumps(
        result,
        ensure_ascii=False,
        default=str,
        separators=(",", ":"),
    ).encode("utf-8"))


def _remove_cache_entry(key: tuple[Any, ...]) -> None:
    global _dashboard_cache_total_bytes
    cached = _dashboard_cache.pop(key, None)
    if cached is not None:
        _dashboard_cache_total_bytes -= cached[2]


def _load_cached_response(
    *,
    namespace: str,
    resolved_key: tuple[Any, ...],
    ttl_seconds: float,
    loader: Callable[[], dict[str, Any]],
) -> dict[str, Any]:
    global _dashboard_cache_total_bytes
    full_key = (namespace, *resolved_key)

    while True:
        now = monotonic()
        with _dashboard_cache_lock:
            failed = _dashboard_cache_failures.get(full_key)
            if failed and now - failed[0] <= _CACHE_FAILURE_TTL_SECONDS:
                raise failed[1]
            if failed:
                _dashboard_cache_failures.pop(full_key, None)
            cached = _dashboard_cache.get(full_key)
            if cached and now - cached[0] <= ttl_seconds:
                _dashboard_cache_stats["hits"] += 1
                _dashboard_cache.move_to_end(full_key)
                return cached[1]
            if cached:
                _remove_cache_entry(full_key)
            pending = _dashboard_cache_inflight.get(full_key)
            if pending is None:
                _dashboard_cache_stats["misses"] += 1
                pending = Event()
                _dashboard_cache_inflight[full_key] = pending
                is_loader = True
            else:
                _dashboard_cache_stats["waits"] += 1
                is_loader = False
        if is_loader:
            break
        pending.wait(timeout=120.0)

    try:
        with _dashboard_cache_lock:
            _dashboard_cache_stats["loads"] += 1
        result = loader()
        result_size = _cache_result_size(result)
        with _dashboard_cache_lock:
            if namespace == "realtime":
                current_version = resolved_key[0]
                stale_keys = [
                    key for key in _dashboard_cache
                    if key[0] == "realtime" and key[1] != current_version
                ]
                for key in stale_keys:
                    _remove_cache_entry(key)
            _remove_cache_entry(full_key)
            _dashboard_cache_failures.pop(full_key, None)
            _dashboard_cache[full_key] = (monotonic(), result, result_size)
            _dashboard_cache_total_bytes += result_size
            while (
                len(_dashboard_cache) > _DASHBOARD_CACHE_MAX_ENTRIES
                or _dashboard_cache_total_bytes > _DASHBOARD_CACHE_MAX_BYTES
            ):
                oldest_key = next(iter(_dashboard_cache))
                _remove_cache_entry(oldest_key)
                _dashboard_cache_stats["evictions"] += 1
        return result
    except Exception as exc:
        with _dashboard_cache_lock:
            _dashboard_cache_stats["failures"] += 1
            _dashboard_cache_failures[full_key] = (monotonic(), exc)
            while len(_dashboard_cache_failures) > _DASHBOARD_CACHE_MAX_ENTRIES:
                _dashboard_cache_failures.popitem(last=False)
        raise
    finally:
        with _dashboard_cache_lock:
            pending = _dashboard_cache_inflight.pop(full_key, None)
            if pending is not None:
                pending.set()


def _cached_response(
    engine,
    cache_key: tuple[Any, ...],
    loader: Callable[[], dict[str, Any]],
) -> dict[str, Any]:
    version_state = _get_cached_version_state(engine)
    return _load_cached_response(
        namespace="realtime",
        resolved_key=(version_state.get("data_version"), *cache_key),
        ttl_seconds=_DASHBOARD_CACHE_TTL_SECONDS,
        loader=loader,
    )


def _cached_history_response(
    engine,
    *,
    as_of: datetime,
    cache_key: tuple[Any, ...],
    loader: Callable[[], dict[str, Any]],
) -> dict[str, Any]:
    version_state = _get_cached_version_state(engine)
    run_id = get_historical_run_id(engine, as_of)
    config_version = version_state.get("config_version")
    history_version = f"{run_id or 0}:{config_version or ''}"

    def load_with_version() -> dict[str, Any]:
        result = loader()
        result["data_version"] = history_version
        result["config_version"] = config_version
        return result

    return _load_cached_response(
        namespace="history",
        resolved_key=(history_version, *cache_key),
        ttl_seconds=_HISTORY_CACHE_TTL_SECONDS,
        loader=load_with_version,
    )


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
        result = upsert_custom_indicator(
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
        _invalidate_version_state()
        return result
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
        result = delete_custom_indicator(engine, code)
        _invalidate_version_state()
        return result
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
        result = update_indicator_settings(
            engine,
            code,
            enabled=payload.enabled,
            storage_mode=payload.storage_mode,
        )
        _invalidate_version_state()
        return result
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"指标设置保存失败: {type(exc).__name__}",
        ) from exc


@router.get("/target-plans")
def dashboard_target_plans(status: str | None = Query(None)):
    engine = get_dashboard_engine()
    try:
        return list_target_plans(engine, status=status)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"目标方案查询失败: {type(exc).__name__}",
        ) from exc


@router.post("/target-plans")
def create_dashboard_target_plan(payload: TargetPlanPayload):
    engine = get_dashboard_engine()
    try:
        result = create_target_plan(
            engine,
            plan_name=payload.plan_name,
            scenario=payload.scenario,
            period_type=payload.period_type,
            effective_from=payload.effective_from,
            priority=payload.priority,
        )
        _invalidate_version_state()
        return result
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"目标方案创建失败: {type(exc).__name__}",
        ) from exc


@router.post("/target-plans/{plan_id}/activate")
def activate_dashboard_target_plan(plan_id: int):
    engine = get_dashboard_engine()
    try:
        result = activate_target_plan(engine, plan_id)
        _invalidate_version_state()
        with _dashboard_cache_lock:
            _dashboard_cache.clear()
            _dashboard_cache_failures.clear()
        return result
    except TargetPlanError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"目标方案激活失败: {type(exc).__name__}",
        ) from exc


@router.post("/target-plans/{plan_id}/use-for-realtime")
def set_dashboard_target_plan_realtime(plan_id: int):
    engine = get_dashboard_engine()
    try:
        result = set_target_plan_realtime(engine, plan_id)
        _invalidate_version_state()
        with _dashboard_cache_lock:
            _dashboard_cache.clear()
            _dashboard_cache_failures.clear()
        return result
    except TargetPlanError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"实时目标设置失败: {type(exc).__name__}",
        ) from exc


@router.post("/target-plans/{plan_id}/clone")
def clone_dashboard_target_plan(plan_id: int, effective_from: date | None = None):
    """Create an editable DRAFT copy of an ACTIVE or RETIRED plan."""
    engine = get_dashboard_engine()
    try:
        result = clone_target_plan(
            engine, source_plan_id=plan_id, effective_from=effective_from
        )
        _invalidate_version_state()
        return result
    except (ValueError, TargetPlanError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"目标方案复制失败: {type(exc).__name__}",
        ) from exc


@router.get("/target-values")
def dashboard_target_values(
    plan_id: int = Query(..., ge=1),
    node_type: str | None = Query(None),
    indicator_code: str | None = Query(None),
    search: str | None = Query(None, max_length=100),
    limit: int = Query(500, ge=1, le=2000),
):
    engine = get_dashboard_engine()
    try:
        return get_target_values(
            engine,
            plan_id=plan_id,
            node_type=node_type,
            indicator_code=indicator_code,
            search=search,
            limit=limit,
        )
    except (ValueError, TargetPlanError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"目标值查询失败: {type(exc).__name__}",
        ) from exc


@router.put("/target-plans/{plan_id}/values")
def save_dashboard_target_values(plan_id: int, payload: TargetValueSavePayload):
    engine = get_dashboard_engine()
    try:
        return save_target_values(
            engine,
            plan_id=plan_id,
            values=[value.model_dump() for value in payload.values],
        )
    except (ValueError, TargetPlanError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"目标值保存失败: {type(exc).__name__}",
        ) from exc


@router.get("/target-template")
def dashboard_target_template():
    engine = get_dashboard_engine()
    try:
        content = build_target_template(engine)
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"目标值模板生成失败: {type(exc).__name__}",
        ) from exc
    filename = "dashboard-v2-target-template.xlsx"
    return StreamingResponse(
        BytesIO(content),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/target-template/import")
async def import_dashboard_target_template(
    plan_id: int = Query(..., ge=1),
    file: UploadFile = File(...),
):
    content = await file.read()
    engine = get_dashboard_engine()
    try:
        return import_target_template(engine, plan_id=plan_id, content=content)
    except (ValueError, TargetPlanError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"目标值导入失败: {type(exc).__name__}",
        ) from exc


@router.get("/latest-run")
def dashboard_latest_run():
    engine = get_dashboard_engine()
    try:
        return _get_cached_version_state(engine)
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"驾驶舱最新批次查询失败: {type(exc).__name__}",
        ) from exc


@router.get("/cache-stats")
def dashboard_cache_stats():
    with _dashboard_cache_lock:
        hits = _dashboard_cache_stats["hits"]
        misses = _dashboard_cache_stats["misses"]
        total_lookups = hits + misses
        namespace_entries = {
            "realtime": sum(1 for key in _dashboard_cache if key[0] == "realtime"),
            "history": sum(1 for key in _dashboard_cache if key[0] == "history"),
        }
        return {
            **_dashboard_cache_stats,
            "hit_rate": round(hits / total_lookups, 4) if total_lookups else 0.0,
            "entries": len(_dashboard_cache),
            "entries_by_namespace": namespace_entries,
            "inflight": len(_dashboard_cache_inflight),
            "memory_bytes": _dashboard_cache_total_bytes,
            "memory_limit_bytes": _DASHBOARD_CACHE_MAX_BYTES,
        }


@router.get("/history/range")
def dashboard_history_range():
    engine = get_dashboard_engine()
    try:
        return _cached_response(
            engine,
            ("history-range",),
            lambda: get_history_range(engine),
        )
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"历史可查范围查询失败: {type(exc).__name__}",
        ) from exc


@router.get("/history/options")
def dashboard_history_options(indicator_codes: str | None = Query(None)):
    engine = get_dashboard_engine()
    try:
        parsed_codes = _parse_indicator_codes(indicator_codes)
        return _cached_response(
            engine,
            ("history-options", tuple(parsed_codes or ())),
            lambda: get_history_options(engine, indicator_codes=parsed_codes),
        )
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"历史可选时间查询失败: {type(exc).__name__}",
        ) from exc


@router.get("/acc/options")
def dashboard_acc_options(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=50),
):
    engine = get_dashboard_engine()
    try:
        return _cached_response(
            engine,
            ("acc-options", page, page_size),
            lambda: get_acc_options(engine, page=page, page_size=page_size),
        )
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"累计可选日期查询失败: {type(exc).__name__}",
        ) from exc


@router.get("/history/current-with-changes")
def dashboard_history_with_changes(
    as_of: datetime = Query(...),
    node_type: str | None = Query(None),
    scope_mode: str = Query("default"),
    parent_id: int | None = Query(None, ge=1),
    parent_node_type: str | None = Query(None),
    branch_code: str | None = Query("AQ"),
    change_windows: str | None = Query(None),
    indicator_codes: str | None = Query(None),
    target_scenario: str = Query("NORMAL", pattern="^(NORMAL|PK)$"),
):
    engine = get_dashboard_engine()
    try:
        resolved_as_of = _history_local_time(as_of)
        parsed_windows = _parse_change_windows(change_windows)
        parsed_codes = _parse_indicator_codes(indicator_codes)
        return _cached_history_response(
            engine,
            as_of=resolved_as_of,
            cache_key=(
                "history-current-with-changes",
                resolved_as_of.isoformat(),
                node_type,
                scope_mode,
                parent_id,
                parent_node_type,
                branch_code,
                tuple(parsed_windows or ()),
                tuple(parsed_codes or ()),
                target_scenario,
            ),
            loader=lambda: get_historical_with_changes(
                engine,
                as_of=resolved_as_of,
                node_type=node_type,
                change_windows=parsed_windows,
                indicator_codes=parsed_codes,
                scope_mode=scope_mode,
                parent_id=parent_id,
                parent_node_type=parent_node_type,
                branch_code=branch_code,
                target_scenario=target_scenario,
            ),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"历史快照查询失败: {type(exc).__name__}",
        ) from exc


@router.get("/history/matrix")
def dashboard_history_matrix(
    as_of: datetime = Query(...),
    node_type: str = Query(...),
    scope_mode: str = Query("default"),
    parent_id: int | None = Query(None, ge=1),
    parent_node_type: str | None = Query(None),
    branch_code: str | None = Query("AQ"),
    indicator_codes: str | None = Query(None),
    change_window: int = Query(60, ge=5, le=1440),
    search: str | None = Query(None, max_length=100),
    sort_indicator: str | None = Query(None, max_length=100),
    sort_mode: str = Query("doneDesc"),
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=200),
    target_scenario: str = Query("NORMAL", pattern="^(NORMAL|PK)$"),
):
    engine = get_dashboard_engine()
    try:
        resolved_as_of = _history_local_time(as_of)
        parsed_codes = _parse_indicator_codes(indicator_codes)
        return _cached_history_response(
            engine,
            as_of=resolved_as_of,
            cache_key=(
                "history-matrix",
                resolved_as_of.isoformat(),
                node_type,
                scope_mode,
                parent_id,
                parent_node_type,
                branch_code,
                tuple(parsed_codes or ()),
                change_window,
                search,
                sort_indicator,
                sort_mode,
                page,
                page_size,
                target_scenario,
            ),
            loader=lambda: get_historical_matrix_page(
                engine,
                as_of=resolved_as_of,
                node_type=node_type,
                scope_mode=scope_mode,
                parent_id=parent_id,
                parent_node_type=parent_node_type,
                branch_code=branch_code,
                indicator_codes=parsed_codes,
                change_window=change_window,
                search=search,
                sort_indicator=sort_indicator,
                sort_mode=sort_mode,
                page=page,
                page_size=page_size,
                target_scenario=target_scenario,
            ),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"历史矩阵查询失败: {type(exc).__name__}",
        ) from exc


@router.get("/matrix")
def dashboard_matrix(
    node_type: str = Query(...),
    scope_mode: str = Query("default"),
    parent_id: int | None = Query(None, ge=1),
    parent_node_type: str | None = Query(None),
    branch_code: str | None = Query("AQ"),
    indicator_codes: str | None = Query(None),
    change_window: int = Query(60, ge=5, le=1440),
    search: str | None = Query(None, max_length=100),
    sort_indicator: str | None = Query(None, max_length=100),
    sort_mode: str = Query("doneDesc"),
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=200),
    value_mode: str = Query("REALTIME", pattern="^(REALTIME|REALTIME_ACC)$"),
    target_scenario: str = Query("NORMAL", pattern="^(NORMAL|PK)$"),
):
    engine = get_dashboard_engine()
    try:
        parsed_codes = _parse_indicator_codes(indicator_codes)
        return _cached_response(
            engine,
            (
                "matrix",
                node_type,
                scope_mode,
                parent_id,
                parent_node_type,
                branch_code,
                tuple(parsed_codes or ()),
                change_window,
                search,
                sort_indicator,
                sort_mode,
                page,
                page_size,
                value_mode,
                target_scenario,
            ),
            lambda: get_dashboard_matrix_page(
                engine,
                node_type=node_type,
                scope_mode=scope_mode,
                parent_id=parent_id,
                parent_node_type=parent_node_type,
                branch_code=branch_code,
                indicator_codes=parsed_codes,
                change_window=change_window,
                search=search,
                sort_indicator=sort_indicator,
                sort_mode=sort_mode,
                page=page,
                page_size=page_size,
                value_mode=value_mode,
                target_scenario=target_scenario,
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
    value_mode: str = Query("REALTIME", pattern="^(REALTIME|REALTIME_ACC)$"),
    target_scenario: str = Query("NORMAL", pattern="^(NORMAL|PK)$"),
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
                value_mode,
                target_scenario,
            ),
            lambda: get_dashboard_overview(
                engine,
                branch_id=branch_id,
                branch_code=branch_code,
                period_type=period_type,
                change_windows=parsed_windows,
                indicator_codes=parsed_codes,
                include_acc=include_acc,
                value_mode=value_mode,
                target_scenario=target_scenario,
            ),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"驾驶舱概览查询失败: {type(exc).__name__}",
        ) from exc


@router.get("/drill-down")
def dashboard_drill_down(
    parent_id: int = Query(..., ge=1),
    parent_node_type: str = Query(
        ...,
        description="父节点类型: CITY/BRANCH/GRID/CHANNEL_MANAGER",
    ),
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
    tree_mode: str = Query("full", pattern="^(full|flat)$"),
    value_mode: str = Query("REALTIME", pattern="^(REALTIME|REALTIME_ACC)$"),
    target_scenario: str = Query("NORMAL", pattern="^(NORMAL|PK)$"),
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
                parent_node_type,
                period_type,
                tuple(parsed_windows or ()),
                tuple(parsed_codes or ()),
                include_acc,
                tree_mode,
                value_mode,
                target_scenario,
            ),
            lambda: get_drill_down(
                engine,
                parent_id=parent_id,
                parent_node_type=parent_node_type,
                period_type=period_type,
                change_windows=parsed_windows,
                indicator_codes=parsed_codes,
                include_acc=include_acc,
                tree_mode=tree_mode,
                value_mode=value_mode,
                target_scenario=target_scenario,
            ),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"驾驶舱下钻查询失败: {type(exc).__name__}",
        ) from exc


@router.get("/current")
def current_dashboard(
    node_type: str | None = Query(None),
    parent_id: int | None = Query(None, ge=1),
    indicator_codes: str | None = Query(None),
    value_mode: str = Query("REALTIME", pattern="^(REALTIME|REALTIME_ACC)$"),
    target_scenario: str = Query("NORMAL", pattern="^(NORMAL|PK)$"),
):
    engine = get_dashboard_engine()
    try:
        return get_current_wide_table(
            engine,
            node_type=node_type,
            parent_id=parent_id,
            indicator_codes=_parse_indicator_codes(indicator_codes),
            value_mode=value_mode,
            target_scenario=target_scenario,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"驾驶舱查询失败: {type(exc).__name__}",
        ) from exc


@router.get("/current-with-changes")
def current_with_changes(
    node_type: str | None = Query(None),
    parent_id: int | None = Query(None, ge=1),
    change_windows: str | None = Query(
        None,
        description="逗号分隔的变化窗口分钟数，最多 4 个，例如 5,15,30,60",
    ),
    indicator_codes: str | None = Query(None),
    value_mode: str = Query("REALTIME", pattern="^(REALTIME|REALTIME_ACC)$"),
    target_scenario: str = Query("NORMAL", pattern="^(NORMAL|PK)$"),
):
    engine = get_dashboard_engine()
    try:
        parsed_windows = _parse_change_windows(change_windows)
        parsed_codes = _parse_indicator_codes(indicator_codes)
        return _cached_response(
            engine,
            (
                "current-with-changes",
                node_type,
                parent_id,
                tuple(parsed_windows or ()),
                tuple(parsed_codes or ()),
                value_mode,
                target_scenario,
            ),
            lambda: get_current_with_changes(
                engine,
                node_type=node_type,
                parent_id=parent_id,
                change_windows=parsed_windows,
                indicator_codes=parsed_codes,
                value_mode=value_mode,
                target_scenario=target_scenario,
            ),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"驾驶舱变化量查询失败: {type(exc).__name__}",
        ) from exc


@router.get("/acc")
def acc_dashboard(
    period_type: str = Query("DAY_ACC"),
    node_type: str | None = Query(None),
    parent_id: int | None = Query(None, ge=1),
    stat_date: str | None = Query(None),
    indicator_codes: str | None = Query(None),
    target_scenario: str = Query("NORMAL", pattern="^(NORMAL|PK)$"),
    target_period: str | None = Query(None, pattern="^(DAY|MONTH)$"),
    target_source: str = Query("ASSESSMENT", pattern="^(WORKING|ASSESSMENT)$"),
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
                node_type,
                parent_id,
                stat_date,
                tuple(parsed_codes or ()),
                target_scenario,
                target_period,
                target_source,
            ),
            lambda: get_acc_wide_table(
                engine,
                period_type=normalized,
                node_type=node_type,
                parent_id=parent_id,
                stat_date=stat_date,
                indicator_codes=parsed_codes,
                target_scenario=target_scenario,
                target_period=target_period,
                target_source=target_source,
            ),
        )
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"驾驶舱累计查询失败: {type(exc).__name__}",
        ) from exc


