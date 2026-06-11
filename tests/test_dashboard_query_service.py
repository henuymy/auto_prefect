from __future__ import annotations

from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool

from services.dashboard_query_service import get_current_wide_table


def create_test_engine():
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE area (
                    id INTEGER PRIMARY KEY,
                    area_code VARCHAR(100) NOT NULL,
                    area_name VARCHAR(200) NOT NULL,
                    level_type VARCHAR(20) NOT NULL,
                    level_no INTEGER NOT NULL,
                    parent_id INTEGER,
                    enabled BOOLEAN NOT NULL,
                    sort_order INTEGER NOT NULL DEFAULT 0,
                    last_seen_at DATETIME,
                    missing_count INTEGER NOT NULL DEFAULT 0,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE indicator (
                    id INTEGER PRIMARY KEY,
                    code VARCHAR(100) NOT NULL,
                    name VARCHAR(200) NOT NULL,
                    enabled BOOLEAN NOT NULL,
                    sort_order INTEGER NOT NULL DEFAULT 0,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
        )
        connection.execute(
            text(
                """
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
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE metric_current (
                    id INTEGER PRIMARY KEY,
                    area_id INTEGER NOT NULL,
                    indicator_id INTEGER NOT NULL,
                    collection_run_id INTEGER NOT NULL,
                    metric_value NUMERIC(20,4) NOT NULL,
                    stat_date DATE NOT NULL,
                    collected_at DATETIME NOT NULL,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO area
                    (id, area_code, area_name, level_type, level_no, parent_id, enabled)
                VALUES
                    (1, 'A', '郑州市', 'CITY', 1, NULL, 1),
                    (2, 'AQ', '中原区', 'BRANCH', 2, 1, 1)
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO indicator (id, code, name, enabled, sort_order)
                VALUES (1, 'sgs_ajvwdz', '爱家亲情网(V网版)', 1, 10)
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO collection_run
                    (id, batch_no, trigger_type, status, phase, stat_date, finished_at)
                VALUES
                    (1, 'batch-1', 'MANUAL', 'SUCCESS', 'COMPLETED',
                     '2026-06-11', '2026-06-11 10:05:08')
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO collection_run
                    (id, batch_no, run_type, trigger_type, status, phase,
                     stat_date, finished_at)
                VALUES
                    (2, 'monthly-newer', 'REALTIME', 'MANUAL', 'SUCCESS',
                     'COMPLETED', '2026-05-31', '2026-06-11 11:05:08')
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO metric_current
                    (id, area_id, indicator_id, collection_run_id, metric_value,
                     stat_date, collected_at)
                VALUES
                    (1, 2, 1, 1, 25, '2026-06-11', '2026-06-11 10:05:08')
                """
            )
        )
    return engine


def test_current_wide_table_builds_dynamic_metric_columns():
    engine = create_test_engine()

    result = get_current_wide_table(engine)

    assert result["latest_run"]["batch_no"] == "batch-1"
    assert result["indicators"][0]["code"] == "sgs_ajvwdz"
    assert result["row_count"] == 2
    assert result["rows"][0]["metrics"] == {"sgs_ajvwdz": None}
    assert result["rows"][1]["metrics"] == {"sgs_ajvwdz": 25}
    engine.dispose()


def test_current_wide_table_filters_level_and_parent():
    engine = create_test_engine()

    result = get_current_wide_table(
        engine,
        level_type="branch",
        parent_id=1,
    )

    assert result["row_count"] == 1
    assert result["rows"][0]["area_code"] == "AQ"
    engine.dispose()
