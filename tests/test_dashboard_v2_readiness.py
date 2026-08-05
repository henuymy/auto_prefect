from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import CheckConstraint, ForeignKeyConstraint, UniqueConstraint

from models.dashboard_v2_base import DashboardV2Base
import models.dashboard_v2  # noqa: F401  Ensures V2 tables are registered.
import models.monitor  # noqa: F401  Ensures monitor tables are registered.
from services import dashboard_v2_readiness as readiness


PROJECT_ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 7, 10, 12)


def valid_snapshot():
    business_day = NOW.date()
    partitions = {
        f"p{day:%Y%m%d}": (day + timedelta(days=1)).isoformat()
        for offset in range(31)
        if (day := business_day + timedelta(days=offset))
    }
    partitions["p_future"] = "MAXVALUE"
    return readiness.DashboardV2SchemaSnapshot(
        actual_revision="head",
        tables=set(readiness.EXPECTED_TABLES),
        columns={
            table: set(columns)
            for table, columns in readiness.EXPECTED_COLUMNS.items()
        },
        generated_columns=set(readiness.EXPECTED_GENERATED_COLUMNS),
        unique_constraints=set(readiness.EXPECTED_UNIQUE_CONSTRAINTS),
        foreign_keys=set(readiness.EXPECTED_FOREIGN_KEYS),
        check_constraints=set(readiness.EXPECTED_CHECK_CONSTRAINTS),
        indexes=set(readiness.EXPECTED_INDEXES),
        primary_keys={"metric_snapshot": ("id", "collected_at")},
        partitions=partitions,
    )


def test_expected_revision_matches_migration_head():
    config = Config(str(PROJECT_ROOT / "alembic_dashboard_v2.ini"))
    head = ScriptDirectory.from_config(config).get_current_head()

    assert readiness.load_expected_revision() == head
    assert readiness.EXPECTED_REVISION == head


def test_readiness_manifest_matches_all_orm_constraints_and_indexes():
    uniques = set()
    foreign_keys = set()
    checks = set()
    indexes = set()
    for table in DashboardV2Base.metadata.tables.values():
        for constraint in table.constraints:
            if isinstance(constraint, UniqueConstraint):
                uniques.add(
                    (
                        table.name,
                        constraint.name,
                        tuple(column.name for column in constraint.columns),
                    )
                )
            elif isinstance(constraint, ForeignKeyConstraint):
                foreign_keys.add(
                    (
                        table.name,
                        tuple(column.name for column in constraint.columns),
                        next(iter(constraint.elements)).column.table.name,
                        tuple(element.column.name for element in constraint.elements),
                        str(constraint.ondelete or "RESTRICT").upper(),
                    )
                )
            elif isinstance(constraint, CheckConstraint):
                checks.add((table.name, constraint.name))
        indexes.update(
            (
                table.name,
                index.name,
                tuple(column.name for column in index.columns),
            )
            for index in table.indexes
        )

    assert readiness.EXPECTED_UNIQUE_CONSTRAINTS == uniques
    assert readiness.EXPECTED_FOREIGN_KEYS == foreign_keys
    assert readiness.EXPECTED_CHECK_CONSTRAINTS == checks
    assert readiness.EXPECTED_INDEXES == indexes


def test_complete_schema_snapshot_is_ready():
    result = readiness.evaluate_dashboard_v2_schema(
        valid_snapshot(), expected_revision="head", now=NOW
    )

    assert result["ok"] is True
    assert result["missing_snapshot_partitions"] == []


@pytest.mark.parametrize(
    ("category", "item", "result_key"),
    [
        (
            "unique_constraints",
            (
                "hierarchy_parent_history",
                "uq_hierarchy_history_active_child",
                ("active_child_node_id",),
            ),
            "missing_unique_constraints",
        ),
        (
            "foreign_keys",
            (
                "metric_current",
                ("node_id",),
                "hierarchy_node",
                ("id",),
                "RESTRICT",
            ),
            "missing_foreign_keys",
        ),
        (
            "generated_columns",
            ("hierarchy_parent_history", "active_child_node_id"),
            "generated_columns_ok",
        ),
        (
            "indexes",
            (
                "metric_snapshot",
                "ix_metric_snapshot_node_indicator_collected",
                ("node_id", "indicator_id", "collected_at"),
            ),
            "missing_indexes",
        ),
        (
            "check_constraints",
            ("collection_run", "ck_collection_run_valid_status"),
            "missing_check_constraints",
        ),
    ],
)
def test_readiness_rejects_missing_critical_schema(category, item, result_key):
    snapshot = deepcopy(valid_snapshot())
    getattr(snapshot, category).remove(item)

    result = readiness.evaluate_dashboard_v2_schema(
        snapshot, expected_revision="head", now=NOW
    )

    assert result["ok"] is False
    if result_key == "generated_columns_ok":
        assert result[result_key] is False
    else:
        assert result[result_key]


def test_readiness_rejects_missing_or_wrong_future_partition():
    snapshot = valid_snapshot()
    snapshot.partitions.pop("p20260711")
    snapshot.partitions["p20260712"] = "2026-08-01"

    result = readiness.evaluate_dashboard_v2_schema(
        snapshot, expected_revision="head", now=NOW
    )

    assert result["ok"] is False
    assert "p20260711" in result["missing_snapshot_partitions"]
    assert "p20260712" in result["invalid_snapshot_partitions"]


def test_readiness_rejects_snapshot_foreign_key_and_wrong_primary_key():
    snapshot = valid_snapshot()
    snapshot.foreign_keys.add(
        (
            "metric_snapshot",
            ("node_id",),
            "hierarchy_node",
            ("id",),
            "RESTRICT",
        )
    )
    snapshot.primary_keys["metric_snapshot"] = ("id",)

    result = readiness.evaluate_dashboard_v2_schema(
        snapshot, expected_revision="head", now=NOW
    )

    assert result["ok"] is False
    assert result["metric_snapshot_has_no_foreign_keys"] is False
    assert result["metric_snapshot_primary_key_ok"] is False
