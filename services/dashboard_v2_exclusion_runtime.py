"""Runtime helpers for channel-by-indicator exclusion rules.

The source metric tables remain the record of the upstream response.  This
module derives a small override layer for the business-facing dashboard value.
Only linear add/subtract indicators are supported by the current product
contract.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from hashlib import sha256
from typing import Any, Iterable

from sqlalchemy import delete, or_, select
from sqlalchemy.orm import Session

from models.dashboard_v2 import (
    ChannelIndicatorExclusion,
    HierarchyNode,
    IndicatorV2,
    MetricCaliberOverride,
)
from services.dashboard_metrics import METRIC_VALUE_QUANT, parse_metric_value


def active_channel_indicator_exclusions_in_session(
    session: Session,
    *,
    business_date: date,
    channel_node_ids: Iterable[int] | None = None,
    indicator_ids: Iterable[int] | None = None,
) -> list[ChannelIndicatorExclusion]:
    """Load active rules that apply to one business date."""
    channel_ids = list(dict.fromkeys(channel_node_ids or ()))
    indicators = list(dict.fromkeys(indicator_ids or ()))
    stmt = select(ChannelIndicatorExclusion).where(
        ChannelIndicatorExclusion.status == "ACTIVE",
        ChannelIndicatorExclusion.effective_from <= business_date,
        or_(
            ChannelIndicatorExclusion.effective_to.is_(None),
            ChannelIndicatorExclusion.effective_to >= business_date,
        ),
    )
    if channel_ids:
        stmt = stmt.where(ChannelIndicatorExclusion.channel_node_id.in_(channel_ids))
    if indicators:
        stmt = stmt.where(ChannelIndicatorExclusion.indicator_id.in_(indicators))
    return list(session.scalars(stmt.order_by(ChannelIndicatorExclusion.id)))


def active_exclusion_pairs_in_session(
    session: Session,
    *,
    business_date: date,
    channel_node_ids: Iterable[int],
    indicator_ids: Iterable[int],
) -> set[tuple[int, int]]:
    """Return direct channel/indicator pairs excluded for the supplied date."""
    return {
        (rule.channel_node_id, rule.indicator_id)
        for rule in active_channel_indicator_exclusions_in_session(
            session,
            business_date=business_date,
            channel_node_ids=channel_node_ids,
            indicator_ids=indicator_ids,
        )
    }


def metric_caliber_overrides_in_session(
    session: Session,
    *,
    collection_run_ids: Iterable[int | None],
    node_ids: Iterable[int],
    indicator_ids: Iterable[int],
) -> dict[tuple[int, int, int], MetricCaliberOverride]:
    """Load exact-run overrides for raw metric rows returned by a query."""
    runs = sorted({int(value) for value in collection_run_ids if value is not None})
    nodes = sorted({int(value) for value in node_ids})
    indicators = sorted({int(value) for value in indicator_ids})
    if not runs or not nodes or not indicators:
        return {}
    rows = session.scalars(
        select(MetricCaliberOverride).where(
            MetricCaliberOverride.collection_run_id.in_(runs),
            MetricCaliberOverride.node_id.in_(nodes),
            MetricCaliberOverride.indicator_id.in_(indicators),
        )
    )
    return {
        (int(row.collection_run_id), row.node_id, row.indicator_id): row
        for row in rows
        if row.collection_run_id is not None
    }


def write_channel_indicator_exclusion_overrides_in_session(
    session: Session,
    *,
    collection_run_id: int,
    business_date: date,
    collected_at: datetime,
    rows: list[dict[str, Any]],
    store_codes: Iterable[str],
    custom_components: dict[str, list[dict[str, Any]]],
) -> dict[str, int | str | None]:
    """Persist direct exclusions and ancestor deductions for one collected batch.

    ``rows`` contains every validated hierarchy node, including channels.  The
    source response is never mutated; override values are calculated from it.
    """
    codes = list(dict.fromkeys(store_codes))
    if not rows or not codes:
        return _empty_override_stats()

    indicators = {
        indicator.code: indicator
        for indicator in session.scalars(
            select(IndicatorV2).where(IndicatorV2.code.in_(codes))
        )
    }
    if set(codes) - set(indicators):
        missing = ", ".join(sorted(set(codes) - set(indicators)))
        raise ValueError(f"排除口径指标不存在: {missing}")
    code_by_indicator_id = {indicator.id: code for code, indicator in indicators.items()}

    row_by_node_id = {
        int(row["node_id"]): row
        for row in rows
        if isinstance(row.get("node_id"), int)
    }
    node_ids = sorted(row_by_node_id)
    nodes = {
        node.id: node
        for node in session.scalars(
            select(HierarchyNode).where(HierarchyNode.id.in_(node_ids))
        )
    }
    channel_ids = [
        node_id for node_id, node in nodes.items() if node.node_type == "CHANNEL"
    ]
    rules = active_channel_indicator_exclusions_in_session(
        session,
        business_date=business_date,
        channel_node_ids=channel_ids,
        indicator_ids=code_by_indicator_id,
    )
    session.execute(
        delete(MetricCaliberOverride).where(
            MetricCaliberOverride.collection_run_id == collection_run_id
        )
    )
    if not rules:
        return _empty_override_stats()

    parents = {node_id: node.parent_id for node_id, node in nodes.items()}
    applicable_rules: list[tuple[ChannelIndicatorExclusion, str, dict[str, Any]]] = []
    direct_exclusions: set[tuple[int, str]] = set()
    seen_direct_pairs: set[tuple[int, str]] = set()
    for rule in rules:
        code = code_by_indicator_id.get(rule.indicator_id)
        row = row_by_node_id.get(rule.channel_node_id)
        node = nodes.get(rule.channel_node_id)
        if code is None or row is None or node is None or node.node_type != "CHANNEL":
            continue
        pair = (rule.channel_node_id, code)
        if pair in seen_direct_pairs:
            continue
        seen_direct_pairs.add(pair)
        direct_exclusions.add(pair)
        applicable_rules.append((rule, code, row))

    deductions: dict[tuple[int, str], Decimal] = defaultdict(lambda: Decimal("0"))
    fingerprint = _rule_fingerprint(rules, business_date)
    dependents = _linear_dependents(custom_components, set(codes))

    for rule, code, row in applicable_rules:
        value = parse_metric_value(row.get(code))
        for affected_code, coefficient in _linear_impact(code, dependents).items():
            # A direct custom-indicator exclusion supplies its complete
            # upstream deduction, so a source indicator must not deduct that
            # same custom value a second time.
            if (
                affected_code != code
                and (rule.channel_node_id, affected_code) in direct_exclusions
            ):
                continue
            contribution = value * coefficient
            if affected_code != code:
                deductions[(rule.channel_node_id, affected_code)] += contribution
            for ancestor_id in _ancestor_ids(rule.channel_node_id, parents):
                deductions[(ancestor_id, affected_code)] += contribution

    overrides: list[MetricCaliberOverride] = []
    for node_id, code in sorted(direct_exclusions):
        overrides.append(
            MetricCaliberOverride(
                collection_run_id=collection_run_id,
                node_id=node_id,
                indicator_id=indicators[code].id,
                metric_value=None,
                value_state="EXCLUDED",
                calculation_type="EXCLUSION_ROLLUP",
                rule_fingerprint=fingerprint,
                created_at=collected_at,
            )
        )
    for (node_id, code), deduction in sorted(deductions.items()):
        if (node_id, code) in direct_exclusions or deduction == 0:
            continue
        source = row_by_node_id.get(node_id)
        if source is None:
            continue
        adjusted = _quantize(parse_metric_value(source.get(code)) - deduction)
        overrides.append(
            MetricCaliberOverride(
                collection_run_id=collection_run_id,
                node_id=node_id,
                indicator_id=indicators[code].id,
                metric_value=adjusted,
                value_state="VALUE",
                calculation_type="EXCLUSION_ROLLUP",
                rule_fingerprint=fingerprint,
                created_at=collected_at,
            )
        )
    session.add_all(overrides)
    return {
        "rule_count": len(rules),
        "excluded_cell_count": len(direct_exclusions),
        "adjusted_cell_count": len(overrides) - len(direct_exclusions),
        "override_count": len(overrides),
        "rule_fingerprint": fingerprint,
    }


def _empty_override_stats() -> dict[str, int | str | None]:
    return {
        "rule_count": 0,
        "excluded_cell_count": 0,
        "adjusted_cell_count": 0,
        "override_count": 0,
        "rule_fingerprint": None,
    }


def _ancestor_ids(
    node_id: int,
    parents: dict[int, int | None],
) -> list[int]:
    result: list[int] = []
    seen = {node_id}
    parent_id = parents.get(node_id)
    while parent_id is not None:
        if parent_id in seen:
            raise ValueError(f"层级树存在循环: node_id={node_id}")
        seen.add(parent_id)
        result.append(parent_id)
        parent_id = parents.get(parent_id)
    return result


def _linear_dependents(
    custom_components: dict[str, list[dict[str, Any]]],
    allowed_codes: set[str],
) -> dict[str, list[tuple[str, Decimal]]]:
    reverse: dict[str, list[tuple[str, Decimal]]] = defaultdict(list)
    for custom_code, components in custom_components.items():
        if custom_code not in allowed_codes:
            continue
        for component in components:
            source_code = str(component.get("source_code") or "").strip()
            if source_code not in allowed_codes:
                continue
            coefficient = Decimal(str(component.get("coefficient", 1)))
            reverse[source_code].append((custom_code, coefficient))
    return dict(reverse)


def _linear_impact(
    code: str,
    dependents: dict[str, list[tuple[str, Decimal]]],
) -> dict[str, Decimal]:
    """Return the linear contribution of one code to itself and dependents."""
    result: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    result[code] = Decimal("1")

    def visit(current: str, coefficient: Decimal, path: set[str]) -> None:
        for dependent, direct_coefficient in dependents.get(current, []):
            if dependent in path:
                raise ValueError(f"自定义指标存在循环依赖: {dependent}")
            total = coefficient * direct_coefficient
            result[dependent] += total
            visit(dependent, total, path | {dependent})

    visit(code, Decimal("1"), {code})
    return dict(result)


def _rule_fingerprint(
    rules: Iterable[ChannelIndicatorExclusion], business_date: date
) -> str:
    payload = ";".join(
        f"{rule.id}:{rule.channel_node_id}:{rule.indicator_id}:"
        f"{rule.effective_from}:{rule.effective_to or ''}"
        for rule in rules
    )
    return sha256(f"{business_date.isoformat()}|{payload}".encode("utf-8")).hexdigest()


def _quantize(value: Decimal) -> Decimal:
    return value.quantize(METRIC_VALUE_QUANT, rounding=ROUND_HALF_UP)
