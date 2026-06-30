from __future__ import annotations

from datetime import datetime

from sqlalchemy import create_engine, text

from services.dashboard_v2_query_service import (
    get_acc_wide_table,
    get_dashboard_matrix_page,
    get_dashboard_overview,
    get_drill_down,
    get_historical_with_changes,
)
from services.dashboard_v2_custom_indicator_service import (
    delete_custom_indicator,
    list_custom_indicators,
    upsert_custom_indicator,
)


def _engine():
    engine = create_engine("sqlite://")
    ddl = [
        """
        CREATE TABLE hierarchy_node (
            id INTEGER PRIMARY KEY, node_type TEXT NOT NULL,
            node_code TEXT NOT NULL, node_name TEXT NOT NULL,
            parent_id INTEGER, level_no INTEGER NOT NULL,
            request_enabled BOOLEAN NOT NULL DEFAULT 0,
            metric_enabled BOOLEAN NOT NULL DEFAULT 1,
            enabled BOOLEAN NOT NULL DEFAULT 1,
            sort_order INTEGER NOT NULL DEFAULT 0,
            last_seen_at DATETIME, missing_count INTEGER NOT NULL DEFAULT 0,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """,
        """
        CREATE TABLE hierarchy_parent_history (
            id INTEGER PRIMARY KEY, child_node_id INTEGER NOT NULL,
            parent_node_id INTEGER NOT NULL, valid_from DATETIME NOT NULL,
            valid_to DATETIME, collection_run_id INTEGER, change_type TEXT NOT NULL,
            active_child_node_id INTEGER, created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """,
        """
        CREATE TABLE indicator (
            id INTEGER PRIMARY KEY, code TEXT NOT NULL, name TEXT NOT NULL,
            indicator_type TEXT NOT NULL, storage_mode TEXT NOT NULL,
            enabled BOOLEAN NOT NULL DEFAULT 1,
            source_active BOOLEAN NOT NULL DEFAULT 1,
            sort_order INTEGER NOT NULL DEFAULT 0, removed_at DATETIME,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """,
        """
        CREATE TABLE indicator_formula_component (
            id INTEGER PRIMARY KEY, custom_indicator_id INTEGER NOT NULL,
            source_indicator_id INTEGER NOT NULL, coefficient NUMERIC NOT NULL,
            sort_order INTEGER NOT NULL DEFAULT 0,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """,
        """
        CREATE TABLE collection_run (
            id INTEGER PRIMARY KEY, batch_no TEXT NOT NULL, run_type TEXT NOT NULL,
            trigger_type TEXT NOT NULL, prefect_flow_run_id TEXT, status TEXT NOT NULL,
            phase TEXT NOT NULL, session_status TEXT, stat_date DATE,
            started_at DATETIME NOT NULL, finished_at DATETIME,
            request_count INTEGER DEFAULT 0, node_count INTEGER DEFAULT 0,
            row_count INTEGER DEFAULT 0, current_upsert_count INTEGER DEFAULT 0,
            snapshot_insert_count INTEGER DEFAULT 0, acc_upsert_count INTEGER DEFAULT 0,
            structure_change_summary JSON, error_type TEXT, error_message JSON,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """,
        """
        CREATE TABLE metric_current (
            id INTEGER PRIMARY KEY, node_id INTEGER NOT NULL,
            indicator_id INTEGER NOT NULL, collection_run_id INTEGER,
            metric_value NUMERIC NOT NULL, stat_date DATE NOT NULL,
            collected_at DATETIME NOT NULL, updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """,
        """
        CREATE TABLE metric_snapshot (
            id INTEGER NOT NULL, collected_at DATETIME NOT NULL,
            collection_run_id INTEGER NOT NULL, node_id INTEGER NOT NULL,
            indicator_id INTEGER NOT NULL, metric_value NUMERIC NOT NULL,
            PRIMARY KEY (id, collected_at)
        )
        """,
        """
        CREATE TABLE metric_acc (
            id INTEGER PRIMARY KEY, period_type TEXT NOT NULL, stat_date DATE NOT NULL,
            node_id INTEGER NOT NULL, indicator_id INTEGER NOT NULL,
            collection_run_id INTEGER, metric_value NUMERIC NOT NULL,
            collected_at DATETIME NOT NULL, updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """,
        """
        CREATE TABLE target_plan (
            id INTEGER PRIMARY KEY, plan_name TEXT NOT NULL, scenario TEXT NOT NULL,
            period_type TEXT NOT NULL, effective_from DATE NOT NULL,
            effective_to DATE, priority INTEGER NOT NULL DEFAULT 0,
            version_no INTEGER NOT NULL DEFAULT 1, status TEXT NOT NULL,
            supersedes_plan_id INTEGER, activated_at DATETIME, retired_at DATETIME,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """,
        """
        CREATE TABLE metric_target_value (
            id INTEGER PRIMARY KEY, plan_id INTEGER NOT NULL,
            node_id INTEGER NOT NULL, indicator_id INTEGER NOT NULL,
            target_value NUMERIC NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """,
    ]
    with engine.begin() as connection:
        for statement in ddl:
            connection.execute(text(statement))
        connection.execute(text("""
            INSERT INTO hierarchy_node
                (id, node_type, node_code, node_name, parent_id, level_no, sort_order)
            VALUES
                (1, 'CITY', 'A', '郑州市', NULL, 1, 1),
                (2, 'BRANCH', 'AQ', '中原区', 1, 2, 1),
                (3, 'GRID', 'AQ701', '须水网格', 2, 3, 1),
                (4, 'CHANNEL_MANAGER', 'M001&AQ701', '张经理', 3, 4, 1),
                (5, 'CHANNEL', 'C001', '测试渠道', 4, 5, 1)
        """))
        connection.execute(text("""
            INSERT INTO hierarchy_parent_history
                (id, child_node_id, parent_node_id, valid_from, change_type)
            VALUES
                (1, 2, 1, '2026-01-01 00:00:00', 'CREATED'),
                (2, 3, 2, '2026-01-01 00:00:00', 'CREATED'),
                (3, 4, 3, '2026-01-01 00:00:00', 'CREATED'),
                (4, 5, 4, '2026-01-01 00:00:00', 'CREATED')
        """))
        connection.execute(text("""
            INSERT INTO indicator
                (id, code, name, indicator_type, storage_mode, sort_order)
            VALUES (1, 'channel_count', '渠道数量', 'SOURCE', 'STORE', 1)
        """))
        connection.execute(text("""
            INSERT INTO collection_run
                (id, batch_no, run_type, trigger_type, status, phase,
                 stat_date, started_at, finished_at)
            VALUES
                (1, 'v2-1000', 'REALTIME', 'SCHEDULED', 'SUCCESS', 'COMPLETED',
                 '2026-06-30', '2026-06-30 10:00:00', '2026-06-30 10:00:01'),
                (2, 'v2-1010', 'REALTIME', 'SCHEDULED', 'SUCCESS', 'COMPLETED',
                 '2026-06-30', '2026-06-30 10:10:00', '2026-06-30 10:10:01')
        """))
        connection.execute(text("""
            INSERT INTO metric_current
                (id, node_id, indicator_id, collection_run_id, metric_value,
                 stat_date, collected_at)
            VALUES
                (1, 2, 1, 2, 100, '2026-06-30', '2026-06-30 10:10:00'),
                (2, 3, 1, 2, 50, '2026-06-30', '2026-06-30 10:10:00'),
                (3, 4, 1, 2, 20, '2026-06-30', '2026-06-30 10:10:00'),
                (4, 5, 1, 2, 12, '2026-06-30', '2026-06-30 10:10:00')
        """))
        connection.execute(text("""
            INSERT INTO metric_snapshot
                (id, collected_at, collection_run_id, node_id, indicator_id, metric_value)
            VALUES
                (1, '2026-06-30 10:00:00', 1, 2, 1, 90),
                (2, '2026-06-30 10:00:00', 1, 3, 1, 45),
                (3, '2026-06-30 10:00:00', 1, 4, 1, 20),
                (4, '2026-06-30 10:00:00', 1, 5, 1, 10),
                (5, '2026-06-30 10:10:00', 2, 5, 1, 12)
        """))
        connection.execute(text("""
            INSERT INTO target_plan
                (id, plan_name, scenario, period_type, effective_from, status)
            VALUES (1, '日目标', 'NORMAL', 'DAY', '2026-01-01', 'ACTIVE')
        """))
        connection.execute(text("""
            INSERT INTO metric_target_value
                (id, plan_id, node_id, indicator_id, target_value)
            VALUES (1, 1, 4, 1, 30), (2, 1, 5, 1, 15)
        """))
    return engine


