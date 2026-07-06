from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool

from services import dashboard_query_service

from services.dashboard_custom_indicator_service import (
    compose_store_metric_rows,
    delete_custom_indicator,
    load_metric_indicator_plan,
    update_indicator_settings,
    upsert_custom_indicator,
)
from services.dashboard_metric_store import MetricValueError
from services.dashboard_query_service import (
    get_acc_wide_table,
    get_current_wide_table,
    get_current_with_changes,
    get_dashboard_matrix_page,
    get_dashboard_overview,
    get_drill_down,
    get_indicator_catalog,
    get_latest_dashboard_run,
    get_historical_matrix_page,
    get_historical_with_changes,
    get_history_options,
    get_history_range,
    parse_change_window_minutes,
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
                    source_active BOOLEAN NOT NULL DEFAULT 1,
                    indicator_type VARCHAR(16) NOT NULL DEFAULT 'SOURCE',
                    storage_mode VARCHAR(16) NOT NULL DEFAULT 'STORE',
                    removed_at DATETIME,
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
                    structure_change_summary TEXT,
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
                CREATE TABLE metric_snapshot (
                    id INTEGER PRIMARY KEY,
                    collection_run_id INTEGER NOT NULL,
                    area_id INTEGER NOT NULL,
                    indicator_id INTEGER NOT NULL,
                    metric_value NUMERIC(20,4) NOT NULL,
                    collected_at DATETIME NOT NULL
                )
                """
            )
        )
        connection.execute(
            text(
                """
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
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE metric_target (
                    id INTEGER PRIMARY KEY,
                    period_type VARCHAR(16) NOT NULL,
                    area_id INTEGER NOT NULL,
                    indicator_id INTEGER NOT NULL,
                    target_value NUMERIC(20,4) NOT NULL,
                    enabled BOOLEAN NOT NULL DEFAULT 1,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE custom_indicator_component (
                    id INTEGER PRIMARY KEY,
                    custom_indicator_id INTEGER NOT NULL,
                    source_indicator_id INTEGER NOT NULL,
                    coefficient NUMERIC(20,4) NOT NULL DEFAULT 1,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
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
        # Add snapshot history for change computation
        anchor = datetime(2026, 6, 11, 10, 5, 8)
        connection.execute(
            text(
                """
                INSERT INTO metric_snapshot
                    (id, collection_run_id, area_id, indicator_id,
                     metric_value, collected_at)
                VALUES
                    (:snap_id_1, 1, 2, 1, 20, :ts_60min_ago),
                    (:snap_id_2, 1, 2, 1, 23, :ts_10min_ago)
                """
            ),
            {
                "snap_id_1": 1,
                "snap_id_2": 2,
                "ts_60min_ago": (anchor - timedelta(minutes=60)).isoformat(sep=" "),
                "ts_10min_ago": (anchor - timedelta(minutes=10)).isoformat(sep=" "),
            },
        )
        # Add acc data
        connection.execute(
            text(
                """
                INSERT INTO metric_acc
                    (id, period_type, stat_date, area_id, indicator_id,
                     collection_run_id, metric_value, collected_at)
                VALUES
                    (1, 'DAY_ACC', '2026-06-10', 2, 1, 2,
                     80, '2026-06-11 11:05:08')
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO metric_target
                    (id, period_type, area_id, indicator_id, target_value, enabled)
                VALUES
                    (1, 'REALTIME', 2, 1, 50, 1),
                    (2, 'DAY_ACC', 2, 1, 100, 1)
                """
            )
        )
    return engine


def test_historical_query_restores_nearest_completed_run():
    engine = create_test_engine()
    with engine.begin() as connection:
        connection.execute(text(
            "UPDATE collection_run SET snapshot_insert_count=2 WHERE id=1"
        ))

    result = get_historical_with_changes(
        engine,
        as_of=datetime(2026, 6, 11, 10, 6),
        level_type="BRANCH",
        change_windows=[10],
        indicator_codes=["sgs_ajvwdz"],
    )

    assert result["data_mode"] == "HISTORY"
    assert result["latest_run"]["batch_no"] == "batch-1"
    assert result["row_count"] == 1
    assert result["rows"][0]["metrics"]["sgs_ajvwdz"] == 23
    assert result["rows"][0]["targets"]["sgs_ajvwdz"] == 50
    assert result["coverage"]["levels"]["BRANCH"] == {
        "expected_areas": 1,
        "snapshot_areas": 1,
        "missing_areas": 0,
        "extra_areas": 0,
        "available_metric_cells": 1,
        "total_metric_cells": 1,
    }
    assert result["history_meta"]["is_fallback"] is True
    assert result["history_meta"]["fallback_seconds"] == 60


def test_historical_query_restores_zero_change_sparse_run():
    engine = create_test_engine()
    with engine.begin() as connection:
        connection.execute(text("""
            INSERT INTO collection_run
                (id, batch_no, run_type, trigger_type, status, phase,
                 stat_date, started_at, finished_at, current_upsert_count,
                 snapshot_insert_count)
            VALUES
                (3, 'sparse-no-change', 'REALTIME', 'SCHEDULED', 'SUCCESS',
                 'COMPLETED', '2026-06-11', '2026-06-11 10:10:00',
                 '2026-06-11 10:10:08', 1, 0)
        """))

    result = get_historical_with_changes(
        engine,
        as_of=datetime(2026, 6, 11, 10, 11),
        level_type="BRANCH",
        indicator_codes=["sgs_ajvwdz"],
    )
    options = get_history_options(engine, indicator_codes=["sgs_ajvwdz"])

    assert result["latest_run"]["batch_no"] == "sparse-no-change"
    assert result["rows"][0]["metrics"]["sgs_ajvwdz"] == 23
    assert result["rows"][0]["collection_run_id"] == 3
    assert result["rows"][0]["collected_at"] == "2026-06-11T10:10:08.000"
    assert "10:10" in options["dates"][0]["times"]
    engine.dispose()


def test_historical_matrix_and_range():
    engine = create_test_engine()
    with engine.begin() as connection:
        connection.execute(text(
            "UPDATE collection_run SET snapshot_insert_count=2 WHERE id=1"
        ))

    history_range = get_history_range(engine)
    history_options = get_history_options(engine)
    missing_indicator_options = get_history_options(
        engine,
        indicator_codes=["not_collected"],
    )
    matrix = get_historical_matrix_page(
        engine,
        as_of=datetime(2026, 6, 11, 10, 6),
        level_type="BRANCH",
        scope_mode="all",
        indicator_codes=["sgs_ajvwdz"],
        change_window=10,
        sort_indicator="sgs_ajvwdz",
        sort_mode="doneDesc",
    )

    assert history_range["earliest_at"] == "2026-06-11T10:05:08.000"
    assert history_range["latest_at"] == "2026-06-11T11:05:08.000"
    assert history_options == {
        "dates": [{"date": "2026-06-11", "times": ["11:05", "10:05"]}],
        "date_count": 1,
    }
    assert missing_indicator_options == {"dates": [], "date_count": 0}
    assert matrix["total"] == 1
    assert matrix["rows"][0]["area_name"] == "中原区"


def test_historical_query_keeps_disabled_snapshot_entities():
    engine = create_test_engine()
    with engine.begin() as connection:
        connection.execute(text(
            "UPDATE collection_run SET snapshot_insert_count=3 WHERE id=1"
        ))
        connection.execute(text("""
            INSERT INTO area
                (id, area_code, area_name, level_type, level_no, parent_id, enabled)
            VALUES (3, 'OLD', '已停用分公司', 'BRANCH', 2, 1, 0)
        """))
        connection.execute(text("""
            INSERT INTO indicator
                (id, code, name, enabled, source_active, storage_mode, sort_order)
            VALUES (2, 'disabled_metric', '已停用指标', 0, 0, 'STORE', 20)
        """))
        connection.execute(text("""
            INSERT INTO metric_snapshot
                (id, collection_run_id, area_id, indicator_id, metric_value, collected_at)
            VALUES (3, 1, 3, 2, 77, '2026-06-11 09:55:08')
        """))

    result = get_historical_with_changes(
        engine,
        as_of=datetime(2026, 6, 11, 10, 6),
        level_type="BRANCH",
        scope_mode="all",
    )

    assert any(item["code"] == "disabled_metric" for item in result["indicators"])
    archived_row = next(row for row in result["rows"] if row["area_id"] == 3)
    assert archived_row["metrics"]["disabled_metric"] == 77
    assert result["coverage"]["levels"]["BRANCH"]["extra_areas"] == 1


def test_historical_query_reads_snapshot_from_previous_day_partition():
    engine = create_test_engine()
    with engine.begin() as connection:
        connection.execute(text("""
            UPDATE collection_run
            SET started_at='2026-06-10 23:59:50',
                finished_at='2026-06-11 00:00:10',
                snapshot_insert_count=1
            WHERE id=1
        """))
        connection.execute(text("DELETE FROM metric_snapshot WHERE id=1"))
        connection.execute(text("""
            UPDATE metric_snapshot
            SET collected_at='2026-06-10 23:59:55', metric_value=23
            WHERE id=2
        """))

    result = get_historical_with_changes(
        engine,
        as_of=datetime(2026, 6, 11, 0, 0, 11),
        level_type="BRANCH",
        indicator_codes=["sgs_ajvwdz"],
    )

    assert result["latest_run"]["batch_no"] == "batch-1"
    assert result["rows"][0]["metrics"]["sgs_ajvwdz"] == 23


def test_historical_query_before_first_completed_run_is_empty():
    engine = create_test_engine()
    result = get_historical_with_changes(
        engine,
        as_of=datetime(2026, 6, 11, 8, 0),
        level_type="BRANCH",
    )

    assert result["latest_run"] is None
    assert result["rows"] == []


def test_historical_query_uses_started_at_when_batch_finishes_late():
    engine = create_test_engine()
    with engine.begin() as connection:
        connection.execute(text("""
            UPDATE collection_run
            SET started_at='2026-06-11 10:05:10',
                finished_at='2026-06-11 10:08:30',
                snapshot_insert_count=2
            WHERE id=1
        """))

    result = get_historical_with_changes(
        engine,
        as_of=datetime(2026, 6, 11, 10, 5, 59, 999000),
        level_type="BRANCH",
        indicator_codes=["sgs_ajvwdz"],
    )

    assert result["latest_run"]["batch_no"] == "batch-1"
    assert result["latest_run"]["started_at"] == "2026-06-11T10:05:10.000"
    assert result["latest_run"]["finished_at"] == "2026-06-11T10:08:30.000"


def test_indicator_catalog_keeps_enabled_archived_indicator_visible():
    engine = create_test_engine()
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                UPDATE indicator
                SET source_active = 0, removed_at = '2026-06-24 10:11:07'
                WHERE code = 'sgs_ajvwdz'
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO indicator
                    (id, code, name, enabled, source_active, sort_order)
                VALUES
                    (2, 'disabled-archived', '已停用归档指标', 0, 0, 20)
                """
            )
        )

    result = get_indicator_catalog(engine)
    codes = [row["code"] for row in result["indicators"]]

    assert "sgs_ajvwdz" in codes
    assert "disabled-archived" not in codes


def test_current_wide_table_builds_dynamic_metric_columns():
    engine = create_test_engine()

    result = get_current_wide_table(engine)

    assert result["latest_run"]["batch_no"] == "batch-1"
    assert result["indicators"][0]["code"] == "sgs_ajvwdz"
    assert result["row_count"] == 2
    assert result["rows"][0]["metrics"] == {"sgs_ajvwdz": None}
    assert result["rows"][1]["metrics"] == {"sgs_ajvwdz": 25}
    assert result["rows"][1]["targets"] == {"sgs_ajvwdz": 50}
    engine.dispose()


def test_current_wide_table_only_returns_store_indicators():
    engine = create_test_engine()
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

    result = get_current_wide_table(engine)

    assert result["indicators"] == []
    assert all(row["metrics"] == {} for row in result["rows"])
    engine.dispose()


def test_current_wide_table_computes_custom_indicator_sum():
    engine = create_test_engine()
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO indicator
                    (id, code, name, enabled, source_active, sort_order)
                VALUES
                    (2, 'cloud_pc', '移动云电脑', 1, 1, 20),
                    (3, 'custom_total', '自建总量', 1, 0, 30)
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO metric_current
                    (id, area_id, indicator_id, collection_run_id,
                     metric_value, stat_date, collected_at)
                VALUES
                    (2, 2, 2, 1, 9, '2026-06-11', '2026-06-11 10:05:08')
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO metric_target
                    (id, period_type, area_id, indicator_id, target_value, enabled)
                VALUES
                    (3, 'REALTIME', 2, 2, 10, 1)
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO custom_indicator_component
                    (id, custom_indicator_id, source_indicator_id, coefficient)
                VALUES
                    (1, 3, 1, 1),
                    (2, 3, 2, 2)
                """
            )
        )

    result = get_current_wide_table(engine, indicator_codes=["custom_total"])
    row = next(row for row in result["rows"] if row["area_code"] == "AQ")

    assert [indicator["code"] for indicator in result["indicators"]] == [
        "custom_total"
    ]
    assert row["metrics"] == {"custom_total": 43}
    assert row["targets"] == {"custom_total": 70}
    engine.dispose()


def test_indicator_catalog_marks_component_owner_as_custom():
    engine = create_test_engine()
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO indicator
                    (id, code, name, enabled, source_active,
                     indicator_type, storage_mode, sort_order)
                VALUES
                    (3, 'custom_total', '自建总量', 1, 0,
                     'SOURCE', 'STORE', 30)
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO custom_indicator_component
                    (id, custom_indicator_id, source_indicator_id, coefficient)
                VALUES
                    (1, 3, 1, 1)
                """
            )
        )

    result = get_indicator_catalog(engine)
    indicator = next(
        item for item in result["indicators"] if item["code"] == "custom_total"
    )

    assert indicator["indicator_type"] == "CUSTOM"
    engine.dispose()


def test_upsert_rejects_component_owner_even_if_marked_source():
    engine = create_test_engine()
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO indicator
                    (id, code, name, enabled, source_active,
                     indicator_type, storage_mode, sort_order)
                VALUES
                    (3, 'custom_total', '自建总量', 1, 0,
                     'SOURCE', 'STORE', 30)
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO custom_indicator_component
                    (id, custom_indicator_id, source_indicator_id, coefficient)
                VALUES
                    (1, 3, 1, 1)
                """
            )
        )

    with pytest.raises(ValueError, match="组成指标必须是源指标"):
        upsert_custom_indicator(
            engine,
            code="custom_nested",
            name="嵌套自定义",
            components=[
                {"source_code": "custom_total", "coefficient": "1"}
            ],
        )

    engine.dispose()


