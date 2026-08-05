"""Administrative helpers for channel-by-indicator exclusion rules.

Rules are deliberately kept separate from the hierarchy and indicator flags.
The source data and the complete hierarchy remain available to collection code;
consumers use an effective rule set to decide which channel metrics contribute
to the published caliber.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import Engine, and_, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from models.dashboard_v2 import (
    ChannelIndicatorExclusion,
    HierarchyNode,
    IndicatorV2,
)


VALID_EXCLUSION_STATUSES = {"ACTIVE", "CANCELLED"}


def _date_to_iso(value: date | None) -> str | None:
    return value.isoformat() if value is not None else None


def _time_to_iso(value: object) -> str | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()  # type: ignore[no-any-return]
    return str(value)


def _normalize_status(value: str | None, *, default: str = "ACTIVE") -> str:
    normalized = str(value or default).strip().upper()
    if normalized not in VALID_EXCLUSION_STATUSES:
        raise ValueError("status 只支持 ACTIVE/CANCELLED")
    return normalized


def _normalize_reason(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    if len(normalized) > 500:
        raise ValueError("reason 最长 500 个字符")
    return normalized or None


def _normalize_created_by(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    if len(normalized) > 100:
        raise ValueError("created_by 最长 100 个字符")
    return normalized or None


def _validate_effective_dates(
    effective_from: date,
    effective_to: date | None,
) -> None:
    if effective_to is not None and effective_to < effective_from:
        raise ValueError("effective_to 不能早于 effective_from")


def _serialize_rule(
    rule: ChannelIndicatorExclusion,
    *,
    node: HierarchyNode | None = None,
    indicator: IndicatorV2 | None = None,
) -> dict[str, Any]:
    """Return a UI-ready rule, enriched when the related rows are available."""
    return {
        "id": rule.id,
        "channel_node_id": rule.channel_node_id,
        "channel_node_code": node.node_code if node is not None else None,
        "channel_node_name": node.node_name if node is not None else None,
        "indicator_id": rule.indicator_id,
        "indicator_code": indicator.code if indicator is not None else None,
        "indicator_name": indicator.name if indicator is not None else None,
        "effective_from": _date_to_iso(rule.effective_from),
        "effective_to": _date_to_iso(rule.effective_to),
        "status": rule.status,
        "reason": rule.reason,
        "created_by": getattr(rule, "created_by", None),
        "created_at": _time_to_iso(rule.created_at),
        "updated_at": _time_to_iso(rule.updated_at),
    }


def _validate_node_and_indicator(
    session: Session,
    *,
    channel_node_id: int,
    indicator_id: int,
) -> tuple[HierarchyNode, IndicatorV2]:
    node = session.get(HierarchyNode, channel_node_id)
    if node is None:
        raise ValueError(f"渠道节点不存在: {channel_node_id}")
    if node.node_type != "CHANNEL":
        raise ValueError("排除规则的节点必须是 CHANNEL 类型")

    indicator = session.get(IndicatorV2, indicator_id)
    if indicator is None:
        raise ValueError(f"指标不存在: {indicator_id}")
    if not indicator.enabled or indicator.storage_mode != "STORE":
        raise ValueError("排除规则的指标必须是已启用且 storage_mode 为 STORE 的指标")
    return node, indicator


def _effective_rule_predicate(business_date: date):
    return and_(
        ChannelIndicatorExclusion.effective_from <= business_date,
        or_(
            ChannelIndicatorExclusion.effective_to.is_(None),
            ChannelIndicatorExclusion.effective_to >= business_date,
        ),
    )


def _find_active_overlaps(
    session: Session,
    *,
    channel_node_id: int,
    indicator_id: int,
    effective_from: date,
    effective_to: date | None,
    exclude_id: int | None = None,
) -> list[ChannelIndicatorExclusion]:
    """Find inclusive date-range intersections among active rules."""
    query = select(ChannelIndicatorExclusion).where(
        ChannelIndicatorExclusion.channel_node_id == channel_node_id,
        ChannelIndicatorExclusion.indicator_id == indicator_id,
        ChannelIndicatorExclusion.status == "ACTIVE",
        or_(
            ChannelIndicatorExclusion.effective_to.is_(None),
            ChannelIndicatorExclusion.effective_to >= effective_from,
        ),
    )
    if effective_to is not None:
        query = query.where(
            ChannelIndicatorExclusion.effective_from <= effective_to
        )
    if exclude_id is not None:
        query = query.where(ChannelIndicatorExclusion.id != exclude_id)
    # The matching unique index starts with channel/indicator/date. Locking the
    # range prevents concurrent administrative saves from passing the overlap
    # check at the same time.
    return list(session.scalars(query.with_for_update()))


def _validate_candidate(
    session: Session,
    *,
    channel_node_id: int,
    indicator_id: int,
    effective_from: date,
    effective_to: date | None,
    status: str,
    exclude_id: int | None = None,
) -> tuple[HierarchyNode, IndicatorV2]:
    _validate_effective_dates(effective_from, effective_to)
    node, indicator = _validate_node_and_indicator(
        session,
        channel_node_id=channel_node_id,
        indicator_id=indicator_id,
    )
    if status == "ACTIVE":
        overlaps = _find_active_overlaps(
            session,
            channel_node_id=channel_node_id,
            indicator_id=indicator_id,
            effective_from=effective_from,
            effective_to=effective_to,
            exclude_id=exclude_id,
        )
        if overlaps:
            raise ValueError("同一渠道和指标的有效排除规则日期范围不能重叠")
    return node, indicator


def _commit_rule(session: Session) -> None:
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise ValueError("排除规则保存失败：同一渠道、指标和生效日期已存在规则") from exc


def list_channel_indicator_exclusions(
    engine: Engine,
    *,
    status: str | None = None,
    active_on: date | None = None,
    channel_node_id: int | None = None,
    indicator_id: int | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """List rules, including archived/cancelled rules unless filtered out."""
    normalized_status = None
    if status is not None and str(status).strip():
        normalized_status = _normalize_status(status)

    with Session(engine) as session:
        query = select(ChannelIndicatorExclusion)
        if normalized_status:
            query = query.where(ChannelIndicatorExclusion.status == normalized_status)
        if active_on is not None:
            query = query.where(
                ChannelIndicatorExclusion.status == "ACTIVE",
                _effective_rule_predicate(active_on),
            )
        if channel_node_id is not None:
            query = query.where(
                ChannelIndicatorExclusion.channel_node_id == channel_node_id
            )
        if indicator_id is not None:
            query = query.where(
                ChannelIndicatorExclusion.indicator_id == indicator_id
            )
        rules = list(
            session.scalars(
                query.order_by(
                    ChannelIndicatorExclusion.effective_from.desc(),
                    ChannelIndicatorExclusion.id.desc(),
                )
            )
        )
        node_ids = {rule.channel_node_id for rule in rules}
        indicator_ids = {rule.indicator_id for rule in rules}
        nodes = {
            row.id: row
            for row in session.scalars(
                select(HierarchyNode).where(HierarchyNode.id.in_(node_ids))
            )
        } if node_ids else {}
        indicators = {
            row.id: row
            for row in session.scalars(
                select(IndicatorV2).where(IndicatorV2.id.in_(indicator_ids))
            )
        } if indicator_ids else {}
        return {
            "exclusions": [
                _serialize_rule(
                    rule,
                    node=nodes.get(rule.channel_node_id),
                    indicator=indicators.get(rule.indicator_id),
                )
                for rule in rules
            ]
        }


def create_channel_indicator_exclusion(
    engine: Engine,
    *,
    channel_node_id: int,
    indicator_id: int,
    effective_from: date,
    effective_to: date | None = None,
    reason: str | None = None,
    status: str = "ACTIVE",
    created_by: str | None = None,
) -> dict[str, Any]:
    normalized_status = _normalize_status(status)
    normalized_reason = _normalize_reason(reason)
    normalized_created_by = _normalize_created_by(created_by)
    with Session(engine) as session:
        node, indicator = _validate_candidate(
            session,
            channel_node_id=channel_node_id,
            indicator_id=indicator_id,
            effective_from=effective_from,
            effective_to=effective_to,
            status=normalized_status,
        )
        rule = ChannelIndicatorExclusion(
            channel_node_id=channel_node_id,
            indicator_id=indicator_id,
            effective_from=effective_from,
            effective_to=effective_to,
            status=normalized_status,
            reason=normalized_reason,
            created_by=normalized_created_by,
        )
        session.add(rule)
        _commit_rule(session)
        session.refresh(rule)
        return _serialize_rule(rule, node=node, indicator=indicator)


def update_channel_indicator_exclusion(
    engine: Engine,
    exclusion_id: int,
    **changes: Any,
) -> dict[str, Any]:
    """Update a rule, allowing an explicit null ``effective_to`` or reason."""
    allowed = {
        "channel_node_id",
        "indicator_id",
        "effective_from",
        "effective_to",
        "reason",
        "status",
        "created_by",
    }
    unexpected = set(changes) - allowed
    if unexpected:
        raise ValueError(f"不支持的排除规则字段: {', '.join(sorted(unexpected))}")
    if not changes:
        raise ValueError("至少需要提供一个待更新字段")

    with Session(engine) as session:
        rule = session.scalar(
            select(ChannelIndicatorExclusion)
            .where(ChannelIndicatorExclusion.id == exclusion_id)
            .with_for_update()
        )
        if rule is None:
            raise ValueError(f"排除规则不存在: {exclusion_id}")

        channel_node_id = int(changes.get("channel_node_id", rule.channel_node_id))
        indicator_id = int(changes.get("indicator_id", rule.indicator_id))
        effective_from = changes.get("effective_from", rule.effective_from)
        effective_to = changes.get("effective_to", rule.effective_to)
        if not isinstance(effective_from, date):
            raise ValueError("effective_from 必须是日期")
        if effective_to is not None and not isinstance(effective_to, date):
            raise ValueError("effective_to 必须是日期或 null")
        status = _normalize_status(changes.get("status", rule.status))
        reason = _normalize_reason(changes.get("reason", rule.reason))
        created_by = _normalize_created_by(
            changes.get("created_by", getattr(rule, "created_by", None))
        )
        node, indicator = _validate_candidate(
            session,
            channel_node_id=channel_node_id,
            indicator_id=indicator_id,
            effective_from=effective_from,
            effective_to=effective_to,
            status=status,
            exclude_id=rule.id,
        )
        rule.channel_node_id = channel_node_id
        rule.indicator_id = indicator_id
        rule.effective_from = effective_from
        rule.effective_to = effective_to
        rule.status = status
        rule.reason = reason
        rule.created_by = created_by
        _commit_rule(session)
        session.refresh(rule)
        return _serialize_rule(rule, node=node, indicator=indicator)


def cancel_channel_indicator_exclusion(
    engine: Engine,
    exclusion_id: int,
) -> dict[str, Any]:
    """Cancel instead of deleting the audit record physically."""
    with Session(engine) as session:
        rule = session.scalar(
            select(ChannelIndicatorExclusion)
            .where(ChannelIndicatorExclusion.id == exclusion_id)
            .with_for_update()
        )
        if rule is None:
            raise ValueError(f"排除规则不存在: {exclusion_id}")
        rule.status = "CANCELLED"
        _commit_rule(session)
        return {"id": exclusion_id, "deleted": True, "status": "CANCELLED"}


def preview_channel_indicator_exclusion(
    engine: Engine,
    *,
    channel_node_id: int,
    indicator_id: int,
    effective_from: date,
    effective_to: date | None = None,
    reason: str | None = None,
    status: str = "ACTIVE",
) -> dict[str, Any]:
    """Validate a prospective rule and return its channel-to-city impact path."""
    normalized_status = _normalize_status(status)
    normalized_reason = _normalize_reason(reason)
    with Session(engine) as session:
        node, indicator = _validate_candidate(
            session,
            channel_node_id=channel_node_id,
            indicator_id=indicator_id,
            effective_from=effective_from,
            effective_to=effective_to,
            status=normalized_status,
        )
        nodes_by_id = {
            row.id: row for row in session.scalars(select(HierarchyNode))
        }
        affected_nodes: list[dict[str, Any]] = []
        current: HierarchyNode | None = node
        visited: set[int] = set()
        distance = 0
        while current is not None and current.id not in visited:
            visited.add(current.id)
            affected_nodes.append(
                {
                    "node_id": current.id,
                    "node_type": current.node_type,
                    "node_code": current.node_code,
                    "node_name": current.node_name,
                    "distance": distance,
                }
            )
            current = nodes_by_id.get(current.parent_id)
            distance += 1
        preview_rule = {
            "channel_node_id": channel_node_id,
            "channel_node_code": node.node_code,
            "channel_node_name": node.node_name,
            "indicator_id": indicator_id,
            "indicator_code": indicator.code,
            "indicator_name": indicator.name,
            "effective_from": _date_to_iso(effective_from),
            "effective_to": _date_to_iso(effective_to),
            "status": normalized_status,
            "reason": normalized_reason,
        }
        return {
            "rule": preview_rule,
            "affected_nodes": affected_nodes,
            "affected_node_count": len(affected_nodes),
        }


def load_effective_channel_indicator_exclusions(
    session: Session,
    business_date: date,
) -> dict[tuple[int, int], ChannelIndicatorExclusion]:
    """Load active rules effective on ``business_date``, keyed by channel/indicator.

    The mapping works for both collection and query code: callers can use a
    membership check for a channel metric and retain the rule object when they
    need an audit ID or fingerprint.
    """
    if isinstance(business_date, datetime):
        business_date = business_date.date()
    rows = session.scalars(
        select(ChannelIndicatorExclusion)
        .where(
            ChannelIndicatorExclusion.status == "ACTIVE",
            _effective_rule_predicate(business_date),
        )
        .order_by(
            ChannelIndicatorExclusion.effective_from.desc(),
            ChannelIndicatorExclusion.id.desc(),
        )
    ).all()
    return {
        (int(rule.channel_node_id), int(rule.indicator_id)): rule
        for rule in rows
    }


__all__ = [
    "VALID_EXCLUSION_STATUSES",
    "cancel_channel_indicator_exclusion",
    "create_channel_indicator_exclusion",
    "list_channel_indicator_exclusions",
    "load_effective_channel_indicator_exclusions",
    "preview_channel_indicator_exclusion",
    "update_channel_indicator_exclusion",
]