def test_overview_returns_v2_tree_with_manager_and_without_legacy_fields():
    result = get_dashboard_overview(
        _engine(), branch_code="AQ", indicator_codes=["channel_count"], include_acc=False
    )

    manager = next(row for row in result["rows"] if row["node_type"] == "CHANNEL_MANAGER")
    channel = next(row for row in result["rows"] if row["node_type"] == "CHANNEL")
    assert manager["id"] == 4
    assert channel["parent_id"] == manager["id"]
    assert manager["metrics"]["channel_count"] == 20
    assert manager["targets"]["channel_count"] == 30
    assert not ({"area_id", "area_code", "area_name", "level_type"} & manager.keys())


def test_drill_down_follows_grid_manager_channel_levels():
    engine = _engine()

    managers = get_drill_down(
        engine,
        parent_id=3,
        parent_node_type="GRID",
        indicator_codes=["channel_count"],
        include_acc=False,
    )
    channels = get_drill_down(
        engine,
        parent_id=4,
        parent_node_type="CHANNEL_MANAGER",
        indicator_codes=["channel_count"],
        include_acc=False,
    )

    assert [row["node_type"] for row in managers["rows"]] == ["CHANNEL_MANAGER"]
    assert [row["node_type"] for row in channels["rows"]] == ["CHANNEL"]