def test_metric_indicator_plan_requests_components_but_stores_custom_only():
    engine = create_test_engine()
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                UPDATE indicator
                SET storage_mode = 'COMPONENT'
                WHERE id = 1
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO indicator
                    (id, code, name, enabled, source_active,
                     indicator_type, storage_mode, sort_order)
                VALUES
                    (2, 'custom_total', '自建总量', 1, 0,
                     'CUSTOM', 'STORE', 20)
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO custom_indicator_component
                    (id, custom_indicator_id, source_indicator_id, coefficient)
                VALUES
                    (1, 2, 1, 2)
                """
            )
        )

    plan = load_metric_indicator_plan(engine)
    rows = compose_store_metric_rows(
        [{"area_id": 2, "sgs_ajvwdz": 25}],
        plan["custom_components"],
    )

    assert plan["request_codes"] == ["sgs_ajvwdz"]
    assert plan["store_codes"] == ["custom_total"]
    assert rows[0]["custom_total"] == 50
    engine.dispose()


def test_custom_metric_rows_round_to_metric_scale():
    rows = compose_store_metric_rows(
        [{"area_id": 2, "a": Decimal("316"), "b": Decimal("0")}],
        {
            "custom_total": [
                {"source_code": "a", "coefficient": Decimal("1.0000")},
                {"source_code": "b", "coefficient": Decimal("0.3333")},
            ]
        },
    )

    assert rows[0]["custom_total"] == Decimal("316.0000")


def test_custom_metric_rows_reject_invalid_source_value():
    with pytest.raises(MetricValueError):
        compose_store_metric_rows(
            [{"area_id": 2, "a": "bad"}],
            {"custom_total": [{"source_code": "a", "coefficient": Decimal("1")}]},
        )


def test_upsert_custom_indicator_rejects_over_precise_coefficient():
    engine = create_test_engine()

    with pytest.raises(ValueError, match="系数最多保留4位小数"):
        upsert_custom_indicator(
            engine,
            code="custom_bad",
            name="超精度系数",
            components=[
                {"source_code": "sgs_ajvwdz", "coefficient": "0.33333"}
            ],
        )

    engine.dispose()


def test_delete_custom_indicator_removes_indicator_components_and_values():
    engine = create_test_engine()
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO indicator
                    (id, code, name, enabled, source_active,
                     indicator_type, storage_mode, sort_order)
                VALUES
                    (3, 'custom_total', '自建总量', 1, 0,
                     'CUSTOM', 'STORE', 30)
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO custom_indicator_component
                    (id, custom_indicator_id, source_indicator_id, coefficient)
                VALUES
                    (1, 3, 1, 1)
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO metric_current
                    (id, area_id, indicator_id, collection_run_id,
                     metric_value, stat_date, collected_at)
                VALUES
                    (2, 2, 3, 1, 25, '2026-06-11', '2026-06-11 10:05:08')
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO metric_snapshot
                    (id, collection_run_id, area_id, indicator_id,
                     metric_value, collected_at)
                VALUES
                    (3, 1, 2, 3, 20, '2026-06-11 09:05:08')
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO metric_acc
                    (id, period_type, stat_date, area_id, indicator_id,
                     collection_run_id, metric_value, collected_at)
                VALUES
                    (2, 'DAY_ACC', '2026-06-10', 2, 3, 2,
                     100, '2026-06-11 11:05:08')
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO metric_target
                    (id, period_type, area_id, indicator_id, target_value, enabled)
                VALUES
                    (3, 'REALTIME', 2, 3, 50, 1)
                """
            )
        )

    assert delete_custom_indicator(engine, "custom_total") == {
        "code": "custom_total",
        "deleted": True,
    }
    with engine.connect() as connection:
        assert connection.execute(
            text("SELECT COUNT(*) FROM indicator WHERE id = 3")
        ).scalar_one() == 0
        assert connection.execute(
            text("SELECT COUNT(*) FROM custom_indicator_component WHERE custom_indicator_id = 3")
        ).scalar_one() == 0
        for table_name in ("metric_current", "metric_snapshot", "metric_acc", "metric_target"):
            assert connection.execute(
                text(f"SELECT COUNT(*) FROM {table_name} WHERE indicator_id = 3")
            ).scalar_one() == 0
    engine.dispose()


