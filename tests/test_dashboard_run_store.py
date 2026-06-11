from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool

from infrastructure.dashboard_run_store import MySQLCollectionRunStore


@pytest.fixture()
def store():
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE collection_run (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    batch_no VARCHAR(64) NOT NULL UNIQUE,
                    run_type VARCHAR(16) NOT NULL DEFAULT 'REALTIME',
                    trigger_type VARCHAR(16) NOT NULL,
                    prefect_flow_run_id VARCHAR(36) UNIQUE,
                    status VARCHAR(16) NOT NULL DEFAULT 'PENDING',
                    phase VARCHAR(32) NOT NULL DEFAULT 'TRIGGER',
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
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
        )
    value = MySQLCollectionRunStore(engine=engine)
    try:
        yield value
    finally:
        value.close()
        engine.dispose()


def test_mysql_store_lifecycle(store):
    created = store.create("dashboard-test", "MANUAL")
    assert created["status"] == "PENDING"
    assert created["run_type"] == "REALTIME"

    running = store.update(
        "dashboard-test",
        status="RUNNING",
        phase="SESSION",
        started_at=datetime(2026, 6, 10, 9, 30),
    )
    assert running["status"] == "RUNNING"
    assert running["phase"] == "SESSION"

    success = store.update(
        "dashboard-test",
        status="SUCCESS",
        phase="SESSION_READY",
        session_status="reused",
        finished_at=datetime(2026, 6, 10, 9, 31),
    )
    assert success["status"] == "SUCCESS"
    assert success["session_status"] == "reused"
    assert store.latest()["batch_no"] == "dashboard-test"


def test_mysql_store_rejects_duplicate_batch_no(store):
    store.create("dashboard-duplicate", "SCHEDULED")

    with pytest.raises(ValueError, match="批次号已存在"):
        store.create("dashboard-duplicate", "SCHEDULED")


def test_mysql_store_rejects_unknown_update_fields(store):
    store.create("dashboard-fields", "MANUAL")

    with pytest.raises(ValueError, match="不允许更新"):
        store.update("dashboard-fields", password="secret")
