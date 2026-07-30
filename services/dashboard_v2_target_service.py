"""Versioned NORMAL/PK and DAY/MONTH target plans for Dashboard V2."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Iterable

from sqlalchemy import delete, or_, select
from sqlalchemy.orm import Session

from models.dashboard_v2 import MetricTargetValue, TargetPlan
from services.dashboard_metrics import parse_metric_value


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
    """Resolve the published target plan for a business date.

    ``effective_from`` and ``effective_to`` define the business period that
    uses the target.  Historical completion rates are therefore resolved by
    the data's business date, not by the timestamp when the plan was published.
    """
    normalized_period = str(period_type or "").strip().upper()
    if normalized_period not in {"DAY", "MONTH"}:
        raise ValueError("period_type 只支持 DAY/MONTH")
    normalized_scenario = normalize_target_scenario(scenario)
    return session.scalar(
        select(TargetPlan)
        .where(
            TargetPlan.scenario == normalized_scenario,
            TargetPlan.period_type == normalized_period,
            TargetPlan.status.in_(("ACTIVE", "RETIRED")),
            TargetPlan.effective_from <= business_date,
            or_(
                TargetPlan.effective_to.is_(None),
                TargetPlan.effective_to >= business_date,
            ),
        )
        .order_by(
            TargetPlan.priority.desc(),
            TargetPlan.effective_from.desc(),
            TargetPlan.version_no.desc(),
            TargetPlan.id.desc(),
        )
        .limit(1)
    )


def load_v2_target_values(
    session: Session,
    *,
    business_date: date,
    period_type: str,
    scenario: str = "NORMAL",
    node_ids: Iterable[int] | None = None,
    indicator_ids: Iterable[int] | None = None,
) -> tuple[TargetPlan | None, dict[tuple[int, int], Decimal]]:
    """Return the published assessment plan for a business date."""
    plan = resolve_v2_target_plan(
        session,
        business_date=business_date,
        period_type=period_type,
        scenario=scenario,
    )
    return plan, _target_plan_values(
        session,
        plan=plan,
        node_ids=node_ids,
        indicator_ids=indicator_ids,
    )


def resolve_v2_working_target_plan(
    session: Session,
    *,
    business_date: date,
    period_type: str,
    scenario: str = "NORMAL",
) -> TargetPlan | None:
    """Resolve the editable target used by live, unclosed-period views."""
    normalized_period = str(period_type or "").strip().upper()
    if normalized_period not in {"DAY", "MONTH"}:
        raise ValueError("period_type 只支持 DAY/MONTH")
    normalized_scenario = normalize_target_scenario(scenario)
    return session.scalar(
        select(TargetPlan)
        .where(
            TargetPlan.scenario == normalized_scenario,
            TargetPlan.period_type == normalized_period,
            TargetPlan.status == "DRAFT",
            TargetPlan.is_realtime.is_(True),
            TargetPlan.effective_from <= business_date,
            or_(
                TargetPlan.effective_to.is_(None),
                TargetPlan.effective_to >= business_date,
            ),
        )
        .order_by(
            TargetPlan.priority.desc(),
            TargetPlan.effective_from.desc(),
            TargetPlan.version_no.desc(),
            TargetPlan.id.desc(),
        )
        .limit(1)
    )


def load_v2_working_target_values(
    session: Session,
    *,
    business_date: date,
    period_type: str,
    scenario: str = "NORMAL",
    node_ids: Iterable[int] | None = None,
    indicator_ids: Iterable[int] | None = None,
) -> tuple[TargetPlan | None, dict[tuple[int, int], Decimal]]:
    """Return the mutable target used by live, unclosed-period views."""
    plan = resolve_v2_working_target_plan(
        session,
        business_date=business_date,
        period_type=period_type,
        scenario=scenario,
    )
    if plan is None:
        # Existing installations may not yet have a selected realtime draft.
        # Keep current boards usable until the operator selects one.
        return load_v2_target_values(
            session,
            business_date=business_date,
            period_type=period_type,
            scenario=scenario,
            node_ids=node_ids,
            indicator_ids=indicator_ids,
        )
    return plan, _target_plan_values(
        session,
        plan=plan,
        node_ids=node_ids,
        indicator_ids=indicator_ids,
    )


def _target_plan_values(
    session: Session,
    *,
    plan: TargetPlan | None,
    node_ids: Iterable[int] | None,
    indicator_ids: Iterable[int] | None,
) -> dict[tuple[int, int], Decimal]:
    if plan is None:
        return {}
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
    return {
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
    plan.updated_at = datetime.now()
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
    latest_version = session.scalar(
        select(TargetPlan.version_no)
        .where(
            TargetPlan.scenario == source.scenario,
            TargetPlan.period_type == source.period_type,
            TargetPlan.plan_name == source.plan_name,
            TargetPlan.status == "DRAFT",
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
        is_realtime=False,
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
    """Publish an immutable copy of a DRAFT and preserve that DRAFT.

    The source DRAFT remains the mutable target used by live views. The
    published copy is the immutable assessment version used by historical and
    settled-period views.
    """
    source = session.scalar(
        select(TargetPlan).where(TargetPlan.id == plan_id).with_for_update()
    )
    if source is None:
        raise TargetPlanError(f"目标方案不存在: {plan_id}")
    if source.status != "DRAFT":
        raise TargetPlanError("只有 DRAFT 目标方案允许发布")
    source_values = session.scalars(
        select(MetricTargetValue).where(MetricTargetValue.plan_id == source.id)
    ).all()
    if not source_values:
        raise TargetPlanError("空目标方案不允许激活")

    published_plans = list(
        session.scalars(
            select(TargetPlan)
            .where(
                TargetPlan.scenario == source.scenario,
                TargetPlan.period_type == source.period_type,
                TargetPlan.plan_name == source.plan_name,
                TargetPlan.status.in_(("ACTIVE", "RETIRED")),
            )
            .with_for_update()
        )
    )
    published = TargetPlan(
        plan_name=source.plan_name,
        scenario=source.scenario,
        period_type=source.period_type,
        effective_from=source.effective_from,
        effective_to=source.effective_to,
        priority=source.priority,
        version_no=max((plan.version_no for plan in published_plans), default=0) + 1,
        status="ACTIVE",
        is_realtime=False,
        supersedes_plan_id=(
            max(published_plans, key=lambda plan: (plan.version_no, plan.id)).id
            if published_plans
            else None
        ),
        activated_at=activated_at,
    )
    session.add(published)
    session.flush()
    session.add_all(
        MetricTargetValue(
            plan_id=published.id,
            node_id=value.node_id,
            indicator_id=value.indicator_id,
            target_value=value.target_value,
        )
        for value in source_values
    )

    timeline_plans = list(
        session.scalars(
            select(TargetPlan)
            .where(
                TargetPlan.id != published.id,
                TargetPlan.scenario == published.scenario,
                TargetPlan.period_type == published.period_type,
                TargetPlan.status.in_(("ACTIVE", "RETIRED")),
            )
            .with_for_update()
        )
    )
    previous_period_end = published.effective_from - timedelta(days=1)
    for previous in timeline_plans:
        if (
            previous.effective_from < published.effective_from
            and (previous.effective_to is None or previous.effective_to >= published.effective_from)
        ):
            previous.effective_to = previous_period_end
            previous.status = "RETIRED"
            previous.retired_at = activated_at
        elif previous.effective_from == published.effective_from and previous.status == "ACTIVE":
            # A corrected version may intentionally use the same business
            # start date. Keep the old range for audit, but let the newer
            # version win through the resolver's version ordering.
            previous.status = "RETIRED"
            previous.retired_at = activated_at

    next_effective_from = min(
        (
            previous.effective_from
            for previous in timeline_plans
            if previous.effective_from > published.effective_from
        ),
        default=None,
    )
    published.effective_to = (
        next_effective_from - timedelta(days=1)
        if next_effective_from is not None
        else None
    )
    published.retired_at = None
    session.flush()
    return published
