from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool

from services.dashboard_query_service import (
    get_acc_wide_table,
    get_current_wide_table,
    get_current_with_changes,
    get_drill_down,
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


def test_current_with_changes_supports_custom_windows():
    engine = create_test_engine()

    result = get_current_with_changes(engine, change_windows=[10, 60])

    row_with_data = [r for r in result["rows"] if r["area_code"] == "AQ"][0]
    changes = row_with_data["changes"]["sgs_ajvwdz"]
    assert set(changes) == {"change_10min", "change_60min"}
    assert changes["change_10min"]["value"] == 2.0
    assert changes["change_60min"]["value"] == 5.0
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


def test_current_with_changes_ignores_stale_cutoff_snapshot():
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
        "value": None,
        "rate": None,
    }
    engine.dispose()


def test_drill_down_returns_latest_run_metadata():
    engine = create_test_engine()

    result = get_drill_down(engine, parent_id=1, parent_level="CITY")

    assert result["latest_run"]["batch_no"] == "batch-1"
    assert result["latest_run"]["finished_at"].startswith("2026-06-11T10:05:08")
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
                INSERT INTO indicator VALUES
                    (1, 'test_code', '测试指标', 1, 10,
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


def test_acc_wide_table_returns_day_acc_data():
    engine = create_test_engine()

    result = get_acc_wide_table(engine, period_type="DAY_ACC")

    assert result["indicators"][0]["code"] == "sgs_ajvwdz"
    assert result["row_count"] == 1
    assert result["rows"][0]["area_code"] == "AQ"
    assert result["rows"][0]["metrics"]["sgs_ajvwdz"] == 80
    assert result["rows"][0]["targets"]["sgs_ajvwdz"] == 100
    engine.dispose()


def test_acc_wide_table_respects_stat_date():
    engine = create_test_engine()

    result = get_acc_wide_table(engine, stat_date="2026-06-10")

    assert result["row_count"] == 1
    assert result["rows"][0]["metrics"]["sgs_ajvwdz"] == 80
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
