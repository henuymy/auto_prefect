"""Dashboard-independent metric parsing and custom formula helpers."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any


METRIC_VALUE_QUANT = Decimal("0.0001")
MAX_DECIMAL_ABS = Decimal("10000000000000000")


class MetricWriteError(RuntimeError):
    phase = "WRITE_METRICS"


class MetricValueError(MetricWriteError):
    error_type = "INVALID_METRIC_VALUE"


class MetricConflictError(MetricWriteError):
    error_type = "DUPLICATE_METRIC_CONFLICT"


def parse_metric_value(value: Any) -> Decimal:
    if value is None or isinstance(value, bool):
        raise MetricValueError(f"指标值不是有效数字: {value!r}")
    text = str(value).strip().replace(",", "")
    if not text:
        raise MetricValueError("指标值不能为空")
    try:
        parsed = Decimal(text)
    except InvalidOperation as exc:
        raise MetricValueError(f"指标值不是有效数字: {value!r}") from exc
    if not parsed.is_finite():
        raise MetricValueError(f"指标值必须是有限数字: {value!r}")
    significant = parsed.normalize() if parsed else parsed
    if significant.as_tuple().exponent < -4:
        raise MetricValueError(f"指标值最多保留4位小数: {value!r}")
    if abs(parsed) >= MAX_DECIMAL_ABS:
        raise MetricValueError(f"指标值超出 DECIMAL(20,4) 范围: {value!r}")
    return parsed


def normalize_indicator_components(
    components: list[dict[str, Any]],
) -> list[dict[str, Any]]:
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
                total.quantize(METRIC_VALUE_QUANT, rounding=ROUND_HALF_UP)
                if has_value
                else None
            )
        composed.append(row)
    return composed


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
    if abs(parsed) >= MAX_DECIMAL_ABS:
        raise ValueError(f"系数超出 DECIMAL(20,4) 范围: {value!r}")
    return parsed.quantize(METRIC_VALUE_QUANT, rounding=ROUND_HALF_UP)
