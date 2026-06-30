"""Tests for the data retention service."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from services.dashboard_retention_service import (
    _daily_partition_clause,
    _partition_day,
    cleanup_expired_data,
)


def create_test_engine():
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(dbapi_connection, _connection_record):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE collection_run (
                id INTEGER PRIMARY KEY,
                batch_no VARCHAR(64) NOT NULL,
                run_type VARCHAR(16) NOT NULL DEFAULT 'REALTIME',
                trigger_type VARCHAR(16) NOT NULL,
                prefect_flow_run_id VARCHAR(36),
                status VARCHAR(16) NOT NULL,
                phase VARCHAR(32) NOT NULL,
                session_status VARCHAR(32),
                stat_date DATE,
                started_at DATETIME,
                finished_at DATETIME,
                request_count INTEGER NOT NULL DEFAULT 0,
                area_count INTEGER NOT NULL DEFAULT 0,
                row_count INTEGER NOT NULL DEFAULT 0,
                current_upsert_count INTEGER NOT NULL DEFAULT 0,
                snapshot_insert_count INTEGER NOT NULL DEFAULT 0,
                acc_upsert_count INTEGER NOT NULL DEFAULT 0,
                error_type VARCHAR(64),
                error_message TEXT,
                structure_change_summary TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """))
        conn.execute(text("""
            CREATE TABLE metric_current (
                id INTEGER PRIMARY KEY,
                area_id INTEGER NOT NULL,
                indicator_id INTEGER NOT NULL,
                collection_run_id INTEGER NOT NULL,
                metric_value NUMERIC(20,4) NOT NULL,
                stat_date DATE NOT NULL,
                collected_at DATETIME NOT NULL,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(collection_run_id) REFERENCES collection_run(id) ON DELETE RESTRICT
            )
        """))
        conn.execute(text("""
            CREATE TABLE metric_snapshot (
                id INTEGER PRIMARY KEY,
                collection_run_id INTEGER NOT NULL,
                area_id INTEGER NOT NULL,
                indicator_id INTEGER NOT NULL,
                metric_value NUMERIC(20,4) NOT NULL,
                collected_at DATETIME NOT NULL,
                FOREIGN KEY(collection_run_id) REFERENCES collection_run(id) ON DELETE RESTRICT
            )
        """))
        conn.execute(text("""
            CREATE TABLE metric_acc (
                id INTEGER PRIMARY KEY,
                period_type VARCHAR(16) NOT NULL,
                stat_date DATE NOT NULL,
                area_id INTEGER NOT NULL,
                indicator_id INTEGER NOT NULL,
                collection_run_id INTEGER NOT NULL,
                metric_value NUMERIC(20,4) NOT NULL,
                collected_at DATETIME NOT NULL,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(collection_run_id) REFERENCES collection_run(id) ON DELETE RESTRICT
            )
        """))
    return engine


def _insert_run(conn, run_id, status="SUCCESS", created_at=None):
    created = created_at.isoformat(sep=" ") if hasattr(created_at, "isoformat") else created_at
    conn.execute(
        text(
            "INSERT INTO collection_run "
            "(id, batch_no, trigger_type, status, phase, created_at, updated_at) "
            "VALUES (:id, :batch, 'SCHEDULED', :status, 'COMPLETED', :created, :created)"
        ),
        {"id": run_id, "batch": f"b-{run_id}", "status": status, "created": created},
    )


def _insert_snapshot(conn, run_id, area_id, indicator_id, value, collected_at):
    collected = collected_at.isoformat(sep=" ") if hasattr(collected_at, "isoformat") else collected_at
    conn.execute(
        text(
            "INSERT INTO metric_snapshot (collection_run_id, area_id, indicator_id, metric_value, collected_at) "
            "VALUES (:run, :area, :ind, :val, :ts)"
        ),
        {"run": run_id, "area": area_id, "ind": indicator_id, "val": value, "ts": collected},
    )


def _insert_current(conn, run_id, area_id, indicator_id, value, collected_at):
    collected = collected_at.isoformat(sep=" ") if hasattr(collected_at, "isoformat") else collected_at
    stat_date = collected_at.date().isoformat() if hasattr(collected_at, "date") else str(collected_at)[:10]
    conn.execute(
        text(
            "INSERT INTO metric_current "
            "(area_id, indicator_id, collection_run_id, metric_value, stat_date, collected_at) "
            "VALUES (:area, :ind, :run, :val, :date, :ts)"
        ),
        {
            "run": run_id,
            "area": area_id,
            "ind": indicator_id,
            "val": value,
            "date": stat_date,
            "ts": collected,
        },
    )


