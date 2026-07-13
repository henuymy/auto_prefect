"""Export V1 hierarchy and configuration into a reviewable V2 migration bundle.

Facts and collection runs are intentionally excluded.  The bundle contains
only base hierarchy, indicator settings, custom formulas, and NORMAL targets.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import tempfile
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import text

from infrastructure.dashboard_mysql import create_dashboard_engine


def _json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    raise TypeError(type(value).__name__)


def _atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(
                payload,
                stream,
                ensure_ascii=False,
                indent=2,
                default=_json_default,
            )
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _atomic_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(
            descriptor,
            "w",
            encoding="utf-8-sig",
            newline="",
        ) as stream:
            writer = csv.DictWriter(
                stream,
                fieldnames=[
                    "node_type",
                    "node_code",
                    "node_name",
                    "parent_node_code",
                    "sort_order",
                ],
            )
            writer.writeheader()
            writer.writerows(rows)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def export_bundle(
    output_dir: str | Path,
    *,
    effective_from: date,
) -> dict[str, Any]:
    output = Path(output_dir).resolve()
    engine = create_dashboard_engine()
    try:
        with engine.connect() as connection:
            database = connection.scalar(text("SELECT DATABASE()"))
            revision = connection.scalar(text("SELECT version_num FROM alembic_version"))
            hierarchy = [dict(row) for row in connection.execute(text("""
                SELECT
                    child.level_type AS node_type,
                    child.area_code AS node_code,
                    child.area_name AS node_name,
                    parent.area_code AS parent_node_code,
                    child.sort_order AS sort_order
                FROM area child
                LEFT JOIN area parent ON parent.id = child.parent_id
                WHERE child.level_type IN ('CITY','BRANCH','GRID')
                  AND child.enabled = 1
                ORDER BY child.level_no, child.sort_order, child.id
            """)).mappings()]
            indicators = [dict(row) for row in connection.execute(text("""
                SELECT code, name, indicator_type, storage_mode, enabled,
                       source_active, sort_order
                FROM indicator
                ORDER BY sort_order, id
            """)).mappings()]
            custom_rows = [dict(row) for row in connection.execute(text("""
                SELECT custom.code AS custom_code,
                       custom.name AS custom_name,
                       custom.enabled AS custom_enabled,
                       custom.sort_order AS custom_sort_order,
                       source.code AS source_code,
                       source.storage_mode AS source_storage_mode,
                       component.coefficient,
                       component.id AS component_order
                FROM custom_indicator_component component
                JOIN indicator custom ON custom.id = component.custom_indicator_id
                JOIN indicator source ON source.id = component.source_indicator_id
                ORDER BY custom.sort_order, custom.id, component.id
            """)).mappings()]
            target_rows = [dict(row) for row in connection.execute(text("""
                SELECT target.period_type,
                       area.level_type AS node_type,
                       area.area_code AS node_code,
                       indicator.code AS indicator_code,
                       target.target_value
                FROM metric_target target
                JOIN area ON area.id = target.area_id
                JOIN indicator ON indicator.id = target.indicator_id
                WHERE target.enabled = 1
                  AND target.period_type IN ('REALTIME','MONTH')
                ORDER BY target.period_type, area.level_no, area.sort_order,
                         area.id, indicator.sort_order, indicator.id
            """)).mappings()]
            fact_counts = dict(connection.execute(text("""
                SELECT
                    (SELECT COUNT(*) FROM metric_current) AS metric_current,
                    (SELECT COUNT(*) FROM metric_snapshot) AS metric_snapshot,
                    (SELECT COUNT(*) FROM metric_acc) AS metric_acc,
                    (SELECT COUNT(*) FROM collection_run) AS collection_run
            """)).mappings().one())
    finally:
        engine.dispose()

    customs: dict[str, dict[str, Any]] = {}
    for row in custom_rows:
        custom = customs.setdefault(
            row["custom_code"],
            {
                "code": row["custom_code"],
                "name": row["custom_name"],
                "enabled": bool(row["custom_enabled"]),
                "sort_order": row["custom_sort_order"],
                "components": [],
            },
        )
        custom["components"].append(
            {
                "source_code": row["source_code"],
                "source_storage_mode": row["source_storage_mode"],
                "coefficient": row["coefficient"],
            }
        )

    normal_day = [row for row in target_rows if row["period_type"] == "REALTIME"]
    normal_month = [row for row in target_rows if row["period_type"] == "MONTH"]

    def target_payload(name: str, period_type: str, rows: list[dict[str, Any]]):
        return {
            "plan_name": name,
            "scenario": "NORMAL",
            "period_type": period_type,
            "effective_from": effective_from.isoformat(),
            "effective_to": None,
            "priority": 0,
            "supersedes_plan_id": None,
            "values": [
                {
                    "node_type": row["node_type"],
                    "node_code": row["node_code"],
                    "indicator_code": row["indicator_code"],
                    "target_value": row["target_value"],
                }
                for row in rows
            ],
        }

    _atomic_csv(output / "hierarchy.csv", hierarchy)
    _atomic_json(output / "indicator_settings.json", indicators)
    _atomic_json(output / "custom_indicators.json", list(customs.values()))
    _atomic_json(
        output / "normal_day_target.json",
        target_payload("正常时期日目标", "DAY", normal_day),
    )
    _atomic_json(
        output / "normal_month_target.json",
        target_payload("正常时期月目标", "MONTH", normal_month),
    )
    summary = {
        "source_database": database,
        "source_revision": revision,
        "effective_from": effective_from.isoformat(),
        "hierarchy_node_count": len(hierarchy),
        "indicator_count": len(indicators),
        "custom_indicator_count": len(customs),
        "custom_component_count": len(custom_rows),
        "normal_day_target_count": len(normal_day),
        "normal_month_target_count": len(normal_month),
        "pk_day_target_count": 0,
        "pk_month_target_count": 0,
        "facts_intentionally_excluded": fact_counts,
    }
    _atomic_json(output / "summary.json", summary)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="导出驾驶舱 V2 配置迁移包")
    parser.add_argument(
        "--output",
        default="runtime/dashboard_v2_migration",
    )
    parser.add_argument(
        "--effective-from",
        type=date.fromisoformat,
        default=date.today(),
    )
    args = parser.parse_args()
    summary = export_bundle(
        args.output,
        effective_from=args.effective_from,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=_json_default))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
