from __future__ import annotations

from pathlib import Path

from sqlalchemy import ForeignKeyConstraint, UniqueConstraint
from sqlalchemy.dialects import mysql
from sqlalchemy.schema import CreateTable

from models.dashboard_v2 import (
    CollectionRunV2,
    DashboardV2Base,
    HierarchyParentHistory,
    MetricAccV2,
    MetricCurrentV2,
    MetricSnapshotV2,
)


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


def test_v2_metadata_contains_only_greenfield_tables():
    assert set(DashboardV2Base.metadata.tables) == EXPECTED_TABLES


def test_partitioned_snapshot_has_composite_primary_key_and_no_foreign_keys():
    table = MetricSnapshotV2.__table__

    assert [column.name for column in table.primary_key.columns] == [
        "id",
        "collected_at",
    ]
    assert not any(
        isinstance(constraint, ForeignKeyConstraint)
        for constraint in table.constraints
    )
    unique_columns = {
        tuple(column.name for column in constraint.columns)
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    assert (
        "collection_run_id",
        "node_id",
        "indicator_id",
        "collected_at",
    ) in unique_columns


def test_parent_history_uses_generated_column_for_one_active_parent():
    table = HierarchyParentHistory.__table__
    generated = table.c.active_child_node_id.computed

    assert generated is not None
    assert "valid_to IS NULL" in str(generated.sqltext)
    assert any(
        isinstance(constraint, UniqueConstraint)
        and constraint.name == "uq_hierarchy_history_active_child"
        for constraint in table.constraints
    )


def test_run_references_are_nullable_and_set_null_on_run_cleanup():
    for table in (
        HierarchyParentHistory.__table__,
        MetricCurrentV2.__table__,
        MetricAccV2.__table__,
    ):
        column = table.c.collection_run_id
        assert column.nullable is True
        foreign_key = next(iter(column.foreign_keys))
        assert foreign_key.ondelete == "SET NULL"


def test_collection_run_status_contract_is_success_not_completed():
    ddl = str(CreateTable(CollectionRunV2.__table__).compile(dialect=mysql.dialect()))

    assert "'PENDING','RUNNING','SUCCESS','FAILED'" in ddl
    assert "'COMPLETED'" not in ddl


def test_v2_baseline_is_frozen_and_does_not_import_live_metadata():
    migration = (
        Path(__file__).resolve().parents[1]
        / "migrations"
        / "dashboard_v2"
        / "versions"
        / "20260630_0001_dashboard_v2_baseline.py"
    ).read_text(encoding="utf-8")

    assert "models.dashboard_v2" not in migration
    assert "metadata.create_all" not in migration
    assert "PRIMARY KEY (id, collected_at)" in migration
    assert "PARTITION p_future VALUES LESS THAN (MAXVALUE)" in migration
