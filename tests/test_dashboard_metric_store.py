from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool

from services.dashboard_metric_store import (
    finalize_metric_run_in_session,
    MetricConflictError,
    MetricValueError,
    MetricWriteError,
    normalize_metric_rows,
    parse_metric_value,
    write_acc_metric_batch,
    write_metric_batch,
    write_metric_batch_in_session,
)


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
                CREATE TABLE collection_run (
                    id INTEGER PRIMARY KEY,
                    batch_no VARCHAR(64) NOT NULL UNIQUE,
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
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE indicator (
                    id INTEGER PRIMARY KEY,
                    code VARCHAR(100) NOT NULL UNIQUE,
                    name VARCHAR(200) NOT NULL,
                    enabled BOOLEAN NOT NULL,
                    source_active BOOLEAN NOT NULL DEFAULT 1,
                    indicator_type VARCHAR(16) NOT NULL DEFAULT 'SOURCE',
                    storage_mode VARCHAR(16) NOT NULL DEFAULT 'STORE',
                    removed_at DATETIME,
                    sort_order INTEGER NOT NULL DEFAULT 0,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE metric_current (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    area_id INTEGER NOT NULL,
                    indicator_id INTEGER NOT NULL,
                    collection_run_id INTEGER NOT NULL,
                    metric_value NUMERIC(20, 4) NOT NULL,
                    stat_date DATE NOT NULL,
                    collected_at DATETIME NOT NULL,
                    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(area_id, indicator_id)
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE metric_snapshot (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    collection_run_id INTEGER NOT NULL,
                    area_id INTEGER NOT NULL,
                    indicator_id INTEGER NOT NULL,
                    metric_value NUMERIC(20, 4) NOT NULL,
                    collected_at DATETIME NOT NULL,
                    UNIQUE(collection_run_id, area_id, indicator_id)
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE metric_acc (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    period_type VARCHAR(16) NOT NULL,
                    stat_date DATE NOT NULL,
                    area_id INTEGER NOT NULL,
                    indicator_id INTEGER NOT NULL,
                    collection_run_id INTEGER NOT NULL,
                    metric_value NUMERIC(20, 4) NOT NULL,
                    collected_at DATETIME NOT NULL,
                    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(period_type, stat_date, area_id, indicator_id)
                )
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
    return engine


def add_run(
    engine,
    run_id,
    batch_no,
    phase="AREA_VALIDATED",
    run_type="REALTIME",
):
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO collection_run
                    (id, batch_no, run_type, trigger_type, status, phase)
                VALUES
                    (:id, :batch_no, :run_type, 'MANUAL', 'RUNNING', :phase)
                """
            ),
            {
                "id": run_id,
                "batch_no": batch_no,
                "run_type": run_type,
                "phase": phase,
            },
        )


def test_parse_metric_value_accepts_integer_and_decimal():
    assert parse_metric_value("2,849") == Decimal("2849")
    assert parse_metric_value("1.2500") == Decimal("1.2500")
    assert parse_metric_value(Decimal("316.000000")) == Decimal("316.000000")


@pytest.mark.parametrize("value", [None, "", "--", "abc", "1.23456"])
def test_parse_metric_value_rejects_invalid_values(value):
    with pytest.raises(MetricValueError):
        parse_metric_value(value)


def test_normalize_deduplicates_equal_values_and_rejects_conflicts():
    result = normalize_metric_rows(
        [
            {"area_id": 1, "raw_value": "10"},
            {"area_id": 1, "raw_value": 10},
        ]
    )
    assert result == [{"area_id": 1, "metric_value": Decimal("10")}]

    with pytest.raises(MetricConflictError):
        normalize_metric_rows(
            [
                {"area_id": 1, "raw_value": "10"},
                {"area_id": 1, "raw_value": "11"},
            ]
        )


def test_write_metric_batch_inserts_snapshot_and_upserts_current():
    engine = create_test_engine()
    add_run(engine, 1, "batch-1")
    collected_at = datetime(2026, 6, 10, 23, 55)

    first = write_metric_batch(
        engine,
        "batch-1",
        "sgs_ajvwdz",
        [{"area_id": 101, "raw_value": "12"}],
        date(2026, 6, 10),
        collected_at,
    )
    add_run(engine, 2, "batch-2")
    second = write_metric_batch(
        engine,
        "batch-2",
        "sgs_ajvwdz",
        [{"area_id": 101, "raw_value": "15"}],
        date(2026, 6, 10),
        datetime(2026, 6, 10, 23, 59),
    )

    with engine.connect() as connection:
        current = connection.execute(
            text("SELECT collection_run_id, metric_value FROM metric_current")
        ).one()
        snapshots = connection.execute(
            text(
                "SELECT collection_run_id, metric_value "
                "FROM metric_snapshot ORDER BY collection_run_id"
            )
        ).all()
        runs = connection.execute(
            text(
                "SELECT batch_no, status, phase, current_upsert_count, "
                "snapshot_insert_count FROM collection_run ORDER BY id"
            )
        ).all()

    assert first["current_upsert_count"] == 1
    assert second["snapshot_insert_count"] == 1
    assert current == (2, 15)
    assert snapshots == [(1, 12), (2, 15)]
    assert runs == [
        ("batch-1", "SUCCESS", "COMPLETED", 1, 1),
        ("batch-2", "SUCCESS", "COMPLETED", 1, 1),
    ]
    engine.dispose()


def test_write_requires_area_validated_phase_and_rolls_back():
    engine = create_test_engine()
    add_run(engine, 1, "batch-invalid", phase="COLLECT")

    with pytest.raises(MetricWriteError, match="尚未完成区域校验"):
        write_metric_batch(
            engine,
            "batch-invalid",
            "sgs_ajvwdz",
            [{"area_id": 101, "raw_value": "12"}],
            date(2026, 6, 10),
            datetime(2026, 6, 10, 23, 55),
        )

    with engine.connect() as connection:
        assert (
            connection.execute(text("SELECT COUNT(*) FROM metric_current")).scalar_one()
            == 0
        )
        assert (
            connection.execute(
                text("SELECT COUNT(*) FROM metric_snapshot")
            ).scalar_one()
            == 0
        )
    engine.dispose()


def test_realtime_write_rejects_component_indicator():
    engine = create_test_engine()
    add_run(engine, 1, "component-indicator")
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                UPDATE indicator
                SET storage_mode = 'COMPONENT'
                WHERE code = 'sgs_ajvwdz'
                """
            )
        )

    with pytest.raises(MetricWriteError, match="启用指标不存在"):
        write_metric_batch(
            engine,
            "component-indicator",
            "sgs_ajvwdz",
            [{"area_id": 101, "raw_value": "12"}],
            date(2026, 6, 10),
            datetime(2026, 6, 10, 23, 55),
        )

    with engine.connect() as connection:
        assert (
            connection.execute(text("SELECT COUNT(*) FROM metric_current")).scalar_one()
            == 0
        )
        assert (
            connection.execute(
                text("SELECT COUNT(*) FROM metric_snapshot")
            ).scalar_one()
            == 0
        )
    engine.dispose()


def test_realtime_write_rejects_cumulative_run_type():
    engine = create_test_engine()
    add_run(engine, 1, "daily-as-realtime", run_type="DAILY")

    with pytest.raises(MetricWriteError, match="run_type=DAILY"):
        write_metric_batch(
            engine,
            "daily-as-realtime",
            "sgs_ajvwdz",
            [{"area_id": 101, "raw_value": "12"}],
            date(2026, 6, 10),
            datetime(2026, 6, 10, 23, 55),
        )

    with engine.connect() as connection:
        assert (
            connection.execute(text("SELECT COUNT(*) FROM metric_current")).scalar_one()
            == 0
        )
        assert (
            connection.execute(
                text("SELECT COUNT(*) FROM metric_snapshot")
            ).scalar_one()
            == 0
        )
    engine.dispose()


def test_write_daily_metric_batch_upserts_same_date():
    engine = create_test_engine()
    add_run(engine, 1, "daily-1", run_type="DAILY")
    first = write_acc_metric_batch(
        engine,
        "daily-1",
        "sgs_ajvwdz",
        [{"area_id": 101, "raw_value": "12"}],
        date(2026, 6, 10),
        datetime(2026, 6, 11, 8, 0),
    )
    add_run(engine, 2, "daily-2", run_type="DAILY")
    write_acc_metric_batch(
        engine,
        "daily-2",
        "sgs_ajvwdz",
        [{"area_id": 101, "raw_value": "15"}],
        date(2026, 6, 10),
        datetime(2026, 6, 11, 8, 5),
    )

    with engine.connect() as connection:
        daily = connection.execute(
            text("SELECT collection_run_id, metric_value " "FROM metric_acc")
        ).one()
        count = connection.execute(
            text("SELECT acc_upsert_count FROM collection_run WHERE id=2")
        ).scalar_one()

    assert first["acc_upsert_count"] == 1
    assert daily == (2, 15)
    assert count == 1
    engine.dispose()


def test_write_monthly_metric_does_not_replace_daily():
    engine = create_test_engine()
    add_run(engine, 1, "daily", run_type="DAILY")
    write_acc_metric_batch(
        engine,
        "daily",
        "sgs_ajvwdz",
        [{"area_id": 101, "raw_value": "12"}],
        date(2026, 6, 10),
        datetime(2026, 6, 11, 8, 0),
        period_type="DAY_ACC",
    )
    add_run(engine, 2, "monthly", run_type="MONTHLY")
    write_acc_metric_batch(
        engine,
        "monthly",
        "sgs_ajvwdz",
        [{"area_id": 101, "raw_value": "120"}],
        date(2026, 6, 10),
        datetime(2026, 6, 11, 8, 5),
        period_type="MONTH",
    )

    with engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT period_type, metric_value FROM metric_acc "
                "ORDER BY period_type"
            )
        ).all()

    assert rows == [("DAY_ACC", 12), ("MONTH", 120)]
    engine.dispose()