def test_source_setting_update_rejects_custom_indicator():
    engine = create_test_engine()
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO indicator
                    (id, code, name, enabled, source_active,
                     indicator_type, storage_mode, sort_order)
                VALUES
                    (3, 'custom_total', '自建总量', 1, 0,
                     'CUSTOM', 'STORE', 30)
                """
            )
        )

    with pytest.raises(ValueError, match="只允许修改源指标"):
        update_indicator_settings(engine, "custom_total", storage_mode="COMPONENT")

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


def test_current_with_changes_includes_change_fields():
    engine = create_test_engine()

    result = get_current_with_changes(engine)

    assert result["indicators"][0]["code"] == "sgs_ajvwdz"
    # Row with data should have changes computed per indicator
    row_with_data = [r for r in result["rows"] if r["area_code"] == "AQ"][0]
    assert "changes" in row_with_data
    per_indicator = row_with_data["changes"]
    assert "sgs_ajvwdz" in per_indicator
    changes = per_indicator["sgs_ajvwdz"]
    for key in ("change_5min", "change_15min", "change_30min", "change_60min"):
        assert key in changes
        assert "value" in changes[key]
        assert "rate" in changes[key]
    # 60min ago value was 20, current=25 => delta=5, rate=0.25
    assert changes["change_60min"]["value"] == 5.0
    assert changes["change_60min"]["rate"] == 0.25
    engine.dispose()


def test_parse_change_window_minutes_uses_shared_rules():
    assert parse_change_window_minutes(None) is None
    assert parse_change_window_minutes("5,15,15,30,60,90") == [5, 15, 30, 60]

    for value in ("abc", "1", "6", "1445"):
        try:
            parse_change_window_minutes(value)
        except ValueError:
            pass
        else:
            raise AssertionError(f"expected invalid change window: {value}")


def test_current_with_changes_supports_custom_windows():
    engine = create_test_engine()

    result = get_current_with_changes(engine, change_windows=[10, 60])

    row_with_data = [r for r in result["rows"] if r["area_code"] == "AQ"][0]
    changes = row_with_data["changes"]["sgs_ajvwdz"]
    assert set(changes) == {"change_10min", "change_60min"}
    assert changes["change_10min"]["value"] == 2.0
    assert changes["change_60min"]["value"] == 5.0
    engine.dispose()


def test_current_with_changes_computes_custom_indicator_delta():
    engine = create_test_engine()
    with engine.begin() as connection:
        anchor = datetime(2026, 6, 11, 10, 5, 8)
        connection.execute(
            text(
                """
                INSERT INTO indicator
                    (id, code, name, enabled, source_active, sort_order)
                VALUES
                    (2, 'cloud_pc', '移动云电脑', 1, 1, 20),
                    (3, 'custom_total', '自建总量', 1, 0, 30)
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO metric_current
                    (id, area_id, indicator_id, collection_run_id,
                     metric_value, stat_date, collected_at)
                VALUES
                    (2, 2, 2, 1, 9, '2026-06-11', '2026-06-11 10:05:08')
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO metric_snapshot
                    (id, collection_run_id, area_id, indicator_id,
                     metric_value, collected_at)
                VALUES
                    (3, 1, 2, 2, 4, :ts_60min_ago)
                """
            ),
            {"ts_60min_ago": (anchor - timedelta(minutes=60)).isoformat(sep=" ")},
        )
        connection.execute(
            text(
                """
                INSERT INTO custom_indicator_component
                    (id, custom_indicator_id, source_indicator_id, coefficient)
                VALUES
                    (1, 3, 1, 1),
                    (2, 3, 2, 2)
                """
            )
        )

    result = get_current_with_changes(
        engine,
        indicator_codes=["custom_total"],
        change_windows=[60],
    )
    row = next(row for row in result["rows"] if row["area_code"] == "AQ")

    assert row["metrics"]["custom_total"] == 43
    assert row["changes"]["custom_total"]["change_60min"]["value"] == 15.0
    engine.dispose()


def test_current_with_changes_prefers_stored_custom_snapshot():
    engine = create_test_engine()
    with engine.begin() as connection:
        anchor = datetime(2026, 6, 11, 10, 5, 8)
        connection.execute(
            text(
                """
                INSERT INTO indicator
                    (id, code, name, enabled, source_active, indicator_type,
                     storage_mode, sort_order)
                VALUES
                    (2, 'source_only', '只参与计算源指标', 0, 1, 'SOURCE',
                     'COMPONENT', 20),
                    (3, 'stored_custom', '已落库自定义指标', 1, 0, 'CUSTOM',
                     'STORE', 30)
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO metric_current
                    (id, area_id, indicator_id, collection_run_id,
                     metric_value, stat_date, collected_at)
                VALUES
                    (2, 2, 3, 1, 12, '2026-06-11', '2026-06-11 10:05:08')
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO metric_snapshot
                    (id, collection_run_id, area_id, indicator_id,
                     metric_value, collected_at)
                VALUES
                    (3, 1, 2, 3, 7, :ts_60min_ago)
                """
            ),
            {"ts_60min_ago": (anchor - timedelta(minutes=60)).isoformat(sep=" ")},
        )
        connection.execute(
            text(
                """
                INSERT INTO custom_indicator_component
                    (id, custom_indicator_id, source_indicator_id, coefficient)
                VALUES
                    (1, 3, 2, 1)
                """
            )
        )

    result = get_current_with_changes(
        engine,
        indicator_codes=["stored_custom"],
        change_windows=[60],
    )
    row = next(row for row in result["rows"] if row["area_code"] == "AQ")

    assert row["metrics"]["stored_custom"] == 12
    assert row["changes"]["stored_custom"]["change_60min"]["value"] == 5.0
    engine.dispose()


def test_current_with_changes_supports_windows_beyond_default_lookback():
    engine = create_test_engine()
    with engine.begin() as connection:
        anchor = datetime(2026, 6, 11, 10, 5, 8)
        connection.execute(
            text(
                """
                INSERT INTO metric_snapshot
                    (id, collection_run_id, area_id, indicator_id,
                     metric_value, collected_at)
                VALUES
                    (3, 1, 2, 1, 19, :ts_180min_ago)
                """
            ),
            {
                "ts_180min_ago": (anchor - timedelta(minutes=180)).isoformat(sep=" "),
            },
        )

    result = get_current_with_changes(engine, change_windows=[180])

    row_with_data = [r for r in result["rows"] if r["area_code"] == "AQ"][0]
    changes = row_with_data["changes"]["sgs_ajvwdz"]
    assert set(changes) == {"change_180min"}
    assert changes["change_180min"]["value"] == 6.0
    engine.dispose()


def test_current_with_changes_carries_forward_value_before_cutoff():
    engine = create_test_engine()
    with engine.begin() as connection:
        connection.execute(text("DELETE FROM metric_snapshot"))
        anchor = datetime(2026, 6, 11, 10, 5, 8)
        connection.execute(
            text(
                """
                INSERT INTO metric_snapshot
                    (id, collection_run_id, area_id, indicator_id,
                     metric_value, collected_at)
                VALUES
                    (1, 1, 2, 1, 20, :stale_ts)
                """
            ),
            {
                "stale_ts": (anchor - timedelta(minutes=65)).isoformat(sep=" "),
            },
        )

    result = get_current_with_changes(engine)
    row_with_data = [r for r in result["rows"] if r["area_code"] == "AQ"][0]

    assert row_with_data["changes"]["sgs_ajvwdz"]["change_60min"] == {
        "value": 5.0,
        "rate": 0.25,
    }
    engine.dispose()


def test_dashboard_overview_keeps_nearest_snapshot_tolerance():
    engine = create_test_engine()
    with engine.begin() as connection:
        connection.execute(text("DELETE FROM metric_snapshot"))
        anchor = datetime(2026, 6, 11, 10, 5, 8)
        connection.execute(
            text(
                """
                INSERT INTO metric_snapshot
                    (id, collection_run_id, area_id, indicator_id,
                     metric_value, collected_at)
                VALUES
                    (1, 1, 2, 1, 19, :far_before_cutoff),
                    (2, 1, 2, 1, 21, :near_after_cutoff)
                """
            ),
            {
                "far_before_cutoff": (
                    anchor - timedelta(minutes=5, seconds=90)
                ).isoformat(sep=" "),
                "near_after_cutoff": (
                    anchor - timedelta(minutes=5) + timedelta(seconds=2)
                ).isoformat(sep=" "),
            },
        )

    result = get_dashboard_overview(engine, branch_code="AQ", change_windows=[5])
    row_with_data = [r for r in result["rows"] if r["area_code"] == "AQ"][0]

    assert row_with_data["changes"]["sgs_ajvwdz"]["change_5min"]["value"] == 4.0
    engine.dispose()


def test_drill_down_returns_latest_run_metadata():
    engine = create_test_engine()

    result = get_drill_down(engine, parent_id=1, parent_level="CITY")

    assert result["latest_run"]["batch_no"] == "batch-1"
    assert result["latest_run"]["finished_at"].startswith("2026-06-11T10:05:08")
    engine.dispose()


def test_overview_and_drill_down_return_all_enabled_indicators():
    engine = create_test_engine()
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO indicator
                    (id, code, name, enabled, source_active, sort_order)
                VALUES
                    (2, 'sgs_YDYDN_260410', '移动云电脑', 1, 1, 20)
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO metric_current
                    (id, area_id, indicator_id, collection_run_id,
                     metric_value, stat_date, collected_at, updated_at)
                VALUES
                    (2, 2, 2, 1, 9, '2026-06-11',
                     '2026-06-11 10:05:08', '2026-06-11 10:05:08')
                """
            )
        )

    overview = get_dashboard_overview(engine, branch_code="AQ", change_windows=[5])
    overview_row = [row for row in overview["rows"] if row["area_code"] == "AQ"][0]
    drill = get_drill_down(engine, parent_id=1, parent_level="CITY")
    drill_row = [row for row in drill["rows"] if row["area_code"] == "AQ"][0]

    assert overview_row["metrics"] == {
        "sgs_ajvwdz": 25,
        "sgs_YDYDN_260410": 9,
    }
    assert drill_row["metrics"] == overview_row["metrics"]
    engine.dispose()


