"""Dashboard V2 read service.

All public node DTOs use the V2 hierarchy vocabulary directly.  Historical
queries reconstruct sparse snapshots by selecting the last value at or before
the requested time; they never fall back to ``metric_current``.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from hashlib import sha256
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from sqlalchemy import Engine, bindparam, func, or_, select, text
from sqlalchemy.orm import Session, aliased

from models.dashboard_v2 import (
    CollectionRunV2,
    HierarchyNode,
    HierarchyParentHistory,
    IndicatorFormulaComponent,
    IndicatorV2,
    MetricAccV2,
    MetricCurrentV2,
    MetricSnapshotV2,
    TargetPlan,
)
from services.dashboard_query_windows import (
    CHANGE_WINDOW_TOLERANCE_MINUTES,
    SPARSE_SNAPSHOT_BASELINE_LOOKBACK_DAYS,
    parse_change_window_minutes,
)
from services.dashboard_v2_target_service import load_v2_target_values, normalize_target_scenario


NODE_TYPES = {"CITY", "BRANCH", "GRID", "CHANNEL_MANAGER", "CHANNEL"}
VALUE_MODES = {"REALTIME", "REALTIME_ACC"}
_SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")


def _yesterday_shanghai() -> date:
    return datetime.now(_SHANGHAI_TZ).date() - timedelta(days=1)


def _normalize_value_mode(value: str | None) -> str:
    normalized = str(value or "REALTIME").strip().upper()
    if normalized not in VALUE_MODES:
        raise ValueError(f"value_mode 只支持 REALTIME/REALTIME_ACC: {value!r}")
    return normalized


def _number(value: Decimal | int | float | None) -> float | int | None:
    if value is None:
        return None
    if isinstance(value, int | float):
        return value
    if value == value.to_integral_value():
        return int(value)
    return float(value)


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat(timespec="milliseconds")
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _indicator_dto(indicator: IndicatorV2) -> dict[str, Any]:
    return {
        "id": indicator.id,
        "code": indicator.code,
        "name": indicator.name,
        "sort_order": indicator.sort_order,
    }


def _enabled_indicators(
    session: Session,
    indicator_codes: Iterable[str] | None = None,
) -> list[IndicatorV2]:
    stmt = select(IndicatorV2).where(
        IndicatorV2.enabled.is_(True),
        IndicatorV2.storage_mode == "STORE",
    )
    if indicator_codes:
        stmt = stmt.where(IndicatorV2.code.in_(list(indicator_codes)))
    return list(session.scalars(stmt.order_by(IndicatorV2.sort_order, IndicatorV2.id)))


def _components(
    session: Session,
    indicators: list[IndicatorV2],
) -> tuple[dict[str, list[tuple[str, Decimal]]], list[IndicatorV2]]:
    custom_ids = [row.id for row in indicators if row.indicator_type == "CUSTOM"]
    if not custom_ids:
        return {}, indicators
    source = aliased(IndicatorV2)
    rows = session.execute(
        select(
            IndicatorFormulaComponent.custom_indicator_id,
            source.code,
            IndicatorFormulaComponent.coefficient,
        )
        .join(source, source.id == IndicatorFormulaComponent.source_indicator_id)
        .where(IndicatorFormulaComponent.custom_indicator_id.in_(custom_ids))
        .order_by(IndicatorFormulaComponent.sort_order, IndicatorFormulaComponent.id)
    ).all()
    code_by_id = {row.id: row.code for row in indicators}
    result: dict[str, list[tuple[str, Decimal]]] = defaultdict(list)
    source_codes: set[str] = set()
    for custom_id, source_code, coefficient in rows:
        custom_code = code_by_id.get(custom_id)
        if custom_code:
            result[custom_code].append((source_code, Decimal(str(coefficient))))
            source_codes.add(source_code)
    physical = list(indicators)
    known = {row.code for row in physical}
    if source_codes - known:
        physical.extend(
            session.scalars(
                select(IndicatorV2).where(IndicatorV2.code.in_(source_codes - known))
            ).all()
        )
    return dict(result), physical


def _weighted_sum(
    values: dict[str, Any], components: list[tuple[str, Decimal]]
) -> float | int | None:
    total = Decimal("0")
    found = False
    for source_code, coefficient in components:
        value = values.get(source_code)
        if value is not None:
            total += Decimal(str(value)) * coefficient
            found = True
    if not found:
        return None
    return _number(total.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP))


def _apply_custom(
    rows: list[dict[str, Any]],
    indicators: list[IndicatorV2],
    components: dict[str, list[tuple[str, Decimal]]],
) -> None:
    response_codes = [row.code for row in indicators]
    for row in rows:
        metrics = row["metrics"]
        targets = row.get("targets")
        for code, parts in components.items():
            if metrics.get(code) is None:
                metrics[code] = _weighted_sum(metrics, parts)
            if isinstance(targets, dict) and targets.get(code) is None:
                targets[code] = _weighted_sum(targets, parts)
        row["metrics"] = {code: metrics.get(code) for code in response_codes}
        if isinstance(targets, dict):
            row["targets"] = {code: targets.get(code) for code in response_codes}


def _latest_run(session: Session, *, as_of: datetime | None = None) -> CollectionRunV2 | None:
    stmt = select(CollectionRunV2).where(
        CollectionRunV2.run_type == "REALTIME",
        CollectionRunV2.status == "SUCCESS",
    )
    if as_of is not None:
        stmt = stmt.where(CollectionRunV2.started_at <= as_of)
    return session.scalar(
        stmt.order_by(CollectionRunV2.started_at.desc(), CollectionRunV2.id.desc()).limit(1)
    )


def _historical_run(session: Session, as_of: datetime) -> CollectionRunV2 | None:
    """Resolve the completed historical batch using the V1 rules."""
    batch_time = func.coalesce(CollectionRunV2.started_at, CollectionRunV2.finished_at)
    return session.scalar(
        select(CollectionRunV2)
        .where(
            CollectionRunV2.run_type == "REALTIME",
            CollectionRunV2.status == "SUCCESS",
            CollectionRunV2.finished_at.is_not(None),
            batch_time <= as_of,
            select(MetricSnapshotV2.id)
            .where(MetricSnapshotV2.collected_at <= CollectionRunV2.finished_at)
            .exists(),
        )
        .order_by(batch_time.desc(), CollectionRunV2.id.desc())
        .limit(1)
    )


def _run_dto(run: CollectionRunV2 | None, *, full: bool = True) -> dict[str, Any] | None:
    if run is None:
        return None
    result = {
        "id": run.id,
        "batch_no": run.batch_no,
        "stat_date": _iso(run.stat_date),
        "finished_at": _iso(run.finished_at),
    }
    if full:
        result["started_at"] = _iso(run.started_at)
    return result


def _normalized_node_type(node_type: str | None) -> str | None:
    value = str(node_type or "").strip().upper() or None
    if value is not None and value not in NODE_TYPES:
        raise ValueError(f"node_type 不受支持: {node_type!r}")
    return value


def _historical_parent_map(session: Session, as_of: datetime) -> dict[int, int]:
    rows = session.execute(
        select(
            HierarchyParentHistory.child_node_id,
            HierarchyParentHistory.parent_node_id,
        ).where(
            HierarchyParentHistory.valid_from <= as_of,
            or_(
                HierarchyParentHistory.valid_to.is_(None),
                HierarchyParentHistory.valid_to > as_of,
            ),
        )
    ).all()
    return {row.child_node_id: row.parent_node_id for row in rows}


def _nodes(
    session: Session,
    *,
    node_type: str | None = None,
    parent_id: int | None = None,
    as_of: datetime | None = None,
    include_disabled: bool = False,
) -> list[HierarchyNode]:
    normalized = _normalized_node_type(node_type)
    stmt = select(HierarchyNode)
    if not include_disabled:
        stmt = stmt.where(HierarchyNode.enabled.is_(True))
    if normalized:
        stmt = stmt.where(HierarchyNode.node_type == normalized)
    result = list(session.scalars(stmt.order_by(HierarchyNode.level_no, HierarchyNode.sort_order, HierarchyNode.id)))
    if parent_id is None:
        return result
    if as_of is None:
        return [row for row in result if row.parent_id == parent_id]
    parents = _historical_parent_map(session, as_of)
    return [row for row in result if parents.get(row.id, row.parent_id) == parent_id]


def _drill_context_nodes(
    session: Session,
    *,
    parent_id: int,
    parent_node_type: str,
    as_of: datetime | None = None,
) -> list[HierarchyNode]:
    """Return ancestor comparisons and the complete descendant drill scope."""
    parent_type = _normalized_node_type(parent_node_type)
    all_nodes = _nodes(session, as_of=as_of)
    node_by_id = {row.id: row for row in all_nodes}
    historical_parents = _historical_parent_map(session, as_of) if as_of else {}

    def effective_parent(node: HierarchyNode) -> int | None:
        return historical_parents.get(node.id, node.parent_id)

    parent = node_by_id.get(parent_id)
    if parent is None or parent.node_type != parent_type:
        return []

    branch_id: int | None = None
    grid_id: int | None = None
    manager_id: int | None = None
    city_id: int | None = parent.id if parent_type == "CITY" else None
    if parent_type == "BRANCH":
        branch_id = parent.id
        city_id = effective_parent(parent)
    elif parent_type == "GRID":
        branch_id = effective_parent(parent)
        grid_id = parent.id
        branch = node_by_id.get(branch_id) if branch_id is not None else None
        city_id = effective_parent(branch) if branch is not None else None
    elif parent_type == "CHANNEL_MANAGER":
        grid_id = effective_parent(parent)
        grid = node_by_id.get(grid_id) if grid_id is not None else None
        branch_id = effective_parent(grid) if grid is not None else None
        branch = node_by_id.get(branch_id) if branch_id is not None else None
        city_id = effective_parent(branch) if branch is not None else None
        manager_id = parent.id

    branch_ids = {
        row.id for row in all_nodes
        if row.node_type == "BRANCH" and effective_parent(row) == city_id
    }
    grid_ids = {
        row.id for row in all_nodes
        if row.node_type == "GRID" and branch_id is not None
        and effective_parent(row) == branch_id
    }
    manager_parent_ids = (
        grid_ids if parent_type == "BRANCH"
        else {grid_id} if grid_id is not None
        else set()
    )
    manager_ids = {
        row.id for row in all_nodes
        if row.node_type == "CHANNEL_MANAGER"
        and effective_parent(row) in manager_parent_ids
    }
    channel_parent_ids = (
        {manager_id}
        if parent_type == "CHANNEL_MANAGER" and manager_id is not None
        else manager_ids
    )
    return [
        row for row in all_nodes
        if (
            (row.node_type == "BRANCH" and row.id in branch_ids)
            or (row.node_type == "GRID" and row.id in grid_ids)
            or (row.node_type == "CHANNEL_MANAGER" and row.id in manager_ids)
            or (
                row.node_type == "CHANNEL"
                and effective_parent(row) in channel_parent_ids
            )
        )
    ]


def _node_dto(node: HierarchyNode, *, parent_id: int | None = None) -> dict[str, Any]:
    return {
        "id": node.id,
        "node_code": node.node_code,
        "node_name": node.node_name,
        "node_type": node.node_type,
        "level_no": node.level_no,
        "parent_id": node.parent_id if parent_id is None else parent_id,
    }


def _descendant_nodes(
    session: Session,
    root_id: int,
    *,
    as_of: datetime | None = None,
    include_root: bool = False,
) -> list[HierarchyNode]:
    nodes = _nodes(session, as_of=as_of)
    parents = _historical_parent_map(session, as_of) if as_of else {}
    children: dict[int, list[HierarchyNode]] = defaultdict(list)
    by_id = {row.id: row for row in nodes}
    for node in nodes:
        parent_id = parents.get(node.id, node.parent_id)
        if parent_id is not None:
            children[parent_id].append(node)
    result: list[HierarchyNode] = [by_id[root_id]] if include_root and root_id in by_id else []
    pending = list(children.get(root_id, []))
    while pending:
        node = pending.pop(0)
        result.append(node)
        pending.extend(children.get(node.id, []))
    return result


def _scoped_nodes(
    session: Session,
    *,
    node_type: str,
    scope_mode: str,
    parent_id: int | None,
    branch_code: str | None,
    as_of: datetime | None = None,
) -> list[HierarchyNode]:
    if parent_id is not None:
        return _nodes(
            session,
            node_type=node_type,
            parent_id=parent_id,
            as_of=as_of,
        )
    candidates = _nodes(session, node_type=node_type, as_of=as_of)
    if str(scope_mode or "").strip().lower() == "all" or node_type == "BRANCH":
        return candidates
    branch = session.scalar(
        select(HierarchyNode)
        .where(
            HierarchyNode.node_type == "BRANCH",
            HierarchyNode.enabled.is_(True),
            HierarchyNode.node_code == (branch_code or "AQ"),
        )
        .limit(1)
    )
    if branch is None:
        return candidates
    allowed = {row.id for row in _descendant_nodes(session, branch.id, as_of=as_of)}
    return [row for row in candidates if row.id in allowed]


def _value_rows_at(
    session: Session,
    *,
    node_ids: list[int],
    indicator_ids: list[int],
    as_of: datetime | None,
    current_stat_date: date | None = None,
) -> list[Any]:
    if not node_ids or not indicator_ids:
        return []
    if as_of is None:
        stmt = select(
            MetricCurrentV2.node_id,
            MetricCurrentV2.indicator_id,
            MetricCurrentV2.metric_value,
            MetricCurrentV2.collection_run_id,
            MetricCurrentV2.collected_at,
        ).where(
            MetricCurrentV2.node_id.in_(node_ids),
            MetricCurrentV2.indicator_id.in_(indicator_ids),
        )
        if current_stat_date is not None:
            stmt = stmt.where(MetricCurrentV2.stat_date == current_stat_date)
        return session.execute(stmt).all()
    ranked = (
        select(
            MetricSnapshotV2.node_id.label("node_id"),
            MetricSnapshotV2.indicator_id.label("indicator_id"),
            MetricSnapshotV2.metric_value.label("metric_value"),
            MetricSnapshotV2.collection_run_id.label("collection_run_id"),
            MetricSnapshotV2.collected_at.label("collected_at"),
            func.row_number().over(
                partition_by=(MetricSnapshotV2.node_id, MetricSnapshotV2.indicator_id),
                order_by=(MetricSnapshotV2.collected_at.desc(), MetricSnapshotV2.id.desc()),
            ).label("rn"),
        )
        .where(
            MetricSnapshotV2.node_id.in_(node_ids),
            MetricSnapshotV2.indicator_id.in_(indicator_ids),
            MetricSnapshotV2.collected_at <= as_of,
        )
        .subquery()
    )
    return session.execute(
        select(
            ranked.c.node_id,
            ranked.c.indicator_id,
            ranked.c.metric_value,
            ranked.c.collection_run_id,
            ranked.c.collected_at,
        ).where(ranked.c.rn == 1)
    ).all()


def _target_period(period_type: str) -> str:
    normalized = str(period_type or "").strip().upper()
    if normalized in {"DAY", "DAY_ACC"}:
        return "DAY"
    if normalized == "MONTH":
        return "MONTH"
    raise ValueError(f"period_type 只支持 DAY_ACC/MONTH: {period_type!r}")


def _active_target_map(
    session: Session,
    *,
    node_ids: list[int],
    indicator_ids: list[int],
    period_type: str,
    target_date: date,
    target_scenario: str = "NORMAL",
) -> dict[tuple[int, int], Decimal]:
    if not node_ids or not indicator_ids:
        return {}
    _, values = load_v2_target_values(
        session,
        business_date=target_date,
        period_type=_target_period(period_type),
        scenario=target_scenario,
        node_ids=node_ids,
        indicator_ids=indicator_ids,
    )
    return values


def _wide_rows(
    session: Session,
    *,
    nodes: list[HierarchyNode],
    indicators: list[IndicatorV2],
    as_of: datetime | None = None,
    period_type: str | None = None,
    include_targets: bool = False,
    current_stat_date: date | None = None,
    target_date: date | None = None,
    target_scenario: str = "NORMAL",
) -> list[dict[str, Any]]:
    components, physical = _components(session, indicators)
    values = _value_rows_at(
        session,
        node_ids=[row.id for row in nodes],
        indicator_ids=[row.id for row in physical],
        as_of=as_of,
        current_stat_date=current_stat_date,
    )
    code_by_id = {row.id: row.code for row in physical}
    parent_map = _historical_parent_map(session, as_of) if as_of else {}
    rows = []
    by_id: dict[int, dict[str, Any]] = {}
    for node in nodes:
        dto = _node_dto(node, parent_id=parent_map.get(node.id) if as_of else None)
        dto.update(
            collection_run_id=None,
            collected_at=None,
            metrics={row.code: None for row in physical},
        )
        rows.append(dto)
        by_id[node.id] = dto
    for value in values:
        row = by_id[value.node_id]
        row["metrics"][code_by_id[value.indicator_id]] = _number(value.metric_value)
        current_at = row["collected_at"]
        value_at = _iso(value.collected_at)
        if current_at is None or (value_at and value_at > current_at):
            row["collection_run_id"] = value.collection_run_id
            row["collected_at"] = value_at
    if include_targets and period_type:
        target_map = _active_target_map(
            session,
            node_ids=list(by_id),
            indicator_ids=[row.id for row in physical],
            period_type=period_type,
            target_date=target_date or (as_of.date() if as_of else date.today()),
            target_scenario=target_scenario,
        )
        for node_id, row in by_id.items():
            row["targets"] = {
                indicator.code: _number(target_map.get((node_id, indicator.id)))
                for indicator in physical
            }
    _apply_custom(rows, indicators, components)
    return rows


def _append_changes(
    session: Session,
    *,
    rows: list[dict[str, Any]],
    indicators: list[IndicatorV2],
    anchor: datetime,
    stat_date: date,
    windows: list[int],
) -> None:
    if not rows or not indicators:
        return
    components, physical = _components(session, indicators)
    for minutes in windows:
        historical = _change_value_rows_at(
            session,
            node_ids=[row["id"] for row in rows],
            indicator_ids=[row.id for row in physical],
            cutoff=anchor - timedelta(minutes=minutes),
            stat_date=stat_date,
        )
        values: dict[int, dict[str, Any]] = defaultdict(dict)
        code_by_id = {row.id: row.code for row in physical}
        for value in historical:
            values[value.node_id][code_by_id[value.indicator_id]] = _number(value.metric_value)
        for node_values in values.values():
            for code, parts in components.items():
                node_values[code] = _weighted_sum(node_values, parts)
        key = f"change_{minutes}min"
        for row in rows:
            changes = row.setdefault("changes", {})
            previous = values.get(row["id"], {})
            for indicator in indicators:
                current = row["metrics"].get(indicator.code)
                old = previous.get(indicator.code)
                value = None if current is None or old is None else current - old
                rate = None if value is None or old in (None, 0) else value / old
                changes.setdefault(indicator.code, {})[key] = {"value": value, "rate": rate}


def _change_value_rows_at(
    session: Session,
    *,
    node_ids: list[int],
    indicator_ids: list[int],
    cutoff: datetime,
    stat_date: date,
) -> list[Any]:
    """Match V1 change-window baseline selection.

    Prefer the closest snapshot within the configured +/- tolerance around the
    nominal cutoff.  If no nearby snapshot exists, carry the latest earlier
    value forward from the sparse-snapshot lookback range.
    """
    if not node_ids or not indicator_ids:
        return []

    tolerance = timedelta(minutes=CHANGE_WINDOW_TOLERANCE_MINUTES)
    near_lower = cutoff - tolerance
    upper_bound = cutoff + tolerance
    dialect_name = session.get_bind().dialect.name
    if dialect_name in {"mysql", "mariadb"}:
        distance_expr = "ABS(TIMESTAMPDIFF(MICROSECOND, ms.collected_at, :cutoff))"
    else:
        distance_expr = (
            "ABS((julianday(ms.collected_at) - julianday(:cutoff)) * 86400000000.0)"
        )

    query = text(f"""
        SELECT node_id, indicator_id, metric_value, collection_run_id, collected_at
        FROM (
            SELECT
                ms.node_id,
                ms.indicator_id,
                ms.metric_value,
                ms.collection_run_id,
                ms.collected_at,
                ROW_NUMBER() OVER (
                    PARTITION BY ms.node_id, ms.indicator_id
                    ORDER BY
                        CASE WHEN ms.collected_at >= :near_lower THEN 0 ELSE 1 END,
                        CASE WHEN ms.collected_at >= :near_lower
                            THEN {distance_expr} ELSE 0 END,
                        ms.collected_at DESC,
                        ms.id DESC
                ) AS rn
            FROM metric_snapshot AS ms
            INNER JOIN collection_run AS cr ON cr.id = ms.collection_run_id
            WHERE ms.node_id IN :node_ids
              AND ms.indicator_id IN :indicator_ids
              AND cr.stat_date = :stat_date
              AND ms.collected_at >= :baseline_start
              AND ms.collected_at <= :upper_bound
        ) ranked
        WHERE rn = 1
    """).bindparams(
        bindparam("node_ids", expanding=True),
        bindparam("indicator_ids", expanding=True),
    )
    return session.execute(
        query,
        {
            "node_ids": tuple(node_ids),
            "indicator_ids": tuple(indicator_ids),
            "stat_date": stat_date,
            "baseline_start": cutoff
            - timedelta(days=SPARSE_SNAPSHOT_BASELINE_LOOKBACK_DAYS),
            "near_lower": near_lower,
            "upper_bound": upper_bound,
            "cutoff": cutoff,
        },
    ).all()


def _change_anchor_time(run: CollectionRunV2 | None) -> datetime:
    """Use the same stable completed-run anchor as V1."""
    if run is not None and run.finished_at is not None:
        return run.finished_at
    return datetime.now()


def _change_stat_date(run: CollectionRunV2 | None, anchor: datetime) -> date:
    """Keep change-window baselines within the anchor's business date."""
    return run.stat_date if run is not None and run.stat_date is not None else anchor.date()


