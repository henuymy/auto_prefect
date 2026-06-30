"""Dashboard V2 migration and critical-schema readiness checks."""

from __future__ import annotations

from typing import Any

from sqlalchemy import inspect, text
from sqlalchemy.exc import SQLAlchemyError

from infrastructure.dashboard_mysql import create_dashboard_engine


EXPECTED_REVISION = "20260630_0001"
EXPECTED_TABLES = {
    "collection_run",
    "hierarchy_node",
    "hierarchy_parent_history",
    "indicator",
    "indicator_formula_component",
    "target_plan",
    "metric_target_value",
    "metric_current",
    "metric_snapshot",
    "metric_acc",
}
EXPECTED_NODE_COLUMNS = {
    "id", "node_type", "node_code", "node_name", "parent_id", "level_no",
    "request_enabled", "metric_enabled", "enabled", "sort_order",
}
EXPECTED_CURRENT_UNIQUE = {"node_id", "indicator_id"}


def check_dashboard_v2_schema() -> dict[str, Any]:
    engine = create_dashboard_engine()
    try:
        with engine.connect() as connection:
            inspector = inspect(connection)
            tables = set(inspector.get_table_names())
            missing_tables = sorted(EXPECTED_TABLES - tables)
            revision = (
                connection.scalar(text("SELECT version_num FROM alembic_version"))
                if "alembic_version" in tables else None
            )
            node_columns = (
                {row["name"] for row in inspector.get_columns("hierarchy_node")}
                if "hierarchy_node" in tables else set()
            )
            current_unique = (
                [
                    set(row.get("column_names") or [])
                    for row in inspector.get_unique_constraints("metric_current")
                ]
                if "metric_current" in tables else []
            )
            snapshot_primary = (
                inspector.get_pk_constraint("metric_snapshot").get(
                    "constrained_columns"
                ) or []
                if "metric_snapshot" in tables else []
            )
            partitions = set(connection.scalars(text("""
                SELECT PARTITION_NAME
                FROM information_schema.PARTITIONS
                WHERE TABLE_SCHEMA = DATABASE()
                  AND TABLE_NAME = 'metric_snapshot'
                  AND PARTITION_NAME IS NOT NULL
            """))) if "metric_snapshot" in tables else set()
        missing_columns = sorted(EXPECTED_NODE_COLUMNS - node_columns)
        current_unique_ok = EXPECTED_CURRENT_UNIQUE in current_unique
        snapshot_primary_ok = snapshot_primary == ["id", "collected_at"]
        partition_ok = "p_future" in partitions and any(
            str(name).startswith("p20") for name in partitions
        )
        ok = all((
            not missing_tables,
            revision == EXPECTED_REVISION,
            not missing_columns,
            current_unique_ok,
            snapshot_primary_ok,
            partition_ok,
        ))
        return {
            "ok": ok,
            "message": "驾驶舱 V2 结构就绪" if ok else "驾驶舱 V2 结构未就绪",
            "expected_revision": EXPECTED_REVISION,
            "actual_revision": revision,
            "missing_tables": missing_tables,
            "missing_hierarchy_node_columns": missing_columns,
            "metric_current_unique_ok": current_unique_ok,
            "metric_snapshot_primary_key_ok": snapshot_primary_ok,
            "metric_snapshot_partition_ok": partition_ok,
            "partition_count": len(partitions),
        }
    except SQLAlchemyError as exc:
        return {
            "ok": False,
            "message": f"驾驶舱 V2 结构检查失败: {type(exc).__name__}",
            "expected_revision": EXPECTED_REVISION,
        }
    finally:
        engine.dispose()