def test_grid_drill_down_can_flatten_channels_for_legacy_interaction():
    result = get_drill_down(
        _engine(),
        parent_id=3,
        parent_node_type="GRID",
        tree_mode="flat",
        indicator_codes=["channel_count"],
        include_acc=False,
    )

    assert result["tree_mode"] == "flat"
    assert [row["node_type"] for row in result["rows"]] == ["CHANNEL"]
    assert result["rows"][0]["node_code"] == "C001"


def test_sparse_history_carries_channel_value_forward_without_using_current():
    result = get_historical_with_changes(
        _engine(),
        as_of=datetime(2026, 6, 30, 10, 6),
        node_type="CHANNEL",
        scope_mode="default",
        branch_code="AQ",
        indicator_codes=["channel_count"],
        change_windows=[5],
    )

    assert result["rows"][0]["metrics"]["channel_count"] == 10
    assert result["rows"][0]["collected_at"] == "2026-06-30T10:00:00.000"


def test_default_matrix_scope_keeps_manager_under_selected_branch():
    result = get_dashboard_matrix_page(
        _engine(),
        node_type="CHANNEL_MANAGER",
        scope_mode="default",
        branch_code="AQ",
        indicator_codes=["channel_count"],
    )

    assert result["total"] == 1
    assert result["rows"][0]["node_code"] == "M001&AQ701"


def test_v2_custom_indicator_can_be_saved_and_is_archived_instead_of_deleted():
    engine = _engine()

    saved = upsert_custom_indicator(
        engine,
        code="double_count",
        name="双倍渠道数",
        components=[{"source_code": "channel_count", "coefficient": 2}],
    )
    archived = delete_custom_indicator(engine, "double_count")
    listed = list_custom_indicators(engine)

    assert saved["components"][0]["coefficient"] == 2
    assert archived == {
        "code": "double_count",
        "deleted": True,
        "archived": True,
    }
    assert listed["indicators"][0]["enabled"] is False


def test_acc_uses_latest_stat_date_and_attaches_versioned_targets():
    engine = _engine()
    with engine.begin() as connection:
        connection.execute(text("""
            INSERT INTO metric_acc
                (id, period_type, stat_date, node_id, indicator_id,
                 collection_run_id, metric_value, collected_at)
            VALUES
                (1, 'DAY_ACC', '2026-06-29', 4, 1, 2, 18, '2026-06-30 08:00:00')
        """))

    result = get_acc_wide_table(
        engine,
        period_type="DAY_ACC",
        node_type="CHANNEL_MANAGER",
        indicator_codes=["channel_count"],
    )

    assert result["rows"][0]["metrics"]["channel_count"] == 18
    assert result["rows"][0]["targets"]["channel_count"] == 30
