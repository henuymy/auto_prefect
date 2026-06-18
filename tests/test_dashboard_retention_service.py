"""Tests for the data retention service."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from services.dashboard_retention_service import cleanup_expired_data


def create_test_engine():
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
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
            CREATE TABLE metric_snapshot (
                id INTEGER PRIMARY KEY,
                collection_run_id INTEGER NOT NULL,
                area_id INTEGER NOT NULL,
                indicator_id INTEGER NOT NULL,
                metric_value NUMERIC(20,4) NOT NULL,
                collected_at DATETIME NOT NULL
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
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """))
    return engine


def _insert_run(conn, run_id, status="SUCCESS", created_at=None):
    created = created_at.isoformat(sep=" ") if hasattr(created_at, "isoformat") else created_at
    conn.execute(
        text(
            "INSERT INTO collection_run (id, batch_no, trigger_type, status, phase, created_at) "
            "VALUES (:id, :batch, 'SCHEDULED', :status, 'COMPLETED', :created)"
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


def test_preserve_running_and_pending_runs():
    engine = create_test_engine()
    now = datetime.now(UTC).replace(tzinfo=None)
    old_ts = now - timedelta(days=10)

    with engine.begin() as conn:
        _insert_run(conn, 1, "RUNNING", old_ts)
        _insert_run(conn, 2, "PENDING", old_ts)

    with Session(engine) as session:
        result = cleanup_expired_data(session, run_retention_days=7)

    assert result["run_deleted"] == 0


def test_no_op_when_nothing_expired():
    engine = create_test_engine()
    now = datetime.now(UTC).replace(tzinfo=None)

    with engine.begin() as conn:
        _insert_run(conn, 1, "SUCCESS", now)
        _insert_snapshot(conn, 1, 100, 1, 100.0, now)

    with Session(engine) as session:
        result = cleanup_expired_data(session, snapshot_retention_days=7)

    assert result == {"snapshot_deleted": 0, "acc_deleted": 0, "run_deleted": 0}