def _history_meta(run: CollectionRunV2 | None, selected_time: datetime) -> dict[str, Any]:
    """Expose historical fallback metadata with V1 minute-level semantics."""
    batch_time = (run.started_at or run.finished_at) if run is not None else None
    duration_seconds = None
    if run is not None and run.finished_at is not None:
        duration_seconds = max(0.0, (run.finished_at - run.started_at).total_seconds())
    selected_minute = selected_time.replace(second=0, microsecond=0)
    batch_minute = batch_time.replace(second=0, microsecond=0) if batch_time else None
    fallback_seconds = (
        max(0.0, (selected_minute - batch_minute).total_seconds())
        if batch_minute is not None
        else None
    )
    return {
        "batch_started_at": _iso(run.started_at) if run else None,
        "batch_finished_at": _iso(run.finished_at) if run else None,
        "duration_seconds": duration_seconds,
        "fallback_seconds": fallback_seconds,
        "is_fallback": bool(fallback_seconds and fallback_seconds > 0),
        "time_basis": "BATCH_STARTED_AT",
        "change_tolerance_minutes": CHANGE_WINDOW_TOLERANCE_MINUTES,
    }


def _realtime_business_date(run: CollectionRunV2 | None) -> date:
    if run is not None and run.stat_date is not None:
        return run.stat_date
    return datetime.now(_SHANGHAI_TZ).date()


