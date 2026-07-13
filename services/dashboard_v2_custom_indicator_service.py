"""Custom indicator CRUD backed by the Dashboard V2 schema."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Engine, delete, func, select
from sqlalchemy.orm import Session, aliased

from models.dashboard_v2 import IndicatorFormulaComponent, IndicatorV2
from services.dashboard_metrics import normalize_indicator_components


def _components_for(
    session: Session, custom_ids: list[int]
) -> dict[int, list[dict[str, Any]]]:
    if not custom_ids:
        return {}
    source = aliased(IndicatorV2)
    rows = session.execute(
        select(
            IndicatorFormulaComponent.custom_indicator_id,
            source.code,
            source.name,
            source.storage_mode,
            IndicatorFormulaComponent.coefficient,
        )
        .join(source, source.id == IndicatorFormulaComponent.source_indicator_id)
        .where(IndicatorFormulaComponent.custom_indicator_id.in_(custom_ids))
        .order_by(
            IndicatorFormulaComponent.sort_order,
            IndicatorFormulaComponent.id,
        )
    ).all()
    result: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        result.setdefault(row.custom_indicator_id, []).append(
            {
                "source_code": row.code,
                "source_name": row.name,
                "coefficient": float(row.coefficient),
                "source_storage_mode": row.storage_mode,
            }
        )
    return result


def _payload(
    indicator: IndicatorV2, components: list[dict[str, Any]]
) -> dict[str, Any]:
    return {
        "id": indicator.id,
        "code": indicator.code,
        "name": indicator.name,
        "enabled": indicator.enabled,
        "source_active": indicator.source_active,
        "indicator_type": indicator.indicator_type,
        "storage_mode": indicator.storage_mode,
        "sort_order": indicator.sort_order,
        "components": components,
    }


def list_custom_indicators(engine: Engine) -> dict[str, Any]:
    with Session(engine) as session:
        indicators = list(
            session.scalars(
                select(IndicatorV2)
                .where(IndicatorV2.indicator_type == "CUSTOM")
                .order_by(IndicatorV2.sort_order, IndicatorV2.id)
            )
        )
        components = _components_for(session, [row.id for row in indicators])
        return {
            "indicators": [
                _payload(row, components.get(row.id, [])) for row in indicators
            ]
        }


def upsert_custom_indicator(
    engine: Engine,
    *,
    code: str,
    name: str,
    components: list[dict[str, Any]],
    enabled: bool = True,
    sort_order: int | None = None,
) -> dict[str, Any]:
    normalized_code = code.strip()
    normalized_name = name.strip()
    if not normalized_code:
        raise ValueError("自建指标编码不能为空")
    if not normalized_name:
        raise ValueError("自建指标名称不能为空")
    normalized_components = normalize_indicator_components(components)
    if not normalized_components:
        raise ValueError("自建指标至少需要 1 个源指标")
    with Session(engine) as session:
        source_codes = [row["source_code"] for row in normalized_components]
        sources = list(
            session.scalars(
                select(IndicatorV2).where(IndicatorV2.code.in_(source_codes))
            )
        )
        source_by_code = {row.code: row for row in sources}
        missing = [code for code in source_codes if code not in source_by_code]
        if missing:
            raise ValueError(f"源指标不存在: {', '.join(missing)}")
        invalid = [row.code for row in sources if row.indicator_type != "SOURCE"]
        if invalid:
            raise ValueError(f"组成指标必须是源指标: {', '.join(invalid)}")
        if normalized_code in source_by_code:
            raise ValueError("自建指标不能引用自身")
        indicator = session.scalar(
            select(IndicatorV2).where(IndicatorV2.code == normalized_code)
        )
        if indicator is not None and indicator.indicator_type != "CUSTOM":
            raise ValueError(f"指标编码已被源指标占用: {normalized_code}")
        if indicator is None:
            resolved_order = sort_order
            if resolved_order is None:
                resolved_order = int(
                    session.scalar(select(func.max(IndicatorV2.sort_order))) or 0
                ) + 10
            indicator = IndicatorV2(
                code=normalized_code,
                name=normalized_name,
                indicator_type="CUSTOM",
                storage_mode="STORE",
                enabled=enabled,
                source_active=False,
                sort_order=resolved_order,
            )
            session.add(indicator)
            session.flush()
        else:
            indicator.name = normalized_name
            indicator.enabled = enabled
            indicator.removed_at = None
            if sort_order is not None:
                indicator.sort_order = sort_order
        session.execute(
            delete(IndicatorFormulaComponent).where(
                IndicatorFormulaComponent.custom_indicator_id == indicator.id
            )
        )
        for index, item in enumerate(normalized_components):
            source = source_by_code[item["source_code"]]
            if item.get("source_storage_mode"):
                source.storage_mode = item["source_storage_mode"]
            session.add(
                IndicatorFormulaComponent(
                    custom_indicator_id=indicator.id,
                    source_indicator_id=source.id,
                    coefficient=item["coefficient"],
                    sort_order=index * 10,
                )
            )
        session.commit()
        session.refresh(indicator)
        parts = _components_for(session, [indicator.id]).get(indicator.id, [])
        return _payload(indicator, parts)


def delete_custom_indicator(engine: Engine, code: str) -> dict[str, Any]:
    """Archive instead of physically deleting immutable historical facts."""
    with Session(engine) as session:
        indicator = session.scalar(
            select(IndicatorV2).where(IndicatorV2.code == code)
        )
        if indicator is None or indicator.indicator_type != "CUSTOM":
            raise ValueError(f"自建指标不存在: {code}")
        indicator.enabled = False
        indicator.source_active = False
        indicator.removed_at = datetime.now()
        session.commit()
    return {"code": code, "deleted": True, "archived": True}


def update_indicator_settings(
    engine: Engine,
    code: str,
    *,
    enabled: bool | None = None,
    storage_mode: str | None = None,
) -> dict[str, Any]:
    normalized_storage = str(storage_mode or "").strip().upper() or None
    if normalized_storage and normalized_storage not in {"STORE", "COMPONENT"}:
        raise ValueError(f"storage_mode 只支持 STORE/COMPONENT: {storage_mode}")
    with Session(engine) as session:
        indicator = session.scalar(
            select(IndicatorV2).where(IndicatorV2.code == code)
        )
        if indicator is None:
            raise ValueError(f"指标不存在: {code}")
        if indicator.indicator_type != "SOURCE":
            raise ValueError("该接口只允许修改源指标设置")
        if enabled is not None:
            indicator.enabled = enabled
        if normalized_storage:
            indicator.storage_mode = normalized_storage
        session.commit()
        session.refresh(indicator)
        return _payload(indicator, [])