def test_write_multiple_realtime_indicators_in_one_run():
    engine = create_test_engine()
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO indicator (id, code, name, enabled, sort_order)
                VALUES (2, 'sgs_YDYDN_260410', '移动云电脑', 1, 20)
                """
            )
        )
    add_run(engine, 1, "multi-realtime")
    collected_at = datetime(2026, 6, 25, 11, 0)

    from sqlalchemy.orm import Session

    with Session(engine) as session, session.begin():
        first = write_metric_batch_in_session(
            session,
            dialect_name=engine.dialect.name,
            batch_no="multi-realtime",
            indicator_code="sgs_ajvwdz",
            normalized_rows=[{"area_id": 101, "metric_value": Decimal("12")}],
            stat_date=date(2026, 6, 25),
            collected_at=collected_at,
            finalize_run=False,
        )
        second = write_metric_batch_in_session(
            session,
            dialect_name=engine.dialect.name,
            batch_no="multi-realtime",
            indicator_code="sgs_YDYDN_260410",
            normalized_rows=[{"area_id": 101, "metric_value": Decimal("7")}],
            stat_date=date(2026, 6, 25),
            collected_at=collected_at,
            finalize_run=False,
        )
        finalize_metric_run_in_session(
            session,
            batch_no="multi-realtime",
            expected_run_type="REALTIME",
            stat_date=date(2026, 6, 25),
            collected_at=collected_at,
            current_upsert_count=2,
            snapshot_insert_count=2,
        )

    with engine.connect() as connection:
        current_rows = connection.execute(
            text(
                "SELECT indicator_id, metric_value FROM metric_current "
                "ORDER BY indicator_id"
            )
        ).all()
        run = connection.execute(
            text(
                "SELECT status, phase, current_upsert_count, snapshot_insert_count "
                "FROM collection_run WHERE batch_no = 'multi-realtime'"
            )
        ).one()

    assert first["current_upsert_count"] == 1
    assert second["current_upsert_count"] == 1
    assert current_rows == [(1, 12), (2, 7)]
    assert run == ("SUCCESS", "COMPLETED", 2, 2)
    engine.dispose()
