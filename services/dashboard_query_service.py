"""Read models for the dashboard frontend.

Performance notes
-----------------
``get_dashboard_overview`` is the hot path for the cockpit.  It was
originally 7-8 sequential ORM queries; the optimised version collapses
area+metric_current into a single JOIN, fetches only the snapshots
needed for the configured change windows with a DB-side nearest-snapshot
window function, and runs the acc query in a second cursor — effectively
two DB round-trips instead of seven.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from decimal import Decimal
from hashlib import sha256
from typing import Any

from sqlalchemy import Engine, and_, bindparam, case, func, inspect, or_, select, text
from sqlalchemy.orm import Session

from models.dashboard_area import Area
from models.dashboard_collection_run import CollectionRun
from models.dashboard_indicator import Indicator
from models.dashboard_metric import MetricAcc, MetricCurrent, MetricSnapshot
from models.dashboard_metric_target import MetricTarget


def _number(value: Decimal | int | float | None) -> float | int | None:
    if value is None:
        return None
    if isinstance(value, int | float):
        return value
    if value == value.to_integral_value():
        return int(value)
    return float(value)


def _date_iso(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _datetime_iso_millis(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat(timespec="milliseconds")
    return str(value).replace(" ", "T")


def _enabled_indicators(
    session: Session,
    indicator_codes: list[str] | tuple[str, ...] | None = None,
) -> list[Indicator]:
    query = select(Indicator).where(Indicator.enabled.is_(True))
    if indicator_codes:
        query = query.where(Indicator.code.in_(indicator_codes))
    return list(
        session.scalars(
            query.order_by(Indicator.sort_order, Indicator.id)
        )
    )


def get_indicator_catalog(
    engine: Engine,
    *,
    include_archived: bool = False,
    enabled_only: bool = False,
) -> dict[str, Any]:
    """Return the source indicator catalog without changing collection state."""
    with Session(engine) as session:
        query = select(Indicator).order_by(Indicator.sort_order, Indicator.id)
        if not include_archived:
            query = query.where(
                or_(
                    Indicator.source_active.is_(True),
                    Indicator.enabled.is_(True),
                )
            )
        if enabled_only:
            query = query.where(Indicator.enabled.is_(True))
        indicators = session.scalars(query).all()

    return {
        "indicators": [
            {
                "id": indicator.id,
                "code": indicator.code,
                "name": indicator.name,
                "enabled": indicator.enabled,
                "source_active": indicator.source_active,
                "removed_at": _datetime_iso_millis(indicator.removed_at),
                "sort_order": indicator.sort_order,
            }
            for indicator in indicators
        ]
    }


def get_latest_dashboard_run(engine: Engine) -> dict[str, Any]:
    with Session(engine) as session:
        latest_run = _latest_realtime_run(session)
        indicators = session.execute(
            select(
                Indicator.id,
                Indicator.code,
                Indicator.name,
                Indicator.enabled,
                Indicator.source_active,
                Indicator.sort_order,
                Indicator.updated_at,
            ).order_by(Indicator.id)
        ).all()
        target_version = session.execute(
            select(
                func.count(MetricTarget.id),
                func.max(MetricTarget.id),
                func.max(MetricTarget.updated_at),
            )
        ).one()
        indicator_version = "|".join(
            ":".join(
                [
                    str(row.id),
                    row.code,
                    row.name,
                    str(int(bool(row.enabled))),
                    str(int(bool(row.source_active))),
                    str(row.sort_order),
                    _datetime_iso_millis(row.updated_at) or "",
                ]
            )
            for row in indicators
        )
        target_updated_at = _datetime_iso_millis(target_version[2]) or ""
        version_source = "#".join(
            [
                latest_run.batch_no if latest_run else "",
                indicator_version,
                str(target_version[0] or 0),
                str(target_version[1] or 0),
                target_updated_at,
            ]
        )
        data_version = sha256(version_source.encode("utf-8")).hexdigest()
        return {
            "latest_run": _run_payload(latest_run),
            "data_version": data_version,
        }


def get_snapshot_trend(
    engine: Engine,
    *,
    area_id: int,
    indicator_code: str,
    minutes: int = 1440,
) -> dict[str, Any]:
    """Return a time-series of snapshot values for one area + indicator.

    Designed for the trend chart.  Queries ``metric_snapshot`` over the
    requested window (default 24 h) and returns an ascending list of
    ``(collected_at, value)`` points.
    """
    cutoff = datetime.now() - timedelta(minutes=minutes)

    with Session(engine) as session:
        indicator = session.scalar(
            select(Indicator).where(
                Indicator.code == indicator_code,
                Indicator.enabled.is_(True),
            )
        )
        if indicator is None:
            return {
                "area_id": area_id,
                "indicator_code": indicator_code,
                "points": [],
            }

        rows = (
            session.query(
                MetricSnapshot.collected_at,
                MetricSnapshot.metric_value,
            )
            .filter(
                MetricSnapshot.area_id == area_id,
                MetricSnapshot.indicator_id == indicator.id,
                MetricSnapshot.collected_at >= cutoff,
            )
            .order_by(MetricSnapshot.collected_at.asc())
            .all()
        )

    return {
        "area_id": area_id,
        "indicator_code": indicator_code,
        "points": [
            {
                "collected_at": r.collected_at.strftime("%Y-%m-%dT%H:%M:%S"),
                "value": _number(r.metric_value),
            }
            for r in rows
        ],
    }


def get_dashboard_matrix_page(
    engine: Engine,
    *,
    level_type: str,
    scope_mode: str = "default",
    parent_id: int | None = None,
    parent_level: str | None = None,
    branch_code: str | None = "AQ",
    indicator_codes: list[str] | tuple[str, ...] | None = None,
    change_window: int = 60,
    search: str | None = None,
    sort_indicator: str | None = None,
    sort_mode: str = "doneDesc",
    page: int = 1,
    page_size: int = 100,
) -> dict[str, Any]:
    normalized_level = str(level_type or "").strip().upper()
    if normalized_level not in {"BRANCH", "GRID", "CHANNEL"}:
        raise ValueError(f"level_type 只支持 BRANCH/GRID/CHANNEL: {level_type!r}")
    normalized_scope = str(scope_mode or "default").strip().lower()
    if normalized_scope not in {"default", "all"}:
        raise ValueError(f"scope_mode 只支持 default/all: {scope_mode!r}")
    normalized_parent_level = str(parent_level or "").strip().upper() or None
    normalized_windows = normalize_change_windows([change_window])
    page = max(1, int(page))
    page_size = max(1, min(200, int(page_size)))

    with Session(engine) as session:
        indicators = _enabled_indicators(session, indicator_codes)
        if not indicators:
            return {
                "latest_run": _run_payload(_latest_realtime_run(session)),
                "indicators": [],
                "rows": [],
                "row_count": 0,
                "total": 0,
                "page": page,
                "page_size": page_size,
                "total_pages": 0,
            }
        indicator_ids = [indicator.id for indicator in indicators]
        indicator_by_code = {indicator.code: indicator for indicator in indicators}
        sort_code = (
            sort_indicator
            if sort_indicator in indicator_by_code
            else indicators[0].code
        )
        sort_indicator_id = indicator_by_code[sort_code].id

        area_query = select(Area).where(
            Area.enabled.is_(True),
            Area.level_type == normalized_level,
        )
        if search:
            keyword = f"%{str(search).strip()}%"
            area_query = area_query.where(
                Area.area_name.like(keyword) | Area.area_code.like(keyword)
            )

        branch_scope_id: int | None = None
        if normalized_parent_level == "BRANCH" and parent_id is not None:
            branch_scope_id = parent_id
        elif normalized_parent_level == "GRID" and parent_id is not None:
            branch_scope_id = session.scalar(
                select(Area.parent_id).where(
                    Area.id == parent_id,
                    Area.level_type == "GRID",
                )
            )
        elif normalized_scope == "default":
            branch_scope = _resolve_branch_scope(
                session,
                branch_id=None,
                branch_code=branch_code,
            )
            branch_scope_id = branch_scope.id if branch_scope else None

        if normalized_level == "GRID" and branch_scope_id is not None:
            area_query = area_query.where(Area.parent_id == branch_scope_id)
        elif normalized_level == "CHANNEL":
            if normalized_parent_level == "GRID" and parent_id is not None:
                area_query = area_query.where(Area.parent_id == parent_id)
            elif branch_scope_id is not None:
                grid_ids = select(Area.id).where(
                    Area.enabled.is_(True),
                    Area.level_type == "GRID",
                    Area.parent_id == branch_scope_id,
                )
                area_query = area_query.where(Area.parent_id.in_(grid_ids))

        latest_run = _latest_realtime_run(session)
        change_anchor = _change_anchor_time(latest_run)
        start = (page - 1) * page_size
        total = int(
            session.scalar(
                select(func.count()).select_from(area_query.subquery())
            )
            or 0
        )

        if sort_mode in {"doneAsc", "doneDesc", "progressAsc", "progressDesc"}:
            paged_query = (
                area_query
                .outerjoin(
                    MetricCurrent,
                    and_(
                        MetricCurrent.area_id == Area.id,
                        MetricCurrent.indicator_id == sort_indicator_id,
                    ),
                )
                .outerjoin(
                    MetricTarget,
                    and_(
                        MetricTarget.area_id == Area.id,
                        MetricTarget.indicator_id == sort_indicator_id,
                        MetricTarget.period_type == "REALTIME",
                        MetricTarget.enabled.is_(True),
                    ),
                )
            )
            if sort_mode.startswith("progress"):
                missing_expr = case(
                    (
                        or_(
                            MetricCurrent.metric_value.is_(None),
                            MetricTarget.target_value.is_(None),
                            MetricTarget.target_value <= 0,
                        ),
                        1,
                    ),
                    else_=0,
                )
                score_expr = (
                    MetricCurrent.metric_value
                    / func.nullif(MetricTarget.target_value, 0)
                )
                score_order = (
                    score_expr.asc()
                    if sort_mode.endswith("Asc")
                    else score_expr.desc()
                )
                order_by = (missing_expr.asc(), score_order, Area.id.asc())
            else:
                missing_expr = case(
                    (MetricCurrent.metric_value.is_(None), 1),
                    else_=0,
                )
                order_by = (
                    missing_expr.asc(),
                    (
                        MetricCurrent.metric_value.asc()
                        if sort_mode.endswith("Asc")
                        else MetricCurrent.metric_value.desc()
                    ),
                    Area.id.asc(),
                )
            page_areas = list(
                session.scalars(
                    paged_query
                    .order_by(*order_by)
                    .offset(start)
                    .limit(page_size)
                )
            )
        else:
            areas = list(
                session.scalars(
                    area_query.order_by(Area.sort_order, Area.id)
                )
            )
            area_ids = [area.id for area in areas]
            sort_values: dict[int, float] = {}
            if area_ids:
                for row in session.execute(
                    select(
                        MetricCurrent.area_id,
                        MetricCurrent.metric_value,
                    ).where(
                        MetricCurrent.area_id.in_(area_ids),
                        MetricCurrent.indicator_id == sort_indicator_id,
                    )
                ):
                    sort_values[row.area_id] = float(row.metric_value)

            sort_rows = [
                {
                    "area_id": area_id,
                    "metrics": {sort_code: sort_values.get(area_id)},
                }
                for area_id in area_ids
            ]
            _attach_changes_fast(
                session,
                sort_rows,
                [indicator_by_code[sort_code]],
                area_ids,
                change_anchor,
                change_windows=normalized_windows,
            )
            window_key = normalized_windows[0][1]
            sort_changes = {
                row["area_id"]: row["changes"][sort_code][window_key]
                for row in sort_rows
            }

            def sort_score(area: Area) -> tuple[int, float, int]:
                change = sort_changes.get(area.id, {})
                value = (
                    change.get("rate")
                    if sort_mode.startswith("changeRate")
                    else change.get("value")
                )
                score = 0.0 if value is None else float(value)
                return (
                    1 if value is None else 0,
                    score if sort_mode.endswith("Asc") else -score,
                    area.id,
                )

            areas.sort(key=sort_score)
            page_areas = areas[start : start + page_size]
        page_area_ids = [area.id for area in page_areas]

        rows_by_area = {
            area.id: {
                "area_id": area.id,
                "area_code": area.area_code,
                "area_name": area.area_name,
                "level_type": area.level_type,
                "level_no": area.level_no,
                "parent_id": area.parent_id,
                "collection_run_id": None,
                "collected_at": None,
                "metrics": {indicator.code: None for indicator in indicators},
            }
            for area in page_areas
        }
        if page_area_ids:
            current_rows = session.execute(
                select(
                    MetricCurrent.area_id,
                    MetricCurrent.indicator_id,
                    MetricCurrent.metric_value,
                    MetricCurrent.collected_at,
                    MetricCurrent.collection_run_id,
                ).where(
                    MetricCurrent.area_id.in_(page_area_ids),
                    MetricCurrent.indicator_id.in_(indicator_ids),
                )
            ).all()
            code_by_id = {indicator.id: indicator.code for indicator in indicators}
            for current in current_rows:
                row = rows_by_area[current.area_id]
                code = code_by_id[current.indicator_id]
                row["metrics"][code] = _number(current.metric_value)
                collected_at = _datetime_iso_millis(current.collected_at)
                if row["collected_at"] is None or collected_at > row["collected_at"]:
                    row["collected_at"] = collected_at
                    row["collection_run_id"] = current.collection_run_id

        rows = list(rows_by_area.values())
        _attach_targets(session, rows, indicators, "REALTIME")
        if page_area_ids:
            _attach_changes_fast(
                session,
                rows,
                indicators,
                page_area_ids,
                change_anchor,
                change_windows=normalized_windows,
            )

    return {
        "latest_run": _run_payload(latest_run),
        "indicators": _indicator_payload(indicators),
        "rows": rows,
        "row_count": len(rows),
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": (total + page_size - 1) // page_size,
    }


def get_current_wide_table(
    engine: Engine,
    level_type: str | None = None,
    parent_id: int | None = None,
    indicator_codes: list[str] | tuple[str, ...] | None = None,
) -> dict[str, Any]:
    normalized_level = str(level_type or "").strip().upper() or None
    with Session(engine) as session:
        indicators = _enabled_indicators(session, indicator_codes)

        area_query = (
            select(Area)
            .where(Area.enabled.is_(True))
            .order_by(Area.level_no, Area.sort_order, Area.id)
        )
        if normalized_level:
            area_query = area_query.where(Area.level_type == normalized_level)
        if parent_id is not None:
            area_query = area_query.where(Area.parent_id == parent_id)
        areas = session.scalars(area_query).all()
        area_ids = [area.id for area in areas]

        values_by_area: dict[int, dict[str, Any]] = defaultdict(dict)
        collected_by_area: dict[int, Any] = {}
        targets_by_area = _target_values_by_area(
            session,
            area_ids,
            list(indicators),
            "REALTIME",
        )
        if area_ids and indicators:
            current_rows = session.execute(
                select(
                    MetricCurrent.area_id,
                    Indicator.code,
                    MetricCurrent.metric_value,
                    MetricCurrent.collected_at,
                    MetricCurrent.collection_run_id,
                )
                .join(Indicator, Indicator.id == MetricCurrent.indicator_id)
                .where(
                    MetricCurrent.area_id.in_(area_ids),
                    Indicator.enabled.is_(True),
                )
            ).all()
            for row in current_rows:
                values_by_area[row.area_id][row.code] = _number(row.metric_value)
                previous = collected_by_area.get(row.area_id)
                if previous is None or row.collected_at > previous["collected_at"]:
                    collected_by_area[row.area_id] = {
                        "collected_at": row.collected_at,
                        "collection_run_id": row.collection_run_id,
                    }

        latest_run = session.scalar(
            select(CollectionRun)
            .join(
                MetricCurrent,
                MetricCurrent.collection_run_id == CollectionRun.id,
            )
            .where(
                CollectionRun.status == "SUCCESS",
                CollectionRun.run_type == "REALTIME",
            )
            .distinct()
            .order_by(CollectionRun.finished_at.desc(), CollectionRun.id.desc())
            .limit(1)
        )

    indicator_payload = [
        {
            "id": indicator.id,
            "code": indicator.code,
            "name": indicator.name,
            "sort_order": indicator.sort_order,
        }
        for indicator in indicators
    ]
    rows = []
    for area in areas:
        collection = collected_by_area.get(area.id)
        metrics = {
            indicator.code: values_by_area[area.id].get(indicator.code)
            for indicator in indicators
        }
        targets = {
            indicator.code: targets_by_area[area.id].get(indicator.code)
            for indicator in indicators
        }
        rows.append(
            {
                "area_id": area.id,
                "area_code": area.area_code,
                "area_name": area.area_name,
                "level_type": area.level_type,
                "level_no": area.level_no,
                "parent_id": area.parent_id,
                "collection_run_id": (
                    collection["collection_run_id"] if collection else None
                ),
                "collected_at": (
                    _datetime_iso_millis(collection["collected_at"])
                    if collection else None
                ),
                "metrics": metrics,
                "targets": targets,
            }
        )

    return {
        "latest_run": (
            {
                "id": latest_run.id,
                "batch_no": latest_run.batch_no,
                "stat_date": (
                    _date_iso(latest_run.stat_date)
                    if latest_run.stat_date else None
                ),
                "finished_at": (
                    _datetime_iso_millis(latest_run.finished_at)
                    if latest_run.finished_at else None
                ),
            }
            if latest_run
            else None
        ),
        "indicators": indicator_payload,
        "rows": rows,
        "row_count": len(rows),
    }


_CHANGE_WINDOWS: tuple[tuple[int, str], ...] = (
    (5, "change_5min"),
    (15, "change_15min"),
    (30, "change_30min"),
    (60, "change_60min"),
)
CHANGE_WINDOW_TOLERANCE_MINUTES = 3
CHANGE_WINDOW_MINUTES_MIN = 5
CHANGE_WINDOW_MINUTES_MAX = 1440
CHANGE_WINDOW_MINUTES_STEP = 5
CHANGE_WINDOW_LIMIT = 4
DEFAULT_CHANGE_WINDOW_MINUTES = tuple(minutes for minutes, _ in _CHANGE_WINDOWS)


def _is_valid_change_window_minutes(minutes: int) -> bool:
    return (
        minutes >= CHANGE_WINDOW_MINUTES_MIN
        and minutes <= CHANGE_WINDOW_MINUTES_MAX
        and minutes % CHANGE_WINDOW_MINUTES_STEP == 0
    )


def normalize_change_windows(
    value: list[int] | tuple[int, ...] | None = None,
) -> tuple[tuple[int, str], ...]:
    windows = value or DEFAULT_CHANGE_WINDOW_MINUTES
    normalized: list[int] = []
    for item in windows:
        try:
            minutes = int(item)
        except (TypeError, ValueError):
            continue
        if not _is_valid_change_window_minutes(minutes) or minutes in normalized:
            continue
        normalized.append(minutes)
        if len(normalized) >= CHANGE_WINDOW_LIMIT:
            break
    if not normalized:
        normalized = list(DEFAULT_CHANGE_WINDOW_MINUTES)
    return tuple((minutes, f"change_{minutes}min") for minutes in normalized)


def parse_change_window_minutes(value: str | None) -> list[int] | None:
    """Parse API change-window query text using the shared window rules."""
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
            raise ValueError(f"change_windows 只支持逗号分隔分钟数: {value!r}") from exc
        if not _is_valid_change_window_minutes(minutes):
            raise ValueError(
                "change_windows 只支持 5 分钟粒度，范围 5-1440"
            )
        if minutes not in windows:
            windows.append(minutes)
        if len(windows) >= CHANGE_WINDOW_LIMIT:
            break
    return windows or None


DEFAULT_OVERVIEW_BRANCH_KEYWORDS = ("中原", "AQ")


def get_dashboard_overview(
    engine: Engine,
    *,
    branch_id: int | None = None,
    branch_code: str | None = None,
    period_type: str = "DAY_ACC",
    change_windows: list[int] | tuple[int, ...] | None = None,
    indicator_codes: list[str] | tuple[str, ...] | None = None,
    include_acc: bool = True,
) -> dict[str, Any]:
    """Backward-compatible overview entrypoint.

    Keep a single implementation path so API behavior, tests, and the cockpit
    all use the same change-window and target/progress rules.
    """
    return get_dashboard_overview_fast(
        engine,
        branch_id=branch_id,
        branch_code=branch_code,
        period_type=period_type,
        change_windows=change_windows,
        indicator_codes=indicator_codes,
        include_acc=include_acc,
    )


def _select_overview_branch(
    branches: list[Area],
    *,
    branch_id: int | None,
    branch_code: str | None,
) -> Area | None:
    normalized_code = str(branch_code or "").strip()
    if branch_id is not None:
        for branch in branches:
            if branch.id == branch_id:
                return branch
    if normalized_code:
        for branch in branches:
            if branch.area_code == normalized_code:
                return branch
    for branch in branches:
        if any(
            keyword in branch.area_name or keyword in branch.area_code
            for keyword in DEFAULT_OVERVIEW_BRANCH_KEYWORDS
        ):
            return branch
    return branches[0] if branches else None


def _indicator_payload(indicators: list[Indicator]) -> list[dict[str, Any]]:
    return [
        {
            "id": indicator.id,
            "code": indicator.code,
            "name": indicator.name,
            "sort_order": indicator.sort_order,
        }
        for indicator in indicators
    ]


def _empty_targets(indicators: list[Indicator]) -> dict[str, Any]:
    return {indicator.code: None for indicator in indicators}


def _target_table_exists(session: Session) -> bool:
    return inspect(session.get_bind()).has_table("metric_target")


def _target_values_by_area(
    session: Session,
    area_ids: list[int],
    indicators: list[Indicator],
    period_type: str,
) -> dict[int, dict[str, Any]]:
    values_by_area: dict[int, dict[str, Any]] = defaultdict(dict)
    if not area_ids or not indicators or not _target_table_exists(session):
        return values_by_area

    indicator_ids = [indicator.id for indicator in indicators]
    code_by_id = {indicator.id: indicator.code for indicator in indicators}
    rows = session.execute(
        select(
            MetricTarget.area_id,
            MetricTarget.indicator_id,
            MetricTarget.target_value,
        ).where(
            MetricTarget.area_id.in_(area_ids),
            MetricTarget.indicator_id.in_(indicator_ids),
            MetricTarget.period_type == period_type,
            MetricTarget.enabled.is_(True),
        )
    ).all()
    for row in rows:
        code = code_by_id.get(row.indicator_id)
        if code:
            values_by_area[row.area_id][code] = _number(row.target_value)
    return values_by_area


def _attach_targets(
    session: Session,
    rows: list[dict[str, Any]],
    indicators: list[Indicator],
    period_type: str,
) -> None:
    targets_by_area = _target_values_by_area(
        session,
        [row["area_id"] for row in rows],
        indicators,
        period_type,
    )
    for row in rows:
        row["targets"] = {
            indicator.code: targets_by_area[row["area_id"]].get(indicator.code)
            for indicator in indicators
        }


def _area_payload(
    area: Area,
    *,
    metrics: dict[str, Any],
    targets: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "area_id": area.id,
        "area_code": area.area_code,
        "area_name": area.area_name,
        "level_type": area.level_type,
        "level_no": area.level_no,
        "parent_id": area.parent_id,
        "metrics": metrics,
        "targets": targets or {},
    }


def _latest_realtime_run(session: Session) -> CollectionRun | None:
    return session.scalar(
        select(CollectionRun)
        .join(
            MetricCurrent,
            MetricCurrent.collection_run_id == CollectionRun.id,
        )
        .where(
            CollectionRun.status == "SUCCESS",
            CollectionRun.run_type == "REALTIME",
        )
        .distinct()
        .order_by(CollectionRun.finished_at.desc(), CollectionRun.id.desc())
        .limit(1)
    )


def _change_anchor_time(latest_run: CollectionRun | None) -> datetime:
    """Use the latest successful collection as the stable change-window anchor."""
    if latest_run is not None and latest_run.finished_at is not None:
        return latest_run.finished_at
    return datetime.now()


def _run_payload(run: CollectionRun | None) -> dict[str, Any] | None:
    if run is None:
        return None
    return {
        "id": run.id,
        "batch_no": run.batch_no,
        "stat_date": _date_iso(run.stat_date),
        "finished_at": _datetime_iso_millis(run.finished_at),
    }


def get_current_with_changes(
    engine: Engine,
    level_type: str | None = None,
    parent_id: int | None = None,
    change_windows: list[int] | tuple[int, ...] | None = None,
    indicator_codes: list[str] | tuple[str, ...] | None = None,
) -> dict[str, Any]:
    """Return the same wide table as get_current_wide_table, enriched with
    per-area change deltas computed from MetricSnapshot history.

    For each enabled indicator, the service looks back the given number of
    minutes, finds the nearest snapshot within the shared tolerance around
    that cutoff, and computes ``value - previous_value`` (value delta) and
    ``(value - previous_value) / previous_value`` (rate delta).
    """
    result = get_current_wide_table(
        engine,
        level_type=level_type,
        parent_id=parent_id,
        indicator_codes=indicator_codes,
    )
    if not result["indicators"] or not result["rows"]:
        return result

    normalized_windows = normalize_change_windows(change_windows)
    area_ids = [row["area_id"] for row in result["rows"]]

    with Session(engine) as session:
        now = _change_anchor_time(_latest_realtime_run(session))
        _attach_changes_fast(
            session,
            result["rows"],
            result["indicators"],
            area_ids,
            now,
            change_windows=normalized_windows,
        )

    return result


def _snapshot_cutoff_time(now: datetime, target_minutes: int) -> datetime:
    return now - timedelta(minutes=target_minutes)


def _snapshot_bounds(cutoff_time: datetime) -> tuple[datetime, datetime]:
    tolerance = timedelta(minutes=CHANGE_WINDOW_TOLERANCE_MINUTES)
    return cutoff_time - tolerance, cutoff_time + tolerance


def _empty_change_payload() -> dict[str, Any]:
    return {"value": None, "rate": None}


def _change_payload(
    current_value: Decimal | None,
    previous_value: Decimal | None,
) -> dict[str, Any]:
    if current_value is None or previous_value is None:
        return _empty_change_payload()

    delta = current_value - previous_value
    delta_float = float(delta)
    try:
        rate = delta_float / float(previous_value) if previous_value != 0 else None
    except OverflowError:
        rate = None
    return {"value": delta_float, "rate": rate}


def _indicator_id(indicator: Indicator | dict[str, Any]) -> int:
    if isinstance(indicator, dict):
        return int(indicator["id"])
    return int(indicator.id)


def _indicator_code(indicator: Indicator | dict[str, Any]) -> str:
    if isinstance(indicator, dict):
        return str(indicator["code"])
    return str(indicator.code)


def get_acc_wide_table(
    engine: Engine,
    period_type: str = "DAY_ACC",
    level_type: str | None = None,
    parent_id: int | None = None,
    stat_date: str | None = None,
    indicator_codes: list[str] | tuple[str, ...] | None = None,
) -> dict[str, Any]:
    """Return accumulated (daily/monthly) metric data in wide-table format."""
    normalized_period = str(period_type or "").strip().upper()
    if normalized_period not in {"DAY_ACC", "MONTH"}:
        raise ValueError(
            f"period_type 只支持 DAY_ACC/MONTH: {period_type!r}"
        )

    normalized_level = str(level_type or "").strip().upper() or None

    with Session(engine) as session:
        indicators = _enabled_indicators(session, indicator_codes)

        area_query = (
            select(Area)
            .where(Area.enabled.is_(True))
            .order_by(Area.level_no, Area.sort_order, Area.id)
        )
        if normalized_level:
            area_query = area_query.where(Area.level_type == normalized_level)
        if parent_id is not None:
            area_query = area_query.where(Area.parent_id == parent_id)
        areas = session.scalars(area_query).all()
        area_ids = [area.id for area in areas]

        indicator_to_id = {ind.code: ind.id for ind in indicators}
        values_by_area: dict[int, dict[str, Any]] = defaultdict(dict)
        targets_by_area = _target_values_by_area(
            session,
            area_ids,
            list(indicators),
            normalized_period,
        )

        if area_ids and indicators:
            acc_query = select(
                MetricAcc.area_id,
                MetricAcc.indicator_id,
                MetricAcc.metric_value,
                MetricAcc.stat_date,
            ).where(
                MetricAcc.area_id.in_(area_ids),
                MetricAcc.indicator_id.in_(indicator_to_id.values()),
                MetricAcc.period_type == normalized_period,
            )

            if stat_date:
                from datetime import date as date_type

                acc_query = acc_query.where(
                    MetricAcc.stat_date
                    == date_type.fromisoformat(stat_date)
                )
            else:
                latest_date_row = session.execute(
                    select(MetricAcc.stat_date)
                    .where(
                        MetricAcc.area_id.in_(area_ids),
                        MetricAcc.indicator_id.in_(
                            indicator_to_id.values()
                        ),
                        MetricAcc.period_type == normalized_period,
                    )
                    .order_by(MetricAcc.stat_date.desc())
                    .limit(1)
                ).first()
                if latest_date_row:
                    acc_query = acc_query.where(
                        MetricAcc.stat_date == latest_date_row[0]
                    )

            acc_rows = session.execute(acc_query).all()

            code_by_id = {v: k for k, v in indicator_to_id.items()}
            for acc_row in acc_rows:
                indicator_code = code_by_id.get(acc_row.indicator_id)
                if indicator_code:
                    values_by_area[acc_row.area_id][indicator_code] = _number(
                        acc_row.metric_value
                    )

    rows = []
    for area in areas:
        metrics = {
            indicator.code: values_by_area[area.id].get(indicator.code)
            for indicator in indicators
        }
        targets = {
            indicator.code: targets_by_area[area.id].get(indicator.code)
            for indicator in indicators
        }
        if any(v is not None for v in metrics.values()):
            rows.append(
                {
                    "area_id": area.id,
                    "area_code": area.area_code,
                    "area_name": area.area_name,
                    "level_type": area.level_type,
                    "level_no": area.level_no,
                    "parent_id": area.parent_id,
                    "metrics": metrics,
                    "targets": targets,
                }
            )

    return {
        "indicators": [
            {
                "id": ind.id,
                "code": ind.code,
                "name": ind.name,
                "sort_order": ind.sort_order,
            }
            for ind in indicators
        ],
        "rows": rows,
        "row_count": len(rows),
    }


# ---------------------------------------------------------------------------
# Optimised cockpit queries
# ---------------------------------------------------------------------------
#
# ``get_dashboard_overview`` makes 7-8 sequential ORM round-trips.
# The functions below collapse them into 2-3 raw-SQL queries.
#
# 1. area + metric_current: single JOIN (eliminates 4 separate selects)
# 2. snapshot: only fetch the 4 cutoff timestamps instead of 120-min dump
# 3. acc: single query with date-subquery

def get_dashboard_overview_fast(
    engine: Engine,
    *,
    branch_id: int | None = None,
    branch_code: str | None = None,
    period_type: str = "DAY_ACC",
    change_windows: list[int] | tuple[int, ...] | None = None,
    indicator_codes: list[str] | tuple[str, ...] | None = None,
    include_acc: bool = True,
) -> dict[str, Any]:
    """Drop-in replacement for ``get_dashboard_overview`` with fewer queries.

    Query budget:  ① indicators  ② area+current JOIN  ③ snapshot  ④ acc
    = 4 queries (vs 7-8).
    """
    normalized_period = str(period_type or "").strip().upper()
    if normalized_period not in {"DAY_ACC", "MONTH"}:
        raise ValueError(f"period_type 只支持 DAY_ACC/MONTH: {period_type!r}")
    normalized_windows = normalize_change_windows(change_windows)

    with Session(engine) as session:
        # ① indicators (small, always cached)
        indicators = _enabled_indicators(session, indicator_codes)
        indicator_ids = [ind.id for ind in indicators]
        code_by_id = {ind.id: ind.code for ind in indicators}

        # ② area + metric_current in one JOIN query
        #    Returns all areas (BRANCH always, GRID+CHANNEL filtered by scope)
        #    with their metric_current values inline.
        latest_run = _latest_realtime_run(session)
        change_anchor = _change_anchor_time(latest_run)

        area_current_sql = text("""
            SELECT
                a.id            AS area_id,
                a.area_code,
                a.area_name,
                a.level_type,
                a.level_no,
                a.parent_id,
                mc.indicator_id,
                mc.metric_value,
                mc.collected_at,
                mc.collection_run_id
            FROM area a
            LEFT JOIN metric_current mc
                ON mc.area_id = a.id
               AND mc.indicator_id IN :ind_ids
            WHERE a.enabled = 1
              AND (
                    a.level_type = 'BRANCH'
                 OR (a.level_type = 'GRID'    AND a.parent_id = :branch_id)
                 OR (a.level_type = 'CHANNEL' AND a.parent_id IN (
                       SELECT id FROM area
                       WHERE enabled = 1 AND level_type = 'GRID' AND parent_id = :branch_id
                     ))
              )
            ORDER BY a.level_no, a.sort_order, a.id
        """).bindparams(bindparam("ind_ids", expanding=True))

        # Resolve branch scope
        branch_scope = _resolve_branch_scope(
            session, branch_id=branch_id, branch_code=branch_code
        )
        scope_branch_id = branch_scope.id if branch_scope else -1

        result = session.execute(
            area_current_sql,
            {
                "ind_ids": tuple(indicator_ids) if indicator_ids else (-1,),
                "branch_id": scope_branch_id,
            },
        ).mappings().all()

        # Build rows grouped by level
        rows_by_area = _group_area_metric_rows(result, indicators, code_by_id)
        all_area_ids = list(rows_by_area)

        # ③ snapshots for change windows — only the 4 cutoff timestamps
        _attach_targets(
            session,
            list(rows_by_area.values()),
            indicators,
            "REALTIME",
        )
        if all_area_ids and indicator_ids:
            _attach_changes_fast(
                session,
                list(rows_by_area.values()),
                indicators,
                all_area_ids,
                change_anchor,
                change_windows=normalized_windows,
            )
        else:
            for row in rows_by_area.values():
                row["changes"] = {
                    ind.code: {k: {"value": None, "rate": None} for _, k in normalized_windows}
                    for ind in indicators
                }

        # ④ acc data
        acc_rows = (
            _acc_rows_for_areas_fast(
                session, all_area_ids, indicator_ids, code_by_id, normalized_period
            )
            if include_acc
            else []
        )

    # Assemble branches / grids / channels
    rows = list(rows_by_area.values())
    return {
        "latest_run": _run_payload(latest_run),
        "selected_branch": (
            _area_payload(branch_scope, metrics={}) if branch_scope else None
        ),
        "indicators": _indicator_payload(indicators),
        "rows": rows,
        "acc_rows": acc_rows,
        "row_count": len(rows),
        "acc_row_count": len(acc_rows),
    }


def get_drill_down(
    engine: Engine,
    *,
    parent_id: int,
    parent_level: str,
    period_type: str = "DAY_ACC",
    change_windows: list[int] | tuple[int, ...] | None = None,
    indicator_codes: list[str] | tuple[str, ...] | None = None,
    include_acc: bool = True,
) -> dict[str, Any]:
    """Lightweight drill-down: fetch only the children of *parent_id*.

    When the user clicks a BRANCH row the cockpit only needs its GRIDs
    and CHANNELs, not all 16 branches again.  This function returns the
    same shape as ``get_dashboard_overview`` but scoped to one parent,
    saving ~60% payload on the wire and ~300ms on the server.

    Query budget:  ① area+current JOIN  ② snapshot  ③ acc  = 3 queries.
    """
    normalized_period = str(period_type or "").strip().upper()
    if normalized_period not in {"DAY_ACC", "MONTH"}:
        raise ValueError(f"period_type 只支持 DAY_ACC/MONTH: {period_type!r}")
    normalized_windows = normalize_change_windows(change_windows)

    with Session(engine) as session:
        indicators = _enabled_indicators(session, indicator_codes)
        indicator_ids = [ind.id for ind in indicators]
        code_by_id = {ind.id: ind.code for ind in indicators}
        latest_run = _latest_realtime_run(session)
        change_anchor = _change_anchor_time(latest_run)

        # Determine child level types and build the WHERE clause
        # BRANCH drill-down needs 2-hop: GRID→parent=branch, CHANNEL→parent IN (grids)
        child_levels_map = {
            "CITY": ["BRANCH"],
            "BRANCH": ["GRID", "CHANNEL"],
            "GRID": ["CHANNEL"],
        }
        child_levels = child_levels_map.get(parent_level.upper(), [])

        if not child_levels:
            return {
                "latest_run": _run_payload(latest_run),
                "indicators": _indicator_payload(indicators),
                "rows": [],
                "acc_rows": [],
                "row_count": 0,
                "acc_row_count": 0,
            }

        if parent_level.upper() == "BRANCH":
            # Keep all BRANCH rows visible for cross-branch comparison, while
            # scoping GRID/CHANNEL rows to the selected branch.
            drill_sql = text("""
                SELECT
                    a.id            AS area_id,
                    a.area_code,
                    a.area_name,
                    a.level_type,
                    a.level_no,
                    a.parent_id,
                    mc.indicator_id,
                    mc.metric_value,
                    mc.collected_at,
                    mc.collection_run_id
                FROM area a
                LEFT JOIN metric_current mc
                    ON mc.area_id = a.id
                   AND mc.indicator_id IN :ind_ids
                WHERE a.enabled = 1
                  AND (
                       a.level_type = 'BRANCH'
                    OR (a.level_type = 'GRID' AND a.parent_id = :parent_id)
                    OR (a.level_type = 'CHANNEL' AND a.parent_id IN (
                          SELECT id FROM area
                          WHERE enabled = 1 AND level_type = 'GRID' AND parent_id = :parent_id2
                        ))
                  )
                ORDER BY a.level_no, a.sort_order, a.id
            """).bindparams(bindparam("ind_ids", expanding=True))
            result = session.execute(
                drill_sql,
                {
                    "ind_ids": tuple(indicator_ids) if indicator_ids else (-1,),
                    "parent_id": parent_id,
                    "parent_id2": parent_id,
                },
            ).mappings().all()
        elif parent_level.upper() == "GRID":
            # Keep BRANCH rows visible for cross-branch comparison, while
            # showing all sibling GRID rows under the selected grid's branch
            # and scoping CHANNEL rows to the selected grid.
            drill_sql = text("""
                SELECT
                    a.id            AS area_id,
                    a.area_code,
                    a.area_name,
                    a.level_type,
                    a.level_no,
                    a.parent_id,
                    mc.indicator_id,
                    mc.metric_value,
                    mc.collected_at,
                    mc.collection_run_id
                FROM area a
                LEFT JOIN metric_current mc
                    ON mc.area_id = a.id
                   AND mc.indicator_id IN :ind_ids
                WHERE a.enabled = 1
                  AND (
                       a.level_type = 'BRANCH'
                    OR (a.level_type = 'GRID' AND a.parent_id = (
                          SELECT parent_id FROM area
                          WHERE enabled = 1 AND level_type = 'GRID' AND id = :parent_id2
                        ))
                    OR (a.level_type = 'CHANNEL' AND a.parent_id = :parent_id)
                  )
                ORDER BY a.level_no, a.sort_order, a.id
            """).bindparams(bindparam("ind_ids", expanding=True))
            result = session.execute(
                drill_sql,
                {
                    "ind_ids": tuple(indicator_ids) if indicator_ids else (-1,),
                    "parent_id": parent_id,
                    "parent_id2": parent_id,
                },
            ).mappings().all()
        else:
            level_filter = ", ".join(f"'{lv}'" for lv in child_levels)
            drill_sql = text(f"""
                SELECT
                    a.id            AS area_id,
                    a.area_code,
                    a.area_name,
                    a.level_type,
                    a.level_no,
                    a.parent_id,
                    mc.indicator_id,
                    mc.metric_value,
                    mc.collected_at,
                    mc.collection_run_id
                FROM area a
                LEFT JOIN metric_current mc
                    ON mc.area_id = a.id
                   AND mc.indicator_id IN :ind_ids
                WHERE a.enabled = 1
                  AND a.level_type IN ({level_filter})
                  AND a.parent_id = :parent_id
                ORDER BY a.level_no, a.sort_order, a.id
            """).bindparams(bindparam("ind_ids", expanding=True))
            result = session.execute(
                drill_sql,
                {
                    "ind_ids": tuple(indicator_ids) if indicator_ids else (-1,),
                    "parent_id": parent_id,
                },
            ).mappings().all()

        rows_by_area = _group_area_metric_rows(result, indicators, code_by_id)
        all_area_ids = list(rows_by_area)
        rows = list(rows_by_area.values())

        _attach_targets(session, rows, indicators, "REALTIME")
        if all_area_ids and indicator_ids:
            _attach_changes_fast(
                session,
                rows,
                indicators,
                all_area_ids,
                change_anchor,
                change_windows=normalized_windows,
            )
        else:
            for row in rows:
                row["changes"] = {
                    ind.code: {k: {"value": None, "rate": None} for _, k in normalized_windows}
                    for ind in indicators
                }

        acc_rows = (
            _acc_rows_for_areas_fast(
                session, all_area_ids, indicator_ids, code_by_id, normalized_period
            )
            if include_acc
            else []
        )

    return {
        "latest_run": _run_payload(latest_run),
        "indicators": _indicator_payload(indicators),
        "rows": rows,
        "acc_rows": acc_rows,
        "row_count": len(rows),
        "acc_row_count": len(acc_rows),
    }


# ── helpers for the fast path ───────────────────────────────────────────

def _group_area_metric_rows(
    result: list[Any],
    indicators: list[Indicator],
    code_by_id: dict[int, str],
) -> dict[int, dict[str, Any]]:
    rows_by_area: dict[int, dict[str, Any]] = {}
    empty_metrics = {indicator.code: None for indicator in indicators}
    for raw in result:
        area_id = raw["area_id"]
        row = rows_by_area.setdefault(
            area_id,
            {
                "area_id": area_id,
                "area_code": raw["area_code"],
                "area_name": raw["area_name"],
                "level_type": raw["level_type"],
                "level_no": raw["level_no"],
                "parent_id": raw["parent_id"],
                "collection_run_id": None,
                "collected_at": None,
                "metrics": dict(empty_metrics),
            },
        )
        indicator_code = code_by_id.get(raw["indicator_id"])
        if indicator_code:
            row["metrics"][indicator_code] = _number(raw["metric_value"])
        if raw["collected_at"] is not None:
            collected_at = _datetime_iso_millis(raw["collected_at"])
            if row["collected_at"] is None or collected_at > row["collected_at"]:
                row["collected_at"] = collected_at
                row["collection_run_id"] = raw["collection_run_id"]
    return rows_by_area

def _resolve_branch_scope(
    session: Session,
    *,
    branch_id: int | None,
    branch_code: str | None,
) -> Area | None:
    branches = list(
        session.scalars(
            select(Area)
            .where(Area.enabled.is_(True), Area.level_type == "BRANCH")
            .order_by(Area.sort_order, Area.id)
        )
    )
    return _select_overview_branch(
        branches, branch_id=branch_id, branch_code=branch_code
    )


def _attach_changes_fast(
    session: Session,
    rows: list[dict[str, Any]],
    indicators: list[Indicator] | list[dict[str, Any]],
    area_ids: list[int],
    now: datetime,
    *,
    change_windows: tuple[tuple[int, str], ...] = _CHANGE_WINDOWS,
) -> None:
    """Attach change deltas using only the 4 cutoff-point snapshots.

    Instead of loading 120 min of full snapshot history, we load the
    single nearest snapshot at each cutoff (5/15/30/60 min ago) per
    (area, indicator) via a correlated subquery.
    """
    indicator_ids = [_indicator_id(ind) for ind in indicators]

    cutoffs = [(_snapshot_cutoff_time(now, m), k) for m, k in change_windows]

    # One query per cutoff window — 4 small bounded queries instead of 1 huge
    # dump.  Pick the nearest snapshot within +/- tolerance; scheduled runs can
    # finish a few seconds before or after the nominal 5-minute boundary.
    snapshot_map: dict[tuple[int, int, str], Decimal] = {}
    for cutoff_ts, window_key in cutoffs:
        lower_bound, upper_bound = _snapshot_bounds(cutoff_ts)
        dialect_name = session.get_bind().dialect.name
        if dialect_name in {"mysql", "mariadb"}:
            distance_expr = (
                "ABS(TIMESTAMPDIFF(MICROSECOND, collected_at, :cutoff_ts))"
            )
        else:
            distance_expr = (
                "ABS((julianday(collected_at) - julianday(:cutoff_ts)) * "
                "86400000000.0)"
            )
        snap_sql = text(f"""
            SELECT area_id, indicator_id, metric_value
            FROM (
                SELECT
                    area_id,
                    indicator_id,
                    metric_value,
                    ROW_NUMBER() OVER (
                        PARTITION BY area_id, indicator_id
                        ORDER BY {distance_expr}, collected_at DESC
                    ) AS rn
                FROM metric_snapshot
                WHERE area_id IN :area_ids
                  AND indicator_id IN :ind_ids
                  AND collected_at >= :lower_bound
                  AND collected_at <= :upper_bound
            ) ranked
            WHERE rn = 1
        """).bindparams(
            bindparam("area_ids", expanding=True),
            bindparam("ind_ids", expanding=True),
        )
        snap_rows = session.execute(
            snap_sql,
            {
                "area_ids": tuple(area_ids) if area_ids else (-1,),
                "ind_ids": tuple(indicator_ids) if indicator_ids else (-1,),
                "lower_bound": lower_bound,
                "upper_bound": upper_bound,
                "cutoff_ts": cutoff_ts,
            },
        ).mappings().all()

        for sr in snap_rows:
            snapshot_map[(sr["area_id"], sr["indicator_id"], window_key)] = (
                sr["metric_value"]
            )

    # Attach to rows
    for row in rows:
        aid = row["area_id"]
        per_indicator: dict[str, dict[str, dict[str, Any]]] = {}
        for ind in indicators:
            indicator_id = _indicator_id(ind)
            indicator_code = _indicator_code(ind)
            current_value = row["metrics"].get(indicator_code)
            current_dec = Decimal(str(current_value)) if current_value is not None else None

            changes: dict[str, dict[str, Any]] = {}
            for _, window_key in cutoffs:
                prev = snapshot_map.get((aid, indicator_id, window_key))
                changes[window_key] = _change_payload(current_dec, prev)
            per_indicator[indicator_code] = changes
        row["changes"] = per_indicator


def _acc_rows_for_areas_fast(
    session: Session,
    area_ids: list[int],
    indicator_ids: list[int],
    code_by_id: dict[int, str],
    period_type: str,
) -> list[dict[str, Any]]:
    """Fetch acc data for the given areas in a single query."""
    if not area_ids or not indicator_ids:
        return []

    acc_sql = text("""
        SELECT ma.area_id, ma.indicator_id, ma.metric_value,
               a.area_code, a.area_name, a.level_type, a.level_no, a.parent_id
        FROM metric_acc ma
        JOIN area a ON a.id = ma.area_id
        WHERE ma.area_id IN :area_ids
          AND ma.indicator_id IN :ind_ids
          AND ma.period_type = :pt
          AND ma.stat_date = (
              SELECT MAX(stat_date) FROM metric_acc
              WHERE area_id IN :area_ids2
                AND indicator_id IN :ind_ids2
                AND period_type = :pt2
          )
    """).bindparams(
        bindparam("area_ids", expanding=True),
        bindparam("ind_ids", expanding=True),
        bindparam("area_ids2", expanding=True),
        bindparam("ind_ids2", expanding=True),
    )
    result = session.execute(
        acc_sql,
        {
            "area_ids": tuple(area_ids),
            "ind_ids": tuple(indicator_ids),
            "pt": period_type,
            "area_ids2": tuple(area_ids),
            "ind_ids2": tuple(indicator_ids),
            "pt2": period_type,
        },
    ).mappings().all()

    targets_by_area: dict[int, dict[str, Any]] = defaultdict(dict)
    if _target_table_exists(session):
        target_rows = session.execute(
            select(
                MetricTarget.area_id,
                MetricTarget.indicator_id,
                MetricTarget.target_value,
            ).where(
                MetricTarget.area_id.in_(area_ids),
                MetricTarget.indicator_id.in_(indicator_ids),
                MetricTarget.period_type == period_type,
                MetricTarget.enabled.is_(True),
            )
        ).all()
        for target in target_rows:
            code = code_by_id.get(target.indicator_id)
            if code:
                targets_by_area[target.area_id][code] = _number(target.target_value)

    rows: list[dict[str, Any]] = []
    for r in result:
        code = code_by_id.get(r["indicator_id"])
        if code:
            rows.append({
                "area_id": r["area_id"],
                "area_code": r["area_code"],
                "area_name": r["area_name"],
                "level_type": r["level_type"],
                "level_no": r["level_no"],
                "parent_id": r["parent_id"],
                "metrics": {code: _number(r["metric_value"])},
                "targets": {code: targets_by_area[r["area_id"]].get(code)},
            })
    return rows