def _realtime_accumulation_meta(
    session: Session,
    *,
    business_date: date,
) -> dict[str, Any]:
    through_date = business_date - timedelta(days=1)
    month_start = business_date.replace(day=1)
    baseline_zero = business_date.day == 1
    stat_date = None
    if not baseline_zero:
        stat_date = session.scalar(
            select(func.max(MetricAccV2.stat_date)).where(
                MetricAccV2.period_type == "DAY_ACC",
                MetricAccV2.stat_date >= month_start,
                MetricAccV2.stat_date <= through_date,
            )
        )
    return {
        "through_date": through_date.isoformat(),
        "stat_date": stat_date.isoformat() if stat_date else None,
        "is_fallback": bool(stat_date is not None and stat_date < through_date),
        "baseline_zero": baseline_zero,
        "baseline_missing": bool(not baseline_zero and stat_date is None),
        "target_period": "MONTH",
    }


def _apply_realtime_accumulation(
    session: Session,
    *,
    rows: list[dict[str, Any]],
    nodes: list[HierarchyNode],
    indicators: list[IndicatorV2],
    run: CollectionRunV2 | None,
    target_scenario: str = "NORMAL",
) -> dict[str, Any]:
    """Replace realtime metrics with current-month accumulated values."""
    business_date = _realtime_business_date(run)
    meta = _realtime_accumulation_meta(session, business_date=business_date)
    components, physical = _components(session, indicators)
    node_ids = [node.id for node in nodes]
    physical_ids = [indicator.id for indicator in physical]

    target_map = _active_target_map(
        session,
        node_ids=node_ids,
        indicator_ids=physical_ids,
        period_type="MONTH",
        target_date=business_date,
        target_scenario=target_scenario,
    )
    target_by_node: dict[int, dict[str, Any]] = {}
    for node_id in node_ids:
        values = {
            indicator.code: _number(target_map.get((node_id, indicator.id)))
            for indicator in physical
        }
        for code, parts in components.items():
            if values.get(code) is None:
                values[code] = _weighted_sum(values, parts)
        target_by_node[node_id] = values

    acc_by_node: dict[int, dict[str, Any]] = {}
    if meta["stat_date"]:
        acc_rows = _acc_metrics_in_session(
            session,
            nodes=nodes,
            indicators=indicators,
            period_type="DAY_ACC",
            target_day=date.fromisoformat(meta["stat_date"]),
            components=components,
            physical=physical,
        )
        acc_by_node = {
            node_id: row["metrics"]
            for node_id, row in acc_rows.items()
        }

    response_codes = [indicator.code for indicator in indicators]
    for row in rows:
        current_metrics = row.get("metrics") or {}
        acc_metrics = acc_by_node.get(row["id"], {})
        totals: dict[str, Any] = {}
        for code in response_codes:
            current = current_metrics.get(code)
            baseline = 0 if meta["baseline_zero"] else acc_metrics.get(code)
            total = (
                None
                if meta["baseline_missing"] or current is None or baseline is None
                else current + baseline
            )
            totals[code] = total

            changes = (row.get("changes") or {}).get(code, {})
            for payload in changes.values():
                delta = payload.get("value")
                if total is None or delta is None:
                    payload["value"] = None
                    payload["rate"] = None
                    continue
                previous_total = total - delta
                payload["rate"] = (
                    None if previous_total == 0 else delta / previous_total
                )

        row["metrics"] = totals
        row["targets"] = {
            code: target_by_node.get(row["id"], {}).get(code)
            for code in response_codes
        }
    return meta


