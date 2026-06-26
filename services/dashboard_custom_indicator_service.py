"""Manage dashboard custom indicators."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

from sqlalchemy import Engine, delete, func, inspect, select
from sqlalchemy.orm import Session

from models.dashboard_custom_indicator import CustomIndicatorComponent
from models.dashboard_indicator import Indicator
from models.dashboard_metric import MetricAcc, MetricCurrent, MetricSnapshot
from models.dashboard_metric_target import MetricTarget
from services.dashboard_metric_store import parse_metric_value

_METRIC_VALUE_QUANT = Decimal("0.0001")
_MAX_DECIMAL_ABS = Decimal("10000000000000000")


def list_custom_indicators(engine: Engine) -> dict[str, Any]:
    with Session(engine) as session:
        custom_ids = select(CustomIndicatorComponent.custom_indicator_id).distinct()
        indicators = list(
            session.scalars(
                select(Indicator)
                .where(Indicator.id.in_(custom_ids))
                .order_by(Indicator.sort_order, Indicator.id)
            )
        )
        components = _components_for(session, [indicator.id for indicator in indicators])

    return {
        "indicators": [
            _custom_payload(indicator, components.get(indicator.id, []))
            for indicator in indicators
        ]
    }


def load_metric_indicator_plan(engine: Engine) -> dict[str, Any]:
    """Return source codes to request and STORE indicators to persist/display."""
    with Session(engine) as session:
        has_component_table = inspect(session.get_bind()).has_table(
            "custom_indicator_component"
        )
        store_indicators = list(
            session.scalars(
                select(Indicator)
                .where(
                    Indicator.enabled.is_(True),
                    Indicator.storage_mode == "STORE",
                )
                .order_by(Indicator.sort_order, Indicator.id)
            )
        )
        store_ids = [indicator.id for indicator in store_indicators]
        store_codes = [indicator.code for indicator in store_indicators]
        custom_store_ids: set[int] = set()
        if has_component_table and store_ids:
            custom_store_ids = {
                int(custom_id)
                for custom_id in session.scalars(
                    select(CustomIndicatorComponent.custom_indicator_id)
                    .where(CustomIndicatorComponent.custom_indicator_id.in_(store_ids))
                    .distinct()
                )
            }
        source_store_codes = [
            indicator.code
            for indicator in store_indicators
            if indicator.indicator_type == "SOURCE"
            and indicator.id not in custom_store_ids
        ]
        if not has_component_table:
            return {
                "request_codes": source_store_codes,
                "store_codes": store_codes,
                "custom_components": {},
            }
        component_rows = session.execute(
            select(
                CustomIndicatorComponent.custom_indicator_id,
                Indicator.code,
                CustomIndicatorComponent.coefficient,
            )
            .join(Indicator, Indicator.id == CustomIndicatorComponent.source_indicator_id)
            .where(CustomIndicatorComponent.custom_indicator_id.in_(store_ids))
            .order_by(CustomIndicatorComponent.id)
        ).all()
        store_code_by_id = {
            indicator.id: indicator.code for indicator in store_indicators
        }

    custom_components: dict[str, list[dict[str, Any]]] = {}
    request_codes = list(source_store_codes)
    for row in component_rows:
        custom_code = store_code_by_id.get(row.custom_indicator_id)
        if not custom_code:
            continue
        custom_components.setdefault(custom_code, []).append(
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


def compose_store_metric_rows(
    rows: list[dict[str, Any]],
    custom_components: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    """Attach computed custom metric values to collected rows before writing."""
    if not custom_components:
        return rows
    composed: list[dict[str, Any]] = []
    for source_row in rows:
        row = dict(source_row)
        for custom_code, components in custom_components.items():
            total = Decimal("0")
            has_value = False
            for component in components:
                value = row.get(component["source_code"])
                if value is None:
                    continue
                text = str(value).strip().replace(",", "")
                if not text:
                    continue
                total += parse_metric_value(text) * component["coefficient"]
                has_value = True
            row[custom_code] = (
                total.quantize(_METRIC_VALUE_QUANT, rounding=ROUND_HALF_UP)
                if has_value
                else None
            )
        composed.append(row)
    return composed


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
    normalized_components = _normalize_components(components)
    if not normalized_components:
        raise ValueError("自建指标至少需要 1 个源指标")

    with Session(engine) as session:
        component_owner_ids = _component_owner_ids(session)
        source_rows = list(
            session.scalars(
                select(Indicator).where(
                    Indicator.code.in_([item["source_code"] for item in normalized_components])
                )
            )
        )
        source_by_code = {indicator.code: indicator for indicator in source_rows}
        missing = [
            item["source_code"]
            for item in normalized_components
            if item["source_code"] not in source_by_code
        ]
        if missing:
            raise ValueError(f"源指标不存在: {', '.join(missing)}")
        non_source = [
            indicator.code
            for indicator in source_rows
            if indicator.indicator_type != "SOURCE"
            or indicator.id in component_owner_ids
        ]
        if non_source:
            raise ValueError(f"组成指标必须是源指标: {', '.join(non_source)}")
        if normalized_code in source_by_code:
            raise ValueError("自建指标不能引用自身")

        indicator = session.scalar(
            select(Indicator).where(Indicator.code == normalized_code)
        )
        if indicator is None:
            resolved_sort_order = sort_order
            if resolved_sort_order is None:
                max_sort = session.scalar(select(func.max(Indicator.sort_order))) or 0
                resolved_sort_order = int(max_sort) + 10
            indicator = Indicator(
                code=normalized_code,
                name=normalized_name,
                enabled=enabled,
                source_active=False,
                indicator_type="CUSTOM",
                storage_mode="STORE",
                sort_order=resolved_sort_order,
            )
            session.add(indicator)
            session.flush()
        else:
            indicator.name = normalized_name
            indicator.enabled = enabled
            indicator.source_active = False
            indicator.indicator_type = "CUSTOM"
            indicator.storage_mode = "STORE"
            if sort_order is not None:
                indicator.sort_order = sort_order

        session.execute(
            delete(CustomIndicatorComponent).where(
                CustomIndicatorComponent.custom_indicator_id == indicator.id
            )
        )
        for item in normalized_components:
            source_indicator = source_by_code[item["source_code"]]
            if source_indicator.id not in component_owner_ids:
                source_indicator.indicator_type = "SOURCE"
            if item.get("source_storage_mode"):
                source_indicator.storage_mode = item["source_storage_mode"]
            session.add(
                CustomIndicatorComponent(
                    custom_indicator_id=indicator.id,
                    source_indicator_id=source_indicator.id,
                    coefficient=item["coefficient"],
                )
        )
        session.commit()
        session.refresh(indicator)
        component_payload = _components_for(session, [indicator.id]).get(indicator.id, [])
        return _custom_payload(indicator, component_payload)


def delete_custom_indicator(engine: Engine, code: str) -> dict[str, Any]:
    with Session(engine) as session:
        indicator = session.scalar(select(Indicator).where(Indicator.code == code))
        if indicator is None:
            raise ValueError(f"自建指标不存在: {code}")
        has_components = session.scalar(
            select(func.count(CustomIndicatorComponent.id)).where(
                CustomIndicatorComponent.custom_indicator_id == indicator.id
            )
        )
        if not has_components:
            raise ValueError(f"不是自建指标: {code}")
        dependent_count = session.scalar(
            select(func.count(CustomIndicatorComponent.id)).where(
                CustomIndicatorComponent.source_indicator_id == indicator.id
            )
        )
        if dependent_count:
            raise ValueError(f"自建指标仍被其他公式引用，不能删除: {code}")
        session.execute(
            delete(MetricTarget).where(MetricTarget.indicator_id == indicator.id)
        )
        session.execute(
            delete(MetricCurrent).where(MetricCurrent.indicator_id == indicator.id)
        )
        session.execute(
            delete(MetricSnapshot).where(MetricSnapshot.indicator_id == indicator.id)
        )
        session.execute(
            delete(MetricAcc).where(MetricAcc.indicator_id == indicator.id)
        )
        session.execute(
            delete(CustomIndicatorComponent).where(
                CustomIndicatorComponent.custom_indicator_id == indicator.id
            )
        )
        session.delete(indicator)
        session.commit()
    return {"code": code, "deleted": True}


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
        indicator = session.scalar(select(Indicator).where(Indicator.code == code))
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
        return _custom_payload(indicator, [])


def _normalize_components(components: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in components:
        source_code = str(item.get("source_code") or item.get("code") or "").strip()
        if not source_code or source_code in seen:
            continue
        coefficient = _normalize_coefficient(item.get("coefficient", 1))
        source_storage_mode = str(item.get("source_storage_mode") or "").strip().upper()
        if source_storage_mode and source_storage_mode not in {"STORE", "COMPONENT"}:
            raise ValueError(
                f"source_storage_mode 只支持 STORE/COMPONENT: {source_storage_mode}"
            )
        normalized.append(
            {
                "source_code": source_code,
                "coefficient": coefficient,
                "source_storage_mode": source_storage_mode or None,
            }
        )
        seen.add(source_code)
    return normalized


def _component_owner_ids(session: Session) -> set[int]:
    if not inspect(session.get_bind()).has_table("custom_indicator_component"):
        return set()
    return {
        int(custom_id)
        for custom_id in session.scalars(
            select(CustomIndicatorComponent.custom_indicator_id).distinct()
        )
    }


def _normalize_coefficient(value: Any) -> Decimal:
    if value is None or isinstance(value, bool):
        raise ValueError(f"系数不是有效数字: {value!r}")
    try:
        parsed = Decimal(str(value).strip())
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"系数不是有效数字: {value!r}") from exc
    if not parsed.is_finite():
        raise ValueError(f"系数必须是有限数字: {value!r}")
    significant = parsed.normalize() if parsed else parsed
    if significant.as_tuple().exponent < -4:
        raise ValueError(f"系数最多保留4位小数: {value!r}")
    if abs(parsed) >= _MAX_DECIMAL_ABS:
        raise ValueError(f"系数超出 DECIMAL(20,4) 范围: {value!r}")
    return parsed.quantize(_METRIC_VALUE_QUANT, rounding=ROUND_HALF_UP)


def _components_for(
    session: Session,
    custom_ids: list[int],
) -> dict[int, list[dict[str, Any]]]:
    if not custom_ids:
        return {}
    rows = session.execute(
        select(
            CustomIndicatorComponent.custom_indicator_id,
            Indicator.code,
            Indicator.name,
            Indicator.storage_mode,
            CustomIndicatorComponent.coefficient,
        )
        .join(Indicator, Indicator.id == CustomIndicatorComponent.source_indicator_id)
        .where(CustomIndicatorComponent.custom_indicator_id.in_(custom_ids))
        .order_by(CustomIndicatorComponent.id)
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


def _custom_payload(
    indicator: Indicator,
    components: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "id": indicator.id,
        "code": indicator.code,
        "name": indicator.name,
        "enabled": indicator.enabled,
        "source_active": indicator.source_active,
        "indicator_type": "CUSTOM" if components else indicator.indicator_type,
        "storage_mode": indicator.storage_mode,
        "sort_order": indicator.sort_order,
        "components": components,
    }