def _insert_acc(conn, run_id, area_id, indicator_id, value, stat_date, period="DAY_ACC"):
    stat = stat_date.isoformat() if hasattr(stat_date, "isoformat") else stat_date
    conn.execute(
        text(
            "INSERT INTO metric_acc (period_type, stat_date, area_id, indicator_id, collection_run_id, metric_value, collected_at) "
            "VALUES (:period, :date, :area, :ind, :run, :val, :ts)"
        ),
        {
            "period": period,
            "date": stat,
            "area": area_id,
            "ind": indicator_id,
            "run": run_id,
            "val": value,
            "ts": stat,
        },
    )


def test_delete_expired_snapshots():
    engine = create_test_engine()
    now = datetime.now(UTC).replace(tzinfo=None)
    old_ts = now - timedelta(days=10)
    recent_ts = now - timedelta(hours=1)

    with engine.begin() as conn:
        _insert_run(conn, 1, "SUCCESS", old_ts)
        _insert_snapshot(conn, 1, 100, 1, 100.0, old_ts)
        _insert_snapshot(conn, 1, 100, 1, 200.0, recent_ts)

    with Session(engine) as session:
        result = cleanup_expired_data(session, snapshot_retention_days=7)

    assert result["snapshot_deleted"] == 1

    with engine.connect() as conn:
        remaining = conn.execute(text("SELECT COUNT(*) FROM metric_snapshot")).scalar()
    assert remaining == 1