def _apply_value_mode(
    session: Session,
    *,
    value_mode: str,
    rows: list[dict[str, Any]],
    nodes: list[HierarchyNode],
    indicators: list[IndicatorV2],
    run: CollectionRunV2 | None,
    target_scenario: str = "NORMAL",
) -> dict[str, Any] | None:
    normalized = _normalize_value_mode(value_mode)
    if normalized == "REALTIME":
        return None
    return _apply_realtime_accumulation(
        session,
        rows=rows,
        nodes=nodes,
        indicators=indicators,
        run=run,
        target_scenario=target_scenario,
    )


def get_indicator_catalog(
    engine: Engine, *, include_archived: bool = False, enabled_only: bool = False
) -> dict[str, Any]:
    with Session(engine) as session:
        stmt = select(IndicatorV2).order_by(IndicatorV2.sort_order, IndicatorV2.id)
        if not include_archived:
            stmt = stmt.where(or_(IndicatorV2.source_active.is_(True), IndicatorV2.enabled.is_(True)))
        if enabled_only:
            stmt = stmt.where(IndicatorV2.enabled.is_(True))
        rows = list(session.scalars(stmt))
        return {"indicators": [{
            **_indicator_dto(row),
            "enabled": row.enabled,
            "source_active": row.source_active,
            "indicator_type": row.indicator_type,
            "storage_mode": row.storage_mode,
            "removed_at": _iso(row.removed_at),
        } for row in rows]}


def get_latest_dashboard_run(engine: Engine) -> dict[str, Any]:
    with Session(engine) as session:
        run = _latest_run(session)
        acc_meta = _realtime_accumulation_meta(
            session,
            business_date=_realtime_business_date(run),
        )
        acc_state = (None, None)
        if acc_meta["stat_date"]:
            acc_state = session.execute(
                select(
                    func.max(MetricAccV2.collection_run_id),
                    func.max(MetricAccV2.collected_at),
                ).where(
                    MetricAccV2.period_type == "DAY_ACC",
                    MetricAccV2.stat_date == date.fromisoformat(acc_meta["stat_date"]),
                )
            ).one()
        indicator_state = session.execute(
            select(func.count(IndicatorV2.id), func.max(IndicatorV2.updated_at))
        ).one()
        target_state = session.execute(
            select(func.count(TargetPlan.id), func.max(TargetPlan.updated_at))
        ).one()
        config_version = sha256(repr((*indicator_state, *target_state)).encode()).hexdigest()[:16]
        acc_label = acc_meta["stat_date"] or (
            "zero" if acc_meta["baseline_zero"] else "missing"
        )
        return {
            "latest_run": _run_dto(run, full=False),
            "data_version": ":".join(
                [
                    f"{run.id}:{_iso(run.finished_at)}" if run else "0",
                    str(acc_label),
                    str(acc_state[0] or 0),
                    str(_iso(acc_state[1]) or ""),
                ]
            ),
            "config_version": config_version,
        }


