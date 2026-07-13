from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy.dialects import mysql

from services.dashboard_metrics import MetricConflictError, MetricWriteError
from services.dashboard_v2_metric_store import (
    build_v2_acc_upsert_statement,
    build_v2_current_upsert_statement,
    build_v2_sparse_snapshot_plan,
    normalize_v2_metric_rows,
)


def current_value(
    node_id: int,
    indicator_id: int,
    metric_value: str,
    stat_date: date,
):
    return {
        "node_id": node_id,
        "indicator_id": indicator_id,
        "collection_run_id": 10,
        "metric_value": Decimal(metric_value),
        "stat_date": stat_date,
        "collected_at": datetime(2026, 6, 30, 12, 0),
        "updated_at": datetime(2026, 6, 30, 12, 0),
    }


def test_normalize_v2_metric_rows_deduplicates_equal_values():
    result = normalize_v2_metric_rows(
        [
            {"node_id": 1, "raw_value": "1,234.5000"},
            {"node_id": 1, "raw_value": Decimal("1234.5")},
        ]
    )

    assert result == [{"node_id": 1, "metric_value": Decimal("1234.5000")}]


def test_normalize_v2_metric_rows_rejects_bad_id_and_conflicts():
    with pytest.raises(MetricWriteError, match="node_id"):
        normalize_v2_metric_rows([{"node_id": None, "raw_value": 1}])
    with pytest.raises(MetricConflictError):
        normalize_v2_metric_rows(
            [
                {"node_id": 1, "raw_value": 1},
                {"node_id": 1, "raw_value": 2},
            ]
        )


def test_sparse_plan_keeps_manager_full_and_skips_unchanged_channel():
    today = date(2026, 6, 30)
    values = [
        current_value(1, 100, "10", today),
        current_value(2, 100, "20", today),
    ]
    existing = {
        (1, 100): SimpleNamespace(metric_value=Decimal("10"), stat_date=today),
        (2, 100): SimpleNamespace(metric_value=Decimal("20"), stat_date=today),
    }

    snapshots, stats = build_v2_sparse_snapshot_plan(
        run_id=10,
        current_values=values,
        node_types={1: "CHANNEL_MANAGER", 2: "CHANNEL"},
        existing_by_key=existing,
        collected_at=datetime(2026, 6, 30, 12, 0),
    )

    assert [row["node_id"] for row in snapshots] == [1]
    assert stats["manager_full_row_count"] == 1
    assert stats["channel_unchanged_skipped_row_count"] == 1


def test_sparse_plan_writes_channel_change_and_daily_checkpoint():
    today = date(2026, 6, 30)
    yesterday = date(2026, 6, 29)
    values = [
        current_value(1, 100, "11", today),
        current_value(2, 100, "20", today),
    ]
    existing = {
        (1, 100): SimpleNamespace(metric_value=Decimal("10"), stat_date=today),
        (2, 100): SimpleNamespace(metric_value=Decimal("20"), stat_date=yesterday),
    }

    snapshots, stats = build_v2_sparse_snapshot_plan(
        run_id=10,
        current_values=values,
        node_types={1: "CHANNEL", 2: "CHANNEL"},
        existing_by_key=existing,
        collected_at=datetime(2026, 6, 30, 12, 0),
    )

    assert len(snapshots) == 2
    assert stats["channel_changed_row_count"] == 1
    assert stats["channel_daily_checkpoint_row_count"] == 1


def test_current_upsert_contains_collected_at_guard():
    statement = build_v2_current_upsert_statement(
        [current_value(1, 100, "10", date(2026, 6, 30))]
    )
    sql = str(statement.compile(dialect=mysql.dialect()))

    assert "ON DUPLICATE KEY UPDATE" in sql
    assert "VALUES(collected_at) >= metric_current.collected_at" in sql


def test_acc_upsert_contains_collected_at_guard():
    value = current_value(1, 100, "10", date(2026, 6, 30))
    value["period_type"] = "DAY_ACC"
    statement = build_v2_acc_upsert_statement([value])
    sql = str(statement.compile(dialect=mysql.dialect()))

    assert "ON DUPLICATE KEY UPDATE" in sql
    assert "VALUES(collected_at) >= metric_acc.collected_at" in sql