def test_overview_and_drill_down_filter_selected_indicators():
    engine = create_test_engine()
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO indicator
                    (id, code, name, enabled, source_active, sort_order)
                VALUES
                    (2, 'sgs_YDYDN_260410', '移动云电脑', 1, 1, 20)
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO metric_current
                    (id, area_id, indicator_id, collection_run_id,
                     metric_value, stat_date, collected_at, updated_at)
                VALUES
                    (2, 2, 2, 1, 9, '2026-06-11',
                     '2026-06-11 10:05:08', '2026-06-11 10:05:08')
                """
            )
        )

    overview = get_dashboard_overview(
        engine,
        branch_code="AQ",
        change_windows=[5],
        indicator_codes=["sgs_YDYDN_260410"],
    )
    drill = get_drill_down(
        engine,
        parent_id=1,
        parent_level="CITY",
        indicator_codes=["sgs_YDYDN_260410"],
    )

    assert [item["code"] for item in overview["indicators"]] == [
        "sgs_YDYDN_260410"
    ]
    overview_row = next(row for row in overview["rows"] if row["area_code"] == "AQ")
    drill_row = next(row for row in drill["rows"] if row["area_code"] == "AQ")
    assert overview_row["metrics"] == {"sgs_YDYDN_260410": 9}
    assert drill_row["metrics"] == {"sgs_YDYDN_260410": 9}
    engine.dispose()


def test_overview_can_skip_accumulated_query_payload():
    engine = create_test_engine()

    result = get_dashboard_overview(
        engine,
        branch_code="AQ",
        change_windows=[5],
        include_acc=False,
    )

    assert result["acc_rows"] == []
    assert result["acc_row_count"] == 0
    engine.dispose()


def test_dashboard_data_version_changes_with_target_update():
    engine = create_test_engine()
    before = get_latest_dashboard_run(engine)
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                UPDATE metric_target
                SET target_value = 120,
                    updated_at = '2099-06-25 12:00:00'
                WHERE id = 1
                """
            )
        )
    after = get_latest_dashboard_run(engine)

    assert before["latest_run"]["batch_no"] == after["latest_run"]["batch_no"]
    assert before["data_version"] != after["data_version"]
    engine.dispose()


