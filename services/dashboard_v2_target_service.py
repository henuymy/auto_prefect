"""Versioned NORMAL/PK and DAY/MONTH target plans for Dashboard V2."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Iterable

from sqlalchemy import delete, or_, select
from sqlalchemy.orm import Session

from models.dashboard_v2 import MetricTargetValue, TargetPlan
from services.dashboard_metric_store import parse_metric_value


class TargetPlanError(RuntimeError):
    error_type = "INVALID_TARGET_PLAN"


class AmbiguousTargetPlanError(TargetPlanError):
    error_type = "AMBIGUOUS_TARGET_PLAN"


@dataclass(frozen=True)
class TargetPlanCandidate:
    id: int
    scenario: str
    period_type: str
    effective_from: date
    priority: int


def choose_target_plan_candidate(
    candidates: Iterable[TargetPlanCandidate],
) -> TargetPlanCandidate | None:
    """Apply the documented priority and effective-date tie breakers."""
    ordered = sorted(
        candidates,
        key=lambda item: (item.priority, item.effective_from, item.id),
        reverse=True,
    )
    if not ordered:
        return None
    winner = ordered[0]
    ambiguous = [
        item
        for item in ordered[1:]
        if item.priority == winner.priority
        and item.effective_from == winner.effective_from
    ]
    if ambiguous:
        ids = [winner.id, *(item.id for item in ambiguous)]
        raise AmbiguousTargetPlanError(f"目标方案无法唯一确定: ids={ids}")
    return winner


def normalize_target_scenario(scenario: str = "NORMAL") -> str:
    normalized = str(scenario or "").strip().upper()
    if normalized not in {"NORMAL", "PK"}:
        raise ValueError("target_scenario 只支持 NORMAL/PK")
    return normalized


def resolve_v2_target_plan(
    session: Session,
    *,
    business_date: date,
    period_type: str,
    scenario: str = "NORMAL",
) -> TargetPlan | None:
    """Resolve the active target plan for an explicit business scenario."""
    normalized_period = str(period_type or "").strip().upper()
    if normalized_period not in {"DAY", "MONTH"}:
        raise ValueError("period_type 只支持 DAY/MONTH")
    normalized_scenario = normalize_target_scenario(scenario)
    plans = list(
        session.scalars(
            select(TargetPlan).where(
                TargetPlan.scenario == normalized_scenario,
                TargetPlan.period_type == normalized_period,
                TargetPlan.status.in_(["ACTIVE", "RETIRED"]),
                TargetPlan.effective_from <= business_date,
                or_(
                    TargetPlan.effective_to.is_(None),
                    TargetPlan.effective_to >= business_date,
                ),
            )
        )
    )
    winner = choose_target_plan_candidate(
        TargetPlanCandidate(
            id=plan.id,
            scenario=plan.scenario,
            period_type=plan.period_type,
            effective_from=plan.effective_from,
            priority=plan.priority,
        )
        for plan in plans
    )
    return next((plan for plan in plans if winner is not None and plan.id == winner.id), None)


def load_v2_target_values(
    session: Session,
    *,
    business_date: date,
    period_type: str,
    scenario: str = "NORMAL",
    node_ids: Iterable[int] | None = None,
    indicator_ids: Iterable[int] | None = None,
) -> tuple[TargetPlan | None, dict[tuple[int, int], Decimal]]:
    """Return the selected immutable plan and its requested target values."""
    plan = resolve_v2_target_plan(
        session,
        business_date=business_date,
        period_type=period_type,
        scenario=scenario,
    )
    if plan is None:
        return None, {}
    query = select(MetricTargetValue).where(MetricTargetValue.plan_id == plan.id)
    normalized_node_ids = set(node_ids or [])
    normalized_indicator_ids = set(indicator_ids or [])
    if normalized_node_ids:
        query = query.where(MetricTargetValue.node_id.in_(normalized_node_ids))
    if normalized_indicator_ids:
        query = query.where(
            MetricTargetValue.indicator_id.in_(normalized_indicator_ids)
        )
    values = session.scalars(query).all()
    return plan, {
        (value.node_id, value.indicator_id): value.target_value for value in values
    }


def replace_draft_target_values_in_session(
    session: Session,
    *,
    plan_id: int,
    values: Iterable[dict[str, object]],
) -> int:
    """Replace all values of a DRAFT plan after validating numeric values."""
    plan = session.scalar(
        select(TargetPlan).where(TargetPlan.id == plan_id).with_for_update()
    )
    if plan is None:
        raise TargetPlanError(f"目标方案不存在: {plan_id}")
    if plan.status != "DRAFT":
        raise TargetPlanError("只有 DRAFT 目标方案允许修改目标值")

    normalized: dict[tuple[int, int], Decimal] = {}
    for item in values:
        node_id = item.get("node_id")
        indicator_id = item.get("indicator_id")
        if not isinstance(node_id, int) or node_id <= 0:
            raise TargetPlanError(f"目标值 node_id 非法: {node_id!r}")
        if not isinstance(indicator_id, int) or indicator_id <= 0:
            raise TargetPlanError(f"目标值 indicator_id 非法: {indicator_id!r}")
        key = (node_id, indicator_id)
        value = parse_metric_value(item.get("target_value"))
        if key in normalized and normalized[key] != value:
            raise TargetPlanError(f"目标值冲突: node_id={node_id}, indicator_id={indicator_id}")
        normalized[key] = value
    if not normalized:
        raise TargetPlanError("目标方案至少需要一条目标值")

    session.execute(
        delete(MetricTargetValue).where(MetricTargetValue.plan_id == plan_id)
    )
    session.add_all(
        MetricTargetValue(
            plan_id=plan_id,
            node_id=node_id,
            indicator_id=indicator_id,
            target_value=value,
        )
        for (node_id, indicator_id), value in normalized.items()
    )
    session.flush()
    return len(normalized)


def clone_v2_target_plan_in_session(
    session: Session,
    *,
    source_plan_id: int,
    effective_from: date,
) -> TargetPlan:
    """Create a mutable DRAFT version with copied target values."""
    source = session.scalar(
        select(TargetPlan).where(TargetPlan.id == source_plan_id).with_for_update()
    )
    if source is None:
        raise TargetPlanError(f"目标方案不存在: {source_plan_id}")
    if effective_from <= source.effective_from:
        raise TargetPlanError("新版本 effective_from 必须晚于旧版本")

    latest_version = session.scalar(
        select(TargetPlan.version_no)
        .where(
            TargetPlan.scenario == source.scenario,
            TargetPlan.period_type == source.period_type,
            TargetPlan.plan_name == source.plan_name,
        )
        .order_by(TargetPlan.version_no.desc())
        .limit(1)
        .with_for_update()
    )
    clone = TargetPlan(
        plan_name=source.plan_name,
        scenario=source.scenario,
        period_type=source.period_type,
        effective_from=effective_from,
        effective_to=source.effective_to,
        priority=source.priority,
        version_no=int(latest_version or 0) + 1,
        status="DRAFT",
        supersedes_plan_id=source.id,
    )
    session.add(clone)
    session.flush()
    source_values = session.scalars(
        select(MetricTargetValue).where(
            MetricTargetValue.plan_id == source_plan_id
        )
    ).all()
    session.add_all(
        MetricTargetValue(
            plan_id=clone.id,
            node_id=value.node_id,
            indicator_id=value.indicator_id,
            target_value=value.target_value,
        )
        for value in source_values
    )
    session.flush()
    return clone


def activate_v2_target_plan_in_session(
    session: Session,
    *,
    plan_id: int,
    activated_at: datetime,
) -> TargetPlan:
    """Activate one DRAFT and retire its explicitly superseded version."""
    plan = session.scalar(
        select(TargetPlan).where(TargetPlan.id == plan_id).with_for_update()
    )
    if plan is None:
        raise TargetPlanError(f"目标方案不存在: {plan_id}")
    if plan.status != "DRAFT":
        raise TargetPlanError("只有 DRAFT 目标方案允许激活")
    value_count = len(
        session.scalars(
            select(MetricTargetValue.id).where(
                MetricTargetValue.plan_id == plan.id
            )
        ).all()
    )
    if value_count == 0:
        raise TargetPlanError("空目标方案不允许激活")

    if plan.supersedes_plan_id is not None:
        previous = session.scalar(
            select(TargetPlan)
            .where(TargetPlan.id == plan.supersedes_plan_id)
            .with_for_update()
        )
        if previous is None:
            raise TargetPlanError("被替代的目标方案不存在")
        if (
            previous.scenario != plan.scenario
            or previous.period_type != plan.period_type
        ):
            raise TargetPlanError("新旧目标方案场景或周期不一致")
        if plan.effective_from <= previous.effective_from:
            raise TargetPlanError("新版本生效日期必须晚于旧版本")
        previous.status = "RETIRED"
        previous.retired_at = activated_at
        previous.effective_to = plan.effective_from - timedelta(days=1)

    conflicting = list(
        session.scalars(
            select(TargetPlan)
            .where(
                TargetPlan.id != plan.id,
                TargetPlan.scenario == plan.scenario,
                TargetPlan.period_type == plan.period_type,
                TargetPlan.status.in_(["ACTIVE", "RETIRED"]),
                TargetPlan.priority == plan.priority,
                TargetPlan.effective_from == plan.effective_from,
            )
            .with_for_update()
        )
    )
    if conflicting:
        raise AmbiguousTargetPlanError(
            f"目标方案优先级和生效日期冲突: "
            f"ids={[item.id for item in conflicting]}"
        )
    plan.status = "ACTIVE"
    plan.activated_at = activated_at
    session.flush()
    return plan