def test_snapshot_retention_keeps_full_boundary_day_checkpoint():
    engine = create_test_engine()
    now = datetime.now(ZoneInfo("Asia/Shanghai")).replace(tzinfo=None)
    boundary_start = (now - timedelta(days=7)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    before_boundary = boundary_start - timedelta(minutes=1)
    boundary_checkpoint = boundary_start + timedelta(minutes=1)

    with engine.begin() as conn:
        _insert_run(conn, 1, "SUCCESS", before_boundary)
        _insert_run(conn, 2, "SUCCESS", boundary_checkpoint)
        _insert_snapshot(conn, 1, 100, 1, 90.0, before_boundary)
        _insert_snapshot(conn, 2, 100, 1, 100.0, boundary_checkpoint)

    with Session(engine) as session:
        result = cleanup_expired_data(session, snapshot_retention_days=7)

    assert result["snapshot_deleted"] == 1
    with engine.connect() as conn:
        remaining = conn.execute(text(
            "SELECT collection_run_id FROM metric_snapshot ORDER BY id"
        )).scalars().all()
    assert remaining == [2]
    engine.dispose()


def test_delete_expired_acc():
    engine = create_test_engine()
    now = datetime.now(UTC).replace(tzinfo=None)
    old_date = (now - timedelta(days=120)).date()
    recent_date = (now - timedelta(days=1)).date()

    with engine.begin() as conn:
        _insert_run(conn, 1, "SUCCESS", now - timedelta(days=120))
        _insert_acc(conn, 1, 100, 1, 50.0, old_date)
        _insert_acc(conn, 1, 100, 1, 80.0, recent_date)

    with Session(engine) as session:
        result = cleanup_expired_data(session, acc_retention_days=90)

    assert result["acc_deleted"] == 1

    with engine.connect() as conn:
        remaining = conn.execute(text("SELECT COUNT(*) FROM metric_acc")).scalar()
    assert remaining == 1


def test_delete_runs_only_when_snapshots_removed():
    engine = create_test_engine()
    now = datetime.now(UTC).replace(tzinfo=None)
    old_ts = now - timedelta(days=10)
    recent_ts = now - timedelta(hours=1)

    with engine.begin() as conn:
        _insert_run(conn, 1, "SUCCESS", old_ts)
        _insert_snapshot(conn, 1, 100, 1, 100.0, old_ts)
        # recent snapshot references run 1 — prevents run deletion
        _insert_snapshot(conn, 1, 100, 1, 200.0, recent_ts)

    with Session(engine) as session:
        result = cleanup_expired_data(
            session, snapshot_retention_days=7, run_retention_days=7,
        )

    # Old snapshot deleted, recent one keeps run alive
    assert result["snapshot_deleted"] == 1
    assert result["run_deleted"] == 0


def test_recover_stale_running_and_pending_runs():
    engine = create_test_engine()
    now = datetime.now(ZoneInfo("Asia/Shanghai")).replace(tzinfo=None)
    old_ts = now - timedelta(hours=3)

    with engine.begin() as conn:
        _insert_run(conn, 1, "RUNNING", old_ts)
        _insert_run(conn, 2, "PENDING", old_ts)

    with Session(engine) as session:
        result = cleanup_expired_data(
            session,
            run_retention_days=7,
            active_run_timeout_minutes=120,
        )

    assert result["stale_run_recovered"] == 2
    assert result["run_deleted"] == 0
    with engine.connect() as conn:
        runs = conn.execute(text(
            "SELECT status, phase, error_type FROM collection_run ORDER BY id"
        )).all()
    assert runs == [
        ("FAILED", "RECOVER_TIMEOUT", "STALE_RUN_TIMEOUT"),
        ("FAILED", "RECOVER_TIMEOUT", "STALE_RUN_TIMEOUT"),
    ]


def test_preserve_recent_running_run():
    engine = create_test_engine()
    now = datetime.now(ZoneInfo("Asia/Shanghai")).replace(tzinfo=None)
    with engine.begin() as conn:
        _insert_run(conn, 1, "RUNNING", now)

    with Session(engine) as session:
        result = cleanup_expired_data(session, active_run_timeout_minutes=120)

    assert result["stale_run_recovered"] == 0
    with engine.connect() as conn:
        assert conn.scalar(text(
            "SELECT COUNT(*) FROM collection_run WHERE status = 'RUNNING'"
        )) == 1


def test_preserve_run_referenced_by_current_metric():
    engine = create_test_engine()
    now = datetime.now(UTC).replace(tzinfo=None)
    old_ts = now - timedelta(days=10)

    with engine.begin() as conn:
        _insert_run(conn, 1, "SUCCESS", old_ts)
        _insert_current(conn, 1, 100, 1, 100.0, old_ts)

    with Session(engine) as session:
        result = cleanup_expired_data(session, run_retention_days=7)

    assert result["run_deleted"] == 0
    with engine.connect() as conn:
        remaining = conn.execute(text("SELECT COUNT(*) FROM collection_run")).scalar()
    assert remaining == 1


def test_delete_unreferenced_expired_run():
    engine = create_test_engine()
    old_ts = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=10)

    with engine.begin() as conn:
        _insert_run(conn, 1, "SUCCESS", old_ts)

    with Session(engine) as session:
        result = cleanup_expired_data(session, run_retention_days=7)

    assert result["run_deleted"] == 1


def test_no_op_when_nothing_expired():
    engine = create_test_engine()
    now = datetime.now(UTC).replace(tzinfo=None)

    with engine.begin() as conn:
        _insert_run(conn, 1, "SUCCESS", now)
        _insert_snapshot(conn, 1, 100, 1, 100.0, now)

    with Session(engine) as session:
        result = cleanup_expired_data(session, snapshot_retention_days=7)

    assert result == {
        "snapshot_deleted": 0,
        "acc_deleted": 0,
        "stale_run_recovered": 0,
        "run_deleted": 0,
        "partition_drop_count": 0,
        "partition_create_count": 0,
    }


def test_daily_partition_helpers():
    day = _partition_day("p20260627")

    assert day == datetime(2026, 6, 27)
    assert _partition_day("p_future") is None
    assert _partition_day("p20261340") is None
    assert _daily_partition_clause(day) == (
        "PARTITION p20260627 VALUES LESS THAN ('2026-06-28')"
    )


@pytest.mark.parametrize(
    ("argument", "value"),
    [
        ("snapshot_retention_days", 0),
        ("run_retention_days", -1),
        ("acc_retention_days", 3651),
        ("active_run_timeout_minutes", 0),
    ],
)
def test_cleanup_rejects_unsafe_retention_values(argument, value):
    engine = create_test_engine()
    with Session(engine) as session:
        with pytest.raises(ValueError, match=argument):
            cleanup_expired_data(session, **{argument: value})
    engine.dispose()