def test_matrix_page_sorts_and_paginates_by_done_value():
    engine = create_test_engine()
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO area
                    (id, area_code, area_name, level_type, level_no, parent_id, enabled)
                VALUES
                    (3, 'ZY', '中原区', 'BRANCH', 2, 1, 1),
                    (4, 'JS', '金水区', 'BRANCH', 2, 1, 1)
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO metric_current
                    (id, area_id, indicator_id, collection_run_id,
                     metric_value, stat_date, collected_at, updated_at)
                VALUES
                    (2, 3, 1, 1, 50, '2026-06-11',
                     '2026-06-11 10:05:08', '2026-06-11 10:05:08'),
                    (3, 4, 1, 1, 10, '2026-06-11',
                     '2026-06-11 10:05:08', '2026-06-11 10:05:08')
                """
            )
        )

    first = get_dashboard_matrix_page(
        engine,
        level_type="BRANCH",
        indicator_codes=["sgs_ajvwdz"],
        sort_indicator="sgs_ajvwdz",
        sort_mode="doneDesc",
        page=1,
        page_size=2,
    )
    second = get_dashboard_matrix_page(
        engine,
        level_type="BRANCH",
        indicator_codes=["sgs_ajvwdz"],
        sort_indicator="sgs_ajvwdz",
        sort_mode="doneDesc",
        page=2,
        page_size=2,
    )

    assert first["total"] == 3
    assert first["total_pages"] == 2
    assert [row["area_code"] for row in first["rows"]] == ["ZY", "AQ"]
    assert [row["area_code"] for row in second["rows"]] == ["JS"]
    engine.dispose()


def test_matrix_page_sorts_by_custom_indicator_value():
    engine = create_test_engine()
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO area
                    (id, area_code, area_name, level_type, level_no, parent_id, enabled)
                VALUES
                    (3, 'ZY', '中原区', 'BRANCH', 2, 1, 1)
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO indicator
                    (id, code, name, enabled, source_active, sort_order)
                VALUES
                    (2, 'cloud_pc', '移动云电脑', 1, 1, 20),
                    (3, 'custom_total', '自建总量', 1, 0, 30)
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO metric_current
                    (id, area_id, indicator_id, collection_run_id,
                     metric_value, stat_date, collected_at)
                VALUES
                    (2, 2, 2, 1, 2, '2026-06-11', '2026-06-11 10:05:08'),
                    (3, 3, 1, 1, 10, '2026-06-11', '2026-06-11 10:05:08'),
                    (4, 3, 2, 1, 20, '2026-06-11', '2026-06-11 10:05:08')
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO custom_indicator_component
                    (id, custom_indicator_id, source_indicator_id, coefficient)
                VALUES
                    (1, 3, 1, 1),
                    (2, 3, 2, 2)
                """
            )
        )

    result = get_dashboard_matrix_page(
        engine,
        level_type="BRANCH",
        indicator_codes=["custom_total"],
        sort_indicator="custom_total",
        sort_mode="doneDesc",
        page=1,
        page_size=2,
    )

    assert [row["area_code"] for row in result["rows"]] == ["ZY", "AQ"]
    assert result["rows"][0]["metrics"] == {"custom_total": 50}
    assert result["rows"][1]["metrics"] == {"custom_total": 29}
    engine.dispose()