def get_history_range(engine: Engine) -> dict[str, Any]:
    with Session(engine) as session:
        first_snapshot_at = session.scalar(
            select(func.min(MetricSnapshotV2.collected_at))
        )
        if first_snapshot_at is None:
            return {"earliest_at": None, "latest_at": None}
        batch_time = func.coalesce(
            CollectionRunV2.started_at, CollectionRunV2.finished_at
        )
        earliest, latest = session.execute(
            select(func.min(batch_time), func.max(batch_time)).where(
                CollectionRunV2.run_type == "REALTIME",
                CollectionRunV2.status == "SUCCESS",
                batch_time.is_not(None),
                CollectionRunV2.finished_at >= first_snapshot_at,
            )
        ).one()
        return {"earliest_at": _iso(earliest), "latest_at": _iso(latest)}


def get_history_options(
    engine: Engine, *, indicator_codes: list[str] | None = None
) -> dict[str, Any]:
    with Session(engine) as session:
        snapshot_range_query = select(func.min(MetricSnapshotV2.collected_at))
        if indicator_codes:
            indicator_ids = list(
                session.scalars(
                    select(IndicatorV2.id).where(IndicatorV2.code.in_(indicator_codes))
                )
            )
            snapshot_range_query = snapshot_range_query.where(
                MetricSnapshotV2.indicator_id.in_(indicator_ids or {-1})
            )
        first_snapshot_at = session.scalar(snapshot_range_query)
        if first_snapshot_at is None:
            return {"dates": [], "date_count": 0}

        batch_time = func.coalesce(
            CollectionRunV2.started_at, CollectionRunV2.finished_at
        )
        runs = session.execute(
            select(CollectionRunV2.id, batch_time.label("batch_time")).where(
                CollectionRunV2.run_type == "REALTIME",
                CollectionRunV2.status == "SUCCESS",
                batch_time.is_not(None),
                CollectionRunV2.finished_at >= first_snapshot_at,
            ).order_by(batch_time.desc(), CollectionRunV2.id.desc())
        ).all()

    minutes_by_date: dict[str, set[str]] = defaultdict(set)
    for run in runs:
        if run.batch_time is None:
            continue
        minutes_by_date[run.batch_time.date().isoformat()].add(
            run.batch_time.strftime("%H:%M")
        )
    dates = [
        {"date": day, "times": sorted(times, reverse=True)}
        for day, times in sorted(minutes_by_date.items(), reverse=True)
    ]
    return {"dates": dates, "date_count": len(dates)}


