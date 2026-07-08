from __future__ import annotations

import calendar
import re
from datetime import datetime, timedelta


DATE_EXPR_PATTERN = re.compile(r"\$\{date:([^}|]+)\|([^}]+)\}")
OFFSET_PATTERN = re.compile(r"([+-]\d+)([dMy])")


def _add_months(value: datetime, months: int) -> datetime:
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return value.replace(year=year, month=month, day=day)


def _add_years(value: datetime, years: int) -> datetime:
    year = value.year + years
    day = min(value.day, calendar.monthrange(year, value.month)[1])
    return value.replace(year=year, day=day)


def _base_date(name: str, now: datetime) -> datetime:
    if name == "today":
        return now
    if name == "yesterday":
        return now - timedelta(days=1)
    if name == "day_before_yesterday":
        return now - timedelta(days=2)
    raise ValueError(f"不支持的日期基准: {name}")


def _format_date(value: datetime, fmt: str) -> str:
    if fmt == "yyyyMMdd":
        return value.strftime("%Y%m%d")
    if fmt == "yyyy-MM-dd":
        return value.strftime("%Y-%m-%d")
    raise ValueError(f"不支持的日期格式: {fmt}")


def resolve_date_expression(expression: str, fmt: str, now: datetime | None = None) -> str:
    now = now or datetime.now()
    base_match = re.match(r"^(today|yesterday|day_before_yesterday)", expression)
    if not base_match:
        raise ValueError(f"日期表达式缺少有效基准: {expression}")
    value = _base_date(base_match.group(1), now)
    rest = expression[base_match.end():]
    position = 0
    while position < len(rest):
        offset_match = OFFSET_PATTERN.match(rest, position)
        if not offset_match:
            raise ValueError(f"日期表达式偏移格式错误: {expression}")
        amount = int(offset_match.group(1))
        unit = offset_match.group(2)
        if unit == "d":
            value = value + timedelta(days=amount)
        elif unit == "M":
            value = _add_months(value, amount)
        elif unit == "y":
            value = _add_years(value, amount)
        position = offset_match.end()
    return _format_date(value, fmt)


def standard_replacements(now: datetime | None = None) -> dict[str, str]:
    now = now or datetime.now()
    yesterday = now - timedelta(days=1)
    day_before_yesterday = now - timedelta(days=2)
    return {
        "${today}": now.strftime("%Y-%m-%d"),
        "${today_yyyymmdd}": now.strftime("%Y%m%d"),
        "${yesterday}": yesterday.strftime("%Y-%m-%d"),
        "${yesterday_yyyymmdd}": yesterday.strftime("%Y%m%d"),
        "${day_before_yesterday}": day_before_yesterday.strftime("%Y-%m-%d"),
        "${day_before_yesterday_yyyymmdd}": day_before_yesterday.strftime("%Y%m%d"),
        "${hour}": str(now.hour),
        "${hour2}": now.strftime("%H"),
    }


def resolve_dynamic_placeholders(value: str, now: datetime | None = None) -> str:
    if not isinstance(value, str):
        return value
    now = now or datetime.now()

    def replace_date(match: re.Match[str]) -> str:
        return resolve_date_expression(match.group(1), match.group(2), now=now)

    resolved = DATE_EXPR_PATTERN.sub(replace_date, value)
    for token, token_value in standard_replacements(now).items():
        resolved = resolved.replace(token, token_value)
    return resolved


def resolve_dynamic_structure(payload, now: datetime | None = None):
    if isinstance(payload, dict):
        return {key: resolve_dynamic_structure(value, now=now) for key, value in payload.items()}
    if isinstance(payload, list):
        return [resolve_dynamic_structure(item, now=now) for item in payload]
    return resolve_dynamic_placeholders(payload, now=now)