def test_branch_drill_down_keeps_all_branches_visible():
    engine = create_test_engine()
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO area
                    (id, area_code, area_name, level_type, level_no, parent_id, enabled)
                VALUES
                    (3, 'ZY', '中原区', 'BRANCH', 2, 1, 1)
                """
            )
        )

    result = get_drill_down(engine, parent_id=2, parent_level="BRANCH")

    branch_rows = [row for row in result["rows"] if row["level_type"] == "BRANCH"]
    assert [row["area_code"] for row in branch_rows] == ["AQ", "ZY"]
    engine.dispose()


def test_grid_drill_down_keeps_branches_and_sibling_grids_visible():
    engine = create_test_engine()
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO area
                    (id, area_code, area_name, level_type, level_no, parent_id, enabled)
                VALUES
                    (3, 'ZY', '中原区', 'BRANCH', 2, 1, 1),
                    (4, 'GRID-AQ-1', '金水网格', 'GRID', 3, 2, 1),
                    (5, 'GRID-ZY-1', '须水网格', 'GRID', 3, 3, 1),
                    (8, 'GRID-ZY-2', '沟赵网格', 'GRID', 3, 3, 1),
                    (6, 'CH-ZY-1', '须水渠道', 'CHANNEL', 4, 5, 1),
                    (7, 'CH-AQ-1', '金水渠道', 'CHANNEL', 4, 4, 1)
                """
            )
        )

    result = get_drill_down(engine, parent_id=5, parent_level="GRID")

    branch_rows = [row for row in result["rows"] if row["level_type"] == "BRANCH"]
    grid_rows = [row for row in result["rows"] if row["level_type"] == "GRID"]
    channel_rows = [row for row in result["rows"] if row["level_type"] == "CHANNEL"]
    assert [row["area_code"] for row in branch_rows] == ["AQ", "ZY"]
    assert [row["area_code"] for row in grid_rows] == ["GRID-ZY-1", "GRID-ZY-2"]
    assert [row["area_code"] for row in channel_rows] == ["CH-ZY-1"]
    engine.dispose()


