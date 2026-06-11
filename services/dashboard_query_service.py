"""Read models for the dashboard frontend."""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from typing import Any

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from models.dashboard_area import Area
from models.dashboard_collection_run import CollectionRun
from models.dashboard_indicator import Indicator
from models.dashboard_metric import MetricCurrent


def _number(value: Decimal | None) -> float | int | None:
    if value is None:
        return None
    if value == value.to_integral_value():
        return int(value)
    return float(value)


def get_current_wide_table(
    engine: Engine,
    level_type: str | None = None,
    parent_id: int | None = None,
) -> dict[str, Any]:
    normalized_level = str(level_type or "").strip().upper() or None
    with Session(engine) as session:
        indicators = session.scalars(
            select(Indicator)
            .where(Indicator.enabled.is_(True))
            .order_by(Indicator.sort_order, Indicator.id)
        ).all()

        area_query = (
            select(Area)
            .where(Area.enabled.is_(True))
            .order_by(Area.level_no, Area.sort_order, Area.id)
        )
        if normalized_level:
            area_query = area_query.where(Area.level_type == normalized_level)
        if parent_id is not None:
            area_query = area_query.where(Area.parent_id == parent_id)
        areas = session.scalars(area_query).all()
        area_ids = [area.id for area in areas]

        values_by_area: dict[int, dict[str, Any]] = defaultdict(dict)
        collected_by_area: dict[int, Any] = {}
        if area_ids and indicators:
            current_rows = session.execute(
                select(
                    MetricCurrent.area_id,
                    Indicator.code,
                    MetricCurrent.metric_value,
                    MetricCurrent.collected_at,
                    MetricCurrent.collection_run_id,
                )
                .join(Indicator, Indicator.id == MetricCurrent.indicator_id)
                .where(
                    MetricCurrent.area_id.in_(area_ids),
                    Indicator.enabled.is_(True),
                )
            ).all()
            for row in current_rows:
                values_by_area[row.area_id][row.code] = _number(row.metric_value)
                previous = collected_by_area.get(row.area_id)
                if previous is None or row.collected_at > previous["collected_at"]:
                    collected_by_area[row.area_id] = {
                        "collected_at": row.collected_at,
                        "collection_run_id": row.collection_run_id,
                    }

        latest_run = session.scalar(
            select(CollectionRun)
            .join(
                MetricCurrent,
                MetricCurrent.collection_run_id == CollectionRun.id,
            )
            .where(
                CollectionRun.status == "SUCCESS",
                CollectionRun.run_type == "REALTIME",
            )
            .distinct()
            .order_by(CollectionRun.finished_at.desc(), CollectionRun.id.desc())
            .limit(1)
        )

    indicator_payload = [
        {
            "id": indicator.id,
            "code": indicator.code,
            "name": indicator.name,
            "sort_order": indicator.sort_order,
        }
        for indicator in indicators
    ]
    rows = []
    for area in areas:
        collection = collected_by_area.get(area.id)
        metrics = {
            indicator.code: values_by_area[area.id].get(indicator.code)
            for indicator in indicators
        }
        rows.append(
            {
                "area_id": area.id,
                "area_code": area.area_code,
                "area_name": area.area_name,
                "level_type": area.level_type,
                "level_no": area.level_no,
                "parent_id": area.parent_id,
                "collection_run_id": (
                    collection["collection_run_id"] if collection else None
                ),
                "collected_at": (
                    collection["collected_at"].isoformat(timespec="milliseconds")
                    if collection
                    else None
                ),
                "metrics": metrics,
            }
        )

    return {
        "latest_run": (
            {
                "id": latest_run.id,
                "batch_no": latest_run.batch_no,
                "stat_date": (
                    latest_run.stat_date.isoformat()
                    if latest_run.stat_date
                    else None
                ),
                "finished_at": (
                    latest_run.finished_at.isoformat(timespec="milliseconds")
                    if latest_run.finished_at
                    else None
                ),
            }
            if latest_run
            else None
        ),
        "indicators": indicator_payload,
        "rows": rows,
        "row_count": len(rows),
    }