def get_acc_options(
    engine: Engine, *, page: int = 1, page_size: int = 50
) -> dict[str, Any]:
    with Session(engine) as session:
        total = session.scalar(
            select(func.count(func.distinct(MetricAccV2.stat_date))).where(
                MetricAccV2.period_type == "DAY_ACC"
            )
        ) or 0
        dates = list(
            session.scalars(
                select(MetricAccV2.stat_date)
                .where(MetricAccV2.period_type == "DAY_ACC")
                .distinct()
                .order_by(MetricAccV2.stat_date.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
    return {
        "dates": [_iso(value) for value in dates],
        "page": page,
        "page_size": page_size,
        "total": total,
        "has_more": page * page_size < total,
    }


def get_historical_run_id(engine: Engine, as_of: datetime) -> int | None:
    with Session(engine) as session:
        run = _historical_run(session, as_of)
        return run.id if run else None


def get_current_wide_table(
    engine: Engine,
    *,
    node_type: str | None = None,
    parent_id: int | None = None,
    indicator_codes: list[str] | None = None,
    value_mode: str = "REALTIME",
    target_scenario: str = "NORMAL",
) -> dict[str, Any]:
    normalized_mode = _normalize_value_mode(value_mode)
    normalized_target_scenario = normalize_target_scenario(target_scenario)
    with Session(engine) as session:
        run = _latest_run(session)
        indicators = _enabled_indicators(session, indicator_codes)
        nodes = _nodes(session, node_type=node_type, parent_id=parent_id)
        rows = _wide_rows(
            session,
            nodes=nodes,
            indicators=indicators,
            period_type="DAY_ACC",
            include_targets=normalized_mode != "REALTIME_ACC",
            current_stat_date=(
                _realtime_business_date(run)
                if normalized_mode == "REALTIME_ACC"
                else None
            ),
            target_date=_realtime_business_date(run),
            target_scenario=normalized_target_scenario,
        )
        accumulation_meta = _apply_value_mode(
            session,
            value_mode=normalized_mode,
            rows=rows,
            nodes=nodes,
            indicators=indicators,
            run=run,
            target_scenario=normalized_target_scenario,
        )
        result = {
            "data_mode": normalized_mode,
            "latest_run": _run_dto(run),
            "indicators": [_indicator_dto(row) for row in indicators],
            "rows": rows,
            "row_count": len(rows),
        }
        if accumulation_meta is not None:
            result["accumulation_meta"] = accumulation_meta
        return result


def get_current_with_changes(
    engine: Engine,
    *,
    node_type: str | None = None,
    parent_id: int | None = None,
    change_windows: list[int] | None = None,
    indicator_codes: list[str] | None = None,
    value_mode: str = "REALTIME",
    target_scenario: str = "NORMAL",
) -> dict[str, Any]:
    normalized_mode = _normalize_value_mode(value_mode)
    normalized_target_scenario = normalize_target_scenario(target_scenario)
    windows = change_windows or [5, 15, 30, 60]
    with Session(engine) as session:
        run = _latest_run(session)
        indicators = _enabled_indicators(session, indicator_codes)
        nodes = _nodes(session, node_type=node_type, parent_id=parent_id)
        rows = _wide_rows(
            session,
            nodes=nodes,
            indicators=indicators,
            period_type="DAY_ACC",
            include_targets=normalized_mode != "REALTIME_ACC",
            current_stat_date=(
                _realtime_business_date(run)
                if normalized_mode == "REALTIME_ACC"
                else None
            ),
            target_date=_realtime_business_date(run),
            target_scenario=normalized_target_scenario,
        )
        anchor = _change_anchor_time(run)
        _append_changes(
            session,
            rows=rows,
            indicators=indicators,
            anchor=anchor,
            stat_date=_change_stat_date(run, anchor),
            windows=windows,
        )
        accumulation_meta = _apply_value_mode(
            session,
            value_mode=normalized_mode,
            rows=rows,
            nodes=nodes,
            indicators=indicators,
            run=run,
            target_scenario=normalized_target_scenario,
        )
        result = {
            "data_mode": normalized_mode,
            "latest_run": _run_dto(run),
            "indicators": [_indicator_dto(row) for row in indicators],
            "rows": rows,
            "row_count": len(rows),
        }
        if accumulation_meta is not None:
            result["accumulation_meta"] = accumulation_meta
        return result


def get_historical_with_changes(
    engine: Engine,
    *,
    as_of: datetime,
    node_type: str | None = None,
    change_windows: list[int] | None = None,
    indicator_codes: list[str] | None = None,
    scope_mode: str = "default",
    parent_id: int | None = None,
    parent_node_type: str | None = None,
    branch_code: str | None = "AQ",
    target_scenario: str = "NORMAL",
) -> dict[str, Any]:
    windows = change_windows or [5, 15, 30, 60]
    normalized_target_scenario = normalize_target_scenario(target_scenario)
    with Session(engine) as session:
        run = _historical_run(session, as_of)
        value_cutoff = (
            run.finished_at or run.started_at or as_of
            if run is not None
            else as_of
        )
        change_anchor = (
            run.started_at or run.finished_at or as_of
            if run is not None
            else as_of
        )
        indicators = _enabled_indicators(session, indicator_codes)
        if node_type:
            nodes = _scoped_nodes(
                session,
                node_type=_normalized_node_type(node_type) or "BRANCH",
                scope_mode=scope_mode,
                parent_id=parent_id,
                branch_code=branch_code,
                as_of=value_cutoff,
            )
        elif parent_id is not None and parent_node_type:
            nodes = _drill_context_nodes(
                session,
                parent_id=parent_id,
                parent_node_type=parent_node_type,
                as_of=value_cutoff,
            )
        elif parent_id is not None:
            nodes = _nodes(session, parent_id=parent_id, as_of=value_cutoff)
        else:
            nodes = _nodes(session, as_of=value_cutoff)
        rows = _wide_rows(
            session,
            nodes=nodes,
            indicators=indicators,
            as_of=value_cutoff,
            period_type="DAY_ACC",
            include_targets=True,
            target_scenario=normalized_target_scenario,
        )
        _append_changes(
            session,
            rows=rows,
            indicators=indicators,
            anchor=change_anchor,
            stat_date=_change_stat_date(run, change_anchor),
            windows=windows,
        )
        return {
            "data_mode": "HISTORY",
            "selected_time": _iso(as_of),
            "latest_run": _run_dto(run),
            "indicators": [_indicator_dto(row) for row in indicators],
            "rows": rows,
            "row_count": len(rows),
            "coverage": _coverage(nodes, rows, indicators),
            "history_meta": _history_meta(run, as_of),
        }


def _coverage(
    nodes: list[HierarchyNode], rows: list[dict[str, Any]], indicators: list[IndicatorV2]
) -> dict[str, Any]:
    result: dict[str, Any] = {"levels": {}}
    by_id = {row["id"]: row for row in rows}
    for node_type in sorted({row.node_type for row in nodes}):
        expected = [row for row in nodes if row.node_type == node_type]
        available = [row for row in expected if any(v is not None for v in by_id[row.id]["metrics"].values())]
        cells = sum(
            value is not None
            for row in expected
            for value in by_id[row.id]["metrics"].values()
        )
        result["levels"][node_type] = {
            "expected_nodes": len(expected),
            "snapshot_nodes": len(available),
            "missing_nodes": len(expected) - len(available),
            "extra_nodes": 0,
            "available_metric_cells": cells,
            "total_metric_cells": len(expected) * len(indicators),
        }
    return result


def _sort_rows(
    rows: list[dict[str, Any]], sort_indicator: str | None, sort_mode: str
) -> list[dict[str, Any]]:
    if not sort_indicator:
        return rows
    reverse = str(sort_mode).lower().endswith("desc")
    normalized_mode = str(sort_mode).strip().lower()
    def key(row: dict[str, Any]) -> tuple[bool, float]:
        value = row["metrics"].get(sort_indicator)
        if normalized_mode.startswith(("progress", "rate")):
            target = (row.get("targets") or {}).get(sort_indicator)
            value = None if value is None or target in (None, 0) else value / target
        elif normalized_mode.startswith(("changevalue", "changerate")):
            windows = (row.get("changes") or {}).get(sort_indicator, {})
            payload = next(iter(windows.values()), {})
            value = payload.get(
                "rate" if normalized_mode.startswith("changerate") else "value"
            )
        present = value is not None
        return (present if reverse else not present, float(value or 0))
    return sorted(rows, key=key, reverse=reverse)


def _matrix_result(
    result: dict[str, Any], *, search: str | None, sort_indicator: str | None,
    sort_mode: str, page: int, page_size: int
) -> dict[str, Any]:
    rows = result["rows"]
    keyword = str(search or "").strip().lower()
    if keyword:
        rows = [row for row in rows if keyword in row["node_name"].lower() or keyword in row["node_code"].lower()]
    rows = _sort_rows(rows, sort_indicator, sort_mode)
    total = len(rows)
    start = (page - 1) * page_size
    result["rows"] = rows[start:start + page_size]
    result["row_count"] = len(result["rows"])
    result.update(total=total, page=page, page_size=page_size, total_pages=max(1, (total + page_size - 1) // page_size))
    return result


def get_dashboard_matrix_page(
    engine: Engine, *, node_type: str, scope_mode: str = "default",
    parent_id: int | None = None, parent_node_type: str | None = None,
    branch_code: str | None = "AQ", indicator_codes: list[str] | None = None,
    change_window: int = 60, search: str | None = None,
    sort_indicator: str | None = None, sort_mode: str = "doneDesc",
    page: int = 1, page_size: int = 100,
    value_mode: str = "REALTIME",
    target_scenario: str = "NORMAL",
) -> dict[str, Any]:
    normalized_mode = _normalize_value_mode(value_mode)
    normalized_target_scenario = normalize_target_scenario(target_scenario)
    del parent_node_type
    with Session(engine) as session:
        run = _latest_run(session)
        normalized = _normalized_node_type(node_type) or "BRANCH"
        indicators = _enabled_indicators(session, indicator_codes)
        nodes = _scoped_nodes(
            session,
            node_type=normalized,
            scope_mode=scope_mode,
            parent_id=parent_id,
            branch_code=branch_code,
        )
        rows = _wide_rows(
            session,
            nodes=nodes,
            indicators=indicators,
            period_type="DAY_ACC",
            include_targets=normalized_mode != "REALTIME_ACC",
            current_stat_date=(
                _realtime_business_date(run)
                if normalized_mode == "REALTIME_ACC"
                else None
            ),
            target_date=_realtime_business_date(run),
            target_scenario=normalized_target_scenario,
        )
        anchor = _change_anchor_time(run)
        _append_changes(
            session,
            rows=rows,
            indicators=indicators,
            anchor=anchor,
            stat_date=_change_stat_date(run, anchor),
            windows=[change_window],
        )
        accumulation_meta = _apply_value_mode(
            session,
            value_mode=normalized_mode,
            rows=rows,
            nodes=nodes,
            indicators=indicators,
            run=run,
            target_scenario=normalized_target_scenario,
        )
        result = {
            "data_mode": normalized_mode,
            "latest_run": _run_dto(run),
            "indicators": [_indicator_dto(row) for row in indicators],
            "rows": rows,
            "row_count": len(rows),
        }
        if accumulation_meta is not None:
            result["accumulation_meta"] = accumulation_meta
    return _matrix_result(result, search=search, sort_indicator=sort_indicator, sort_mode=sort_mode, page=page, page_size=page_size)


def get_historical_matrix_page(
    engine: Engine, *, as_of: datetime, node_type: str,
    scope_mode: str = "default", parent_id: int | None = None,
    parent_node_type: str | None = None, branch_code: str | None = "AQ",
    indicator_codes: list[str] | None = None, change_window: int = 60,
    search: str | None = None, sort_indicator: str | None = None,
    sort_mode: str = "doneDesc", page: int = 1, page_size: int = 100,
    target_scenario: str = "NORMAL",
) -> dict[str, Any]:
    result = get_historical_with_changes(
        engine, as_of=as_of, node_type=node_type, change_windows=[change_window],
        indicator_codes=indicator_codes, scope_mode=scope_mode, parent_id=parent_id,
        parent_node_type=parent_node_type, branch_code=branch_code,
        target_scenario=target_scenario,
    )
    return _matrix_result(result, search=search, sort_indicator=sort_indicator, sort_mode=sort_mode, page=page, page_size=page_size)


def _acc_metrics_in_session(
    session: Session,
    *,
    nodes: list[HierarchyNode],
    indicators: list[IndicatorV2],
    period_type: str,
    target_day: date,
    components: dict[str, list[tuple[str, Decimal]]] | None = None,
    physical: list[IndicatorV2] | None = None,
) -> dict[int, dict[str, Any]]:
    if components is None or physical is None:
        components, physical = _components(session, indicators)
    values = session.execute(
        select(MetricAccV2).where(
            MetricAccV2.period_type == period_type,
            MetricAccV2.stat_date == target_day,
            MetricAccV2.node_id.in_([row.id for row in nodes] or [-1]),
            MetricAccV2.indicator_id.in_([row.id for row in physical] or [-1]),
        )
    ).scalars().all()
    code_by_id = {row.id: row.code for row in physical}
    by_id = {
        node.id: {
            "collection_run_id": None,
            "collected_at": None,
            "metrics": {row.code: None for row in physical},
        }
        for node in nodes
    }
    for value in values:
        row = by_id[value.node_id]
        row["metrics"][code_by_id[value.indicator_id]] = _number(value.metric_value)
        row["collection_run_id"] = value.collection_run_id
        row["collected_at"] = _iso(value.collected_at)
    _apply_custom(list(by_id.values()), indicators, components)
    return by_id


def _acc_rows_in_session(
    session: Session,
    *,
    nodes: list[HierarchyNode],
    indicators: list[IndicatorV2],
    period_type: str,
    target_day: date,
    target_scenario: str = "NORMAL",
) -> list[dict[str, Any]]:
    components, physical = _components(session, indicators)
    metrics_by_id = _acc_metrics_in_session(
        session,
        nodes=nodes,
        indicators=indicators,
        period_type=period_type,
        target_day=target_day,
        components=components,
        physical=physical,
    )
    target_map = _active_target_map(
        session,
        node_ids=[row.id for row in nodes],
        indicator_ids=[row.id for row in physical],
        period_type=period_type,
        target_date=target_day,
        target_scenario=target_scenario,
    )
    rows: list[dict[str, Any]] = []
    for node in nodes:
        dto = _node_dto(node)
        metric_row = metrics_by_id[node.id]
        dto.update(
            collection_run_id=metric_row["collection_run_id"],
            collected_at=metric_row["collected_at"],
            metrics=metric_row["metrics"],
            targets={
                row.code: _number(target_map.get((node.id, row.id)))
                for row in physical
            },
        )
        rows.append(dto)
    _apply_custom(rows, indicators, components)
    return rows


def get_acc_wide_table(
    engine: Engine, *, period_type: str = "DAY_ACC", node_type: str | None = None,
    parent_id: int | None = None, stat_date: str | None = None,
    indicator_codes: list[str] | None = None,
    target_scenario: str = "NORMAL",
) -> dict[str, Any]:
    normalized = str(period_type).strip().upper()
    if normalized not in {"DAY_ACC", "MONTH"}:
        raise ValueError(f"period_type 只支持 DAY_ACC/MONTH: {period_type!r}")
    normalized_target_scenario = normalize_target_scenario(target_scenario)
    with Session(engine) as session:
        through_date = date.fromisoformat(stat_date) if stat_date else _yesterday_shanghai()
        target_day = (
            through_date
            if stat_date
            else session.scalar(
                select(func.max(MetricAccV2.stat_date)).where(
                    MetricAccV2.period_type == normalized,
                    MetricAccV2.stat_date <= through_date,
                )
            )
        )
        indicators = _enabled_indicators(session, indicator_codes)
        nodes = _nodes(session, node_type=node_type, parent_id=parent_id)
        rows = (
            _acc_rows_in_session(
                session,
                nodes=nodes,
                indicators=indicators,
                period_type=normalized,
                target_day=target_day,
                target_scenario=normalized_target_scenario,
            )
            if target_day is not None
            else []
        )
        rows = [
            row for row in rows
            if any(value is not None for value in row["metrics"].values())
        ]
        return {
            "indicators": [_indicator_dto(row) for row in indicators],
            "rows": rows,
            "row_count": len(rows),
            "through_date": through_date.isoformat(),
            "stat_date": target_day.isoformat() if target_day else None,
            "is_fallback": bool(target_day is not None and target_day < through_date),
        }


def get_dashboard_overview(
    engine: Engine, *, branch_id: int | None = None, branch_code: str | None = None,
    period_type: str = "DAY_ACC", change_windows: list[int] | None = None,
    indicator_codes: list[str] | None = None, include_acc: bool = True,
    value_mode: str = "REALTIME",
    target_scenario: str = "NORMAL",
) -> dict[str, Any]:
    normalized_mode = _normalize_value_mode(value_mode)
    normalized_target_scenario = normalize_target_scenario(target_scenario)
    with Session(engine) as session:
        run = _latest_run(session)
        branches = _nodes(session, node_type="BRANCH")
        branch = next(
            (
                row for row in branches
                if branch_id is not None and row.id == branch_id
            ),
            None,
        )
        if branch is None and branch_code:
            branch = next(
                (row for row in branches if row.node_code == branch_code),
                None,
            )
        if branch is None:
            branch = next(
                (
                    row for row in branches
                    if "中原" in row.node_name or row.node_code == "AQ"
                ),
                branches[0] if branches else None,
            )
        indicators = _enabled_indicators(session, indicator_codes)
        # Preserve the pre-V2 cockpit scope: the BRANCH board compares every
        # branch, while lower-level boards are limited to the selected branch.
        # Using only the selected branch's subtree makes the top board collapse
        # to one row after the database migration.
        nodes = branches + (
            _descendant_nodes(session, branch.id) if branch else []
        )
        rows = _wide_rows(
            session,
            nodes=nodes,
            indicators=indicators,
            period_type=period_type,
            include_targets=normalized_mode != "REALTIME_ACC",
            current_stat_date=(
                _realtime_business_date(run)
                if normalized_mode == "REALTIME_ACC"
                else None
            ),
            target_date=_realtime_business_date(run),
            target_scenario=normalized_target_scenario,
        )
        anchor = _change_anchor_time(run)
        _append_changes(
            session,
            rows=rows,
            indicators=indicators,
            anchor=anchor,
            stat_date=_change_stat_date(run, anchor),
            windows=change_windows or [5, 15, 30, 60],
        )
        accumulation_meta = _apply_value_mode(
            session,
            value_mode=normalized_mode,
            rows=rows,
            nodes=nodes,
            indicators=indicators,
            run=run,
            target_scenario=normalized_target_scenario,
        )
        acc_rows: list[dict[str, Any]] = []
        if include_acc and normalized_mode == "REALTIME":
            normalized_period = str(period_type).strip().upper()
            acc_date = session.scalar(
                select(func.max(MetricAccV2.stat_date)).where(
                    MetricAccV2.period_type == normalized_period
                )
            ) or date.today()
            acc_rows = _acc_rows_in_session(
                session,
                nodes=nodes,
                indicators=indicators,
                period_type=normalized_period,
                target_day=acc_date,
                target_scenario=normalized_target_scenario,
            )
        result = {
            "data_mode": normalized_mode,
            "latest_run": _run_dto(run),
            "selected_branch": _node_dto(branch) if branch else None,
            "indicators": [_indicator_dto(row) for row in indicators],
            "rows": rows,
            "row_count": len(rows),
            "acc_rows": acc_rows,
            "acc_row_count": len(acc_rows),
        }
        if accumulation_meta is not None:
            result["accumulation_meta"] = accumulation_meta
        return result


def get_drill_down(
    engine: Engine, *, parent_id: int, parent_node_type: str,
    period_type: str = "DAY_ACC", change_windows: list[int] | None = None,
    indicator_codes: list[str] | None = None, include_acc: bool = True,
    tree_mode: str = "full",
    value_mode: str = "REALTIME",
    target_scenario: str = "NORMAL",
) -> dict[str, Any]:
    normalized_mode = _normalize_value_mode(value_mode)
    normalized_target_scenario = normalize_target_scenario(target_scenario)
    parent_type = _normalized_node_type(parent_node_type)
    normalized_tree_mode = str(tree_mode or "").strip().lower()
    if normalized_tree_mode not in {"full", "flat"}:
        raise ValueError("tree_mode 只支持 full/flat")
    child_by_parent = {
        "CITY": "BRANCH", "BRANCH": "GRID", "GRID": "CHANNEL_MANAGER",
        "CHANNEL_MANAGER": "CHANNEL",
    }
    child_type = child_by_parent.get(parent_type or "")
    if child_type is None:
        raise ValueError(f"{parent_node_type!r} 没有可下钻层级")
    if parent_type == "GRID" and normalized_tree_mode == "flat":
        with Session(engine) as session:
            run = _latest_run(session)
            managers = _nodes(
                session,
                node_type="CHANNEL_MANAGER",
                parent_id=parent_id,
            )
            manager_ids = {row.id for row in managers}
            nodes = [
                row for row in _nodes(session, node_type="CHANNEL")
                if row.parent_id in manager_ids
            ]
            indicators = _enabled_indicators(session, indicator_codes)
            rows = _wide_rows(
                session,
                nodes=nodes,
                indicators=indicators,
                period_type=period_type,
                include_targets=normalized_mode != "REALTIME_ACC",
                current_stat_date=(
                    _realtime_business_date(run)
                    if normalized_mode == "REALTIME_ACC"
                    else None
                ),
                target_date=_realtime_business_date(run),
                target_scenario=normalized_target_scenario,
            )
            anchor = _change_anchor_time(run)
            _append_changes(
                session,
                rows=rows,
                indicators=indicators,
                anchor=anchor,
                stat_date=_change_stat_date(run, anchor),
                windows=change_windows or [5, 15, 30, 60],
            )
            accumulation_meta = _apply_value_mode(
                session,
                value_mode=normalized_mode,
                rows=rows,
                nodes=nodes,
                indicators=indicators,
                run=run,
                target_scenario=normalized_target_scenario,
            )
            acc_rows = []
            if include_acc and normalized_mode == "REALTIME":
                normalized_period = str(period_type).strip().upper()
                acc_date = session.scalar(
                    select(func.max(MetricAccV2.stat_date)).where(
                        MetricAccV2.period_type == normalized_period
                    )
                ) or date.today()
                acc_rows = _acc_rows_in_session(
                    session,
                    nodes=nodes,
                    indicators=indicators,
                    period_type=normalized_period,
                    target_day=acc_date,
                    target_scenario=normalized_target_scenario,
                )
            result = {
                "data_mode": normalized_mode,
                "tree_mode": "flat",
                "latest_run": _run_dto(run),
                "indicators": [_indicator_dto(row) for row in indicators],
                "rows": rows,
                "row_count": len(rows),
                "acc_rows": acc_rows,
                "acc_row_count": len(acc_rows),
            }
            if accumulation_meta is not None:
                result["accumulation_meta"] = accumulation_meta
            return result

    # Keep ancestor comparison panels populated and refresh every descendant
    # panel within the clicked scope. For example, clicking a BRANCH keeps its
    # sibling branches visible while loading all GRID / CHANNEL_MANAGER /
    # CHANNEL rows below that branch.
    with Session(engine) as session:
        run = _latest_run(session)
        context_nodes = _drill_context_nodes(
            session,
            parent_id=parent_id,
            parent_node_type=parent_type,
        )

        indicators = _enabled_indicators(session, indicator_codes)
        rows = _wide_rows(
            session,
            nodes=context_nodes,
            indicators=indicators,
            period_type=period_type,
            include_targets=normalized_mode != "REALTIME_ACC",
            current_stat_date=(
                _realtime_business_date(run)
                if normalized_mode == "REALTIME_ACC"
                else None
            ),
            target_date=_realtime_business_date(run),
            target_scenario=normalized_target_scenario,
        )
        anchor = _change_anchor_time(run)
        _append_changes(
            session,
            rows=rows,
            indicators=indicators,
            anchor=anchor,
            stat_date=_change_stat_date(run, anchor),
            windows=change_windows or [5, 15, 30, 60],
        )
        accumulation_meta = _apply_value_mode(
            session,
            value_mode=normalized_mode,
            rows=rows,
            nodes=context_nodes,
            indicators=indicators,
            run=run,
            target_scenario=normalized_target_scenario,
        )
        acc_rows: list[dict[str, Any]] = []
        if include_acc and normalized_mode == "REALTIME":
            normalized_period = str(period_type).strip().upper()
            acc_date = session.scalar(
                select(func.max(MetricAccV2.stat_date)).where(
                    MetricAccV2.period_type == normalized_period
                )
            ) or date.today()
            acc_rows = _acc_rows_in_session(
                session,
                nodes=context_nodes,
                indicators=indicators,
                period_type=normalized_period,
                target_day=acc_date,
                target_scenario=normalized_target_scenario,
            )
        result = {
            "data_mode": normalized_mode,
            "tree_mode": "full",
            "latest_run": _run_dto(run),
            "indicators": [_indicator_dto(row) for row in indicators],
            "rows": rows,
            "row_count": len(rows),
            "acc_rows": acc_rows,
            "acc_row_count": len(acc_rows),
        }
        if accumulation_meta is not None:
            result["accumulation_meta"] = accumulation_meta
        return result


__all__ = [
    "get_acc_options", "get_acc_wide_table", "get_current_wide_table", "get_current_with_changes",
    "get_dashboard_matrix_page", "get_dashboard_overview", "get_drill_down",
    "get_historical_matrix_page", "get_historical_run_id",
    "get_historical_with_changes", "get_history_options", "get_history_range",
    "get_indicator_catalog", "get_latest_dashboard_run", "parse_change_window_minutes",
]
