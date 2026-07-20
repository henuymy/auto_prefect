from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from services.dashboard_v2_retention_service import cleanup_v2_expired_data
from tests.test_dashboard_v2_query_service import _engine


def _create_monitor_runs_table(engine) -> None:
    with engine.begin() as connection:
        connection.execute(text("""
            CREATE TABLE monitor_runs (
                id TEXT PRIMARY KEY, source TEXT NOT NULL, external_run_id TEXT NOT NULL,
                task_name TEXT NOT NULL, trigger TEXT NOT NULL, status TEXT NOT NULL,
                finished_at DATETIME, current_step TEXT NOT NULL
            )
        """))


def test_v2_cleanup_removes_expired_data_and_clears_run_references():
    engine = _engine()
    _create_monitor_runs_table(engine)
    with engine.begin() as connection:
        connection.execute(text("""
            INSERT INTO collection_run
                (id, batch_no, run_type, trigger_type, status, phase,
                 stat_date, started_at, finished_at, created_at, updated_at)
            VALUES
                (3, 'old-run', 'REALTIME', 'SCHEDULED', 'SUCCESS', 'COMPLETED',
                 '2026-05-01', '2026-05-01 10:00:00', '2026-05-01 10:00:01',
                 '2026-05-01 10:00:00', '2026-05-01 10:00:01')
        """))
        connection.execute(text("""
            INSERT INTO metric_snapshot
                (id, collected_at, collection_run_id, node_id, indicator_id, metric_value)
            VALUES (30, '2026-05-01 10:00:00', 3, 5, 1, 8)
        """))
        connection.execute(text("""
            INSERT INTO metric_acc
                (id, period_type, stat_date, node_id, indicator_id,
                 collection_run_id, metric_value, collected_at)
            VALUES
                (1, 'DAY_ACC', '2026-01-01', 5, 1, 3, 8, '2026-01-01 10:00:00')
        """))
        connection.execute(text(
            "UPDATE metric_current SET collection_run_id = 3 WHERE id = 4"
        ))
        connection.execute(text(
            "UPDATE hierarchy_parent_history SET collection_run_id = 3 WHERE id = 4"
        ))

    with Session(engine) as session:
        result = cleanup_v2_expired_data(
            session,
            now=datetime(2026, 6, 30, 12),
            snapshot_retention_days=7,
            run_retention_days=30,
            acc_retention_days=90,
        )

    assert result["snapshot_deleted"] == 1
    assert result["acc_deleted"] == 1
    assert result["run_deleted"] == 1
    assert result["run_reference_cleared"] == 2
    with engine.connect() as connection:
        assert connection.scalar(text(
            "SELECT collection_run_id FROM metric_current WHERE id = 4"
        )) is None
        assert connection.scalar(text(
            "SELECT collection_run_id FROM hierarchy_parent_history WHERE id = 4"
        )) is None


def test_run_retention_cannot_be_shorter_than_snapshot_retention():
    with Session(_engine()) as session:
        with pytest.raises(ValueError, match="不能小于"):
            cleanup_v2_expired_data(
                session,
                snapshot_retention_days=30,
                run_retention_days=7,
            )


def test_v2_cleanup_keeps_active_monitor_runs_and_removes_finished_monitor_runs_after_30_days():
    engine = _engine()
    _create_monitor_runs_table(engine)
    with engine.begin() as connection:
        connection.execute(text("""
            INSERT INTO monitor_runs (id, source, external_run_id, task_name, trigger, status, finished_at, current_step)
            VALUES
                ('expired', 'prefect', 'expired', '通报 · 过期日报', '定时调度', 'succeeded', '2026-05-31 11:00:00', '已完成'),
                ('active', 'prefect', 'active', '通报 · 仍在运行', '定时调度', 'running', NULL, '下载报表')
        """))

    with Session(engine) as session:
        result = cleanup_v2_expired_data(
            session,
            now=datetime(2026, 6, 30, 12),
            monitor_run_retention_days=30,
        )

    assert result["monitor_run_deleted"] == 1
    with engine.connect() as connection:
        assert connection.scalar(text(
            "SELECT COUNT(*) FROM monitor_runs WHERE id = 'expired'"
        )) == 0
        assert connection.scalar(text(
            "SELECT COUNT(*) FROM monitor_runs WHERE id = 'active'"
        )) == 1