def test_current_with_changes_handles_no_snapshots():
    # Fresh engine without snapshots should still return changes (all null)
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    with engine.begin() as conn:
        for tbl in ("area", "indicator", "collection_run", "metric_current",
                     "metric_snapshot"):
            if tbl == "metric_snapshot":
                conn.execute(
                    text(
                        f"""
                        CREATE TABLE {tbl} (
                            id INTEGER PRIMARY KEY,
                            collection_run_id INTEGER NOT NULL,
                            area_id INTEGER NOT NULL,
                            indicator_id INTEGER NOT NULL,
                            metric_value NUMERIC(20,4) NOT NULL,
                            collected_at DATETIME NOT NULL
                        )
                        """
                    )
                )
            elif tbl == "metric_current":
                conn.execute(
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
            elif tbl == "collection_run":
                conn.execute(
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
                            structure_change_summary TEXT,
                            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                        )
                        """
                    )
                )
            elif tbl == "indicator":
                conn.execute(
                    text(
                        """
                        CREATE TABLE indicator (
                            id INTEGER PRIMARY KEY,
                            code VARCHAR(100) NOT NULL,
                            name VARCHAR(200) NOT NULL,
                            enabled BOOLEAN NOT NULL,
                            source_active BOOLEAN NOT NULL DEFAULT 1,
                            indicator_type VARCHAR(16) NOT NULL DEFAULT 'SOURCE',
                            storage_mode VARCHAR(16) NOT NULL DEFAULT 'STORE',
                            removed_at DATETIME,
                            sort_order INTEGER NOT NULL DEFAULT 0,
                            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                        )
                        """
                    )
                )
            else:
                conn.execute(
                    text(
                        f"""
                        CREATE TABLE {tbl} (
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
        conn.execute(
            text(
                """
                INSERT INTO area VALUES
                    (1, 'A', '郑州市', 'CITY', 1, NULL, 1, 0, NULL, 0,
                     '2026-06-11', '2026-06-11')
                """
            )
        )
        conn.execute(
            text(
                """
                    INSERT INTO indicator
                        (id, code, name, enabled, source_active, sort_order,
                         created_at, updated_at)
                    VALUES
                        (1, 'test_code', '测试指标', 1, 1, 10,
                         '2026-06-11', '2026-06-11')
                """
            )
        )

    result = get_current_with_changes(engine)
    assert result["row_count"] == 1
    assert result["rows"][0]["changes"]["test_code"]["change_60min"] == {
        "value": None,
        "rate": None,
    }
    engine.dispose()


def test_acc_wide_table_returns_latest_data_through_yesterday(monkeypatch):
    engine = create_test_engine()
    monkeypatch.setattr(
        dashboard_query_service,
        "_yesterday_shanghai",
        lambda: date(2026, 6, 11),
    )
    with engine.begin() as connection:
        connection.execute(text("""
            INSERT INTO metric_acc
                (id, period_type, stat_date, area_id, indicator_id,
                 collection_run_id, metric_value, collected_at)
            VALUES
                (9, 'DAY_ACC', '2026-06-12', 2, 1, 2,
                 999, '2026-06-12 08:00:00')
        """))

    result = get_acc_wide_table(engine, period_type="DAY_ACC")

    assert result["indicators"][0]["code"] == "sgs_ajvwdz"
    assert result["row_count"] == 1
    assert result["rows"][0]["area_code"] == "AQ"
    assert result["rows"][0]["metrics"]["sgs_ajvwdz"] == 80
    assert result["rows"][0]["targets"]["sgs_ajvwdz"] == 100
    assert result["through_date"] == "2026-06-11"
    assert result["stat_date"] == "2026-06-10"
    assert result["is_fallback"] is True
    engine.dispose()


def test_acc_wide_table_computes_custom_indicator_sum():
    engine = create_test_engine()
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO indicator
                    (id, code, name, enabled, source_active, sort_order)
                VALUES
                    (2, 'cloud_pc', '移动云电脑', 1, 1, 20),
                    (3, 'custom_total', '自建总量', 1, 0, 30)
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO metric_acc
                    (id, period_type, stat_date, area_id, indicator_id,
                     collection_run_id, metric_value, collected_at)
                VALUES
                    (2, 'DAY_ACC', '2026-06-10', 2, 2, 2,
                     11, '2026-06-11 11:05:08')
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO custom_indicator_component
                    (id, custom_indicator_id, source_indicator_id, coefficient)
                VALUES
                    (1, 3, 1, 1),
                    (2, 3, 2, 2)
                """
            )
        )

    result = get_acc_wide_table(engine, indicator_codes=["custom_total"])

    assert result["row_count"] == 1
    assert result["rows"][0]["metrics"] == {"custom_total": 102}
    engine.dispose()


def test_acc_wide_table_respects_stat_date():
    engine = create_test_engine()

    result = get_acc_wide_table(engine, stat_date="2026-06-10")

    assert result["row_count"] == 1
    assert result["rows"][0]["metrics"]["sgs_ajvwdz"] == 80
    assert result["through_date"] == "2026-06-10"
    assert result["stat_date"] == "2026-06-10"
    assert result["is_fallback"] is False
    engine.dispose()


def test_acc_wide_table_bad_period_raises():
    engine = create_test_engine()

    try:
        get_acc_wide_table(engine, period_type="INVALID")
        assert False, "should have raised"
    except ValueError as exc:
        assert "period_type" in str(exc)
    finally:
        engine.dispose()
