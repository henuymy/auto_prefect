"""Indicator catalogue and custom formula helpers for Dashboard V2."""

from __future__ import annotations

from decimal import Decimal
from datetime import datetime
from typing import Any

from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from models.dashboard_v2 import IndicatorFormulaComponent, IndicatorV2


def load_v2_metric_indicator_plan(engine: Engine) -> dict[str, Any]:
    """Return source request codes, STORE codes, and custom components."""
    with Session(engine) as session:
        store_indicators = list(
            session.scalars(
                select(IndicatorV2)
                .where(
                    IndicatorV2.enabled.is_(True),
                    IndicatorV2.storage_mode == "STORE",
                )
                .order_by(IndicatorV2.sort_order, IndicatorV2.id)
            )
        )
        by_id = {indicator.id: indicator for indicator in store_indicators}
        store_codes = [indicator.code for indicator in store_indicators]
        component_rows = session.execute(
            select(
                IndicatorFormulaComponent.custom_indicator_id,
                IndicatorV2.code,
                IndicatorFormulaComponent.coefficient,
            )
            .join(
                IndicatorV2,
                IndicatorV2.id == IndicatorFormulaComponent.source_indicator_id,
            )
            .where(
                IndicatorFormulaComponent.custom_indicator_id.in_(
                    list(by_id) or [-1]
                )
            )
            .order_by(IndicatorFormulaComponent.id)
        ).all()

    custom_ids = {row.custom_indicator_id for row in component_rows}
    request_codes = [
        indicator.code
        for indicator in store_indicators
        if indicator.indicator_type == "SOURCE" and indicator.id not in custom_ids
    ]
    custom_components: dict[str, list[dict[str, Any]]] = {}
    for row in component_rows:
        custom = by_id.get(row.custom_indicator_id)
        if custom is None:
            continue
        custom_components.setdefault(custom.code, []).append(
            {
                "source_code": row.code,
                "coefficient": Decimal(str(row.coefficient)),
            }
        )
        if row.code not in request_codes:
            request_codes.append(row.code)
    return {
        "request_codes": request_codes,
        "store_codes": store_codes,
        "custom_components": custom_components,
    }


def sync_v2_indicator_records(
    session: Session,
    records: list[tuple[str, str, int]],
) -> dict[str, Any]:
    """Upsert source indicators while retaining V2 enablement and formulas."""
    if not records:
        raise ValueError("指标清单为空，拒绝归档现有指标")
    codes = [code for code, _name, _order in records]
    existing = {
        row.code: row
        for row in session.scalars(
            select(IndicatorV2).where(IndicatorV2.code.in_(codes))
        )
    }
    max_sort = (
        session.scalar(
            select(IndicatorV2.sort_order)
            .order_by(IndicatorV2.sort_order.desc())
            .limit(1)
        )
        or 0
    )
    inserted: list[str] = []
    updated: list[str] = []
    unchanged: list[str] = []
    for code, name, source_order in records:
        indicator = existing.get(code)
        if indicator is None:
            session.add(
                IndicatorV2(
                    code=code,
                    name=name,
                    indicator_type="SOURCE",
                    storage_mode="STORE",
                    enabled=False,
                    source_active=True,
                    sort_order=int(max_sort) + source_order,
                )
            )
            inserted.append(code)
            continue
        changed = False
        if indicator.name != name:
            indicator.name = name
            changed = True
        if indicator.sort_order != source_order:
            indicator.sort_order = source_order
            changed = True
        if indicator.indicator_type == "SOURCE" and not indicator.source_active:
            indicator.source_active = True
            changed = True
        if indicator.removed_at is not None:
            indicator.removed_at = None
            changed = True
        (updated if changed else unchanged).append(code)

    archived: list[str] = []
    archived_at = datetime.now()
    for indicator in session.scalars(
        select(IndicatorV2).where(
            IndicatorV2.indicator_type == "SOURCE",
            IndicatorV2.source_active.is_(True),
            IndicatorV2.enabled.is_(False),
            IndicatorV2.code.not_in(codes),
        )
    ):
        indicator.source_active = False
        indicator.removed_at = archived_at
        archived.append(indicator.code)
    session.flush()
    total = int(session.scalar(select(func.count()).select_from(IndicatorV2)) or 0)
    enabled = int(
        session.scalar(
            select(func.count())
            .select_from(IndicatorV2)
            .where(IndicatorV2.enabled.is_(True))
        )
        or 0
    )
    source_active = int(
        session.scalar(
            select(func.count())
            .select_from(IndicatorV2)
            .where(IndicatorV2.source_active.is_(True))
        )
        or 0
    )
    return {
        "source_count": len(records),
        "inserted_count": len(inserted),
        "updated_count": len(updated),
        "unchanged_count": len(unchanged),
        "archived_missing_count": len(archived),
        "indicator_total": total,
        "indicator_enabled": enabled,
        "indicator_source_active": source_active,
        "indicator_archived": total - source_active,
        "inserted_codes": inserted[:50],
        "updated_codes": updated[:50],
        "archived_missing_codes": archived[:50],
    }
