from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import create_engine, text

from services import dashboard_v2_query_service

from services.dashboard_v2_query_service import (
    get_acc_wide_table,
    get_current_with_changes,
    get_dashboard_matrix_page,
    get_dashboard_overview,
    get_drill_down,
    get_historical_with_changes,
    get_history_options,
    get_history_range,
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


def test_change_window_matches_v1_finished_anchor_and_nearby_snapshot():
    engine = _engine()
    with engine.begin() as connection:
        connection.execute(text("""
            UPDATE collection_run
            SET started_at = '2026-06-30 10:05:00',
                finished_at = '2026-06-30 10:09:00'
            WHERE id = 1
        """))
        connection.execute(text("""
            UPDATE collection_run
            SET finished_at = '2026-06-30 10:14:00'
            WHERE id = 2
        """))
        connection.execute(text("""
            INSERT INTO metric_snapshot
                (id, collected_at, collection_run_id, node_id, indicator_id, metric_value)
            VALUES (6, '2026-06-30 10:09:00', 1, 2, 1, 95)
        """))

    result = get_current_with_changes(
        engine,
        node_type="BRANCH",
        indicator_codes=["channel_count"],
        change_windows=[5],
    )

    change = result["rows"][0]["changes"]["channel_count"]["change_5min"]
    assert change == {"value": 5, "rate": 5 / 95}


def test_history_availability_matches_frontend_minute_contract():
    engine = _engine()

    assert get_history_range(engine) == {
        "earliest_at": "2026-06-30T10:00:00.000",
        "latest_at": "2026-06-30T10:10:00.000",
    }
    assert get_history_options(engine, indicator_codes=["channel_count"]) == {
        "dates": [{"date": "2026-06-30", "times": ["10:10", "10:00"]}],
        "date_count": 1,
    }
    assert get_history_options(engine, indicator_codes=["missing"]) == {
        "dates": [],
        "date_count": 0,
    }


def test_overview_keeps_all_branches_and_scopes_lower_levels_to_selected_branch():
    engine = _engine()
    with engine.begin() as connection:
        connection.execute(text("""
            INSERT INTO hierarchy_node
                (id, node_type, node_code, node_name, parent_id, level_no, sort_order)
            VALUES
                (6, 'BRANCH', 'ZY', '郑东新区', 1, 2, 2),
                (7, 'GRID', 'ZY701', '郑东网格', 6, 3, 1)
        """))
        connection.execute(text("""
            INSERT INTO metric_current
                (id, node_id, indicator_id, collection_run_id, metric_value,
                 stat_date, collected_at)
            VALUES
                (6, 6, 1, 2, 80, '2026-06-30', '2026-06-30 10:10:00'),
                (7, 7, 1, 2, 40, '2026-06-30', '2026-06-30 10:10:00')
        """))

    result = get_dashboard_overview(
        engine,
        branch_code="AQ",
        indicator_codes=["channel_count"],
        include_acc=False,
    )

    assert [
        row["node_code"]
        for row in result["rows"]
        if row["node_type"] == "BRANCH"
    ] == ["AQ", "ZY"]
    assert [
        row["node_code"]
        for row in result["rows"]
        if row["node_type"] == "GRID"
    ] == ["AQ701"]


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

    assert [row["node_type"] for row in managers["rows"]] == [
        "BRANCH", "GRID", "CHANNEL_MANAGER", "CHANNEL",
    ]
    assert [row["node_type"] for row in channels["rows"]] == [
        "BRANCH", "GRID", "CHANNEL_MANAGER", "CHANNEL",
    ]


def test_full_drill_down_keeps_v1_ancestor_comparison_context():
    engine = _engine()
    with engine.begin() as connection:
        connection.execute(text("""
            INSERT INTO hierarchy_node
                (id, node_type, node_code, node_name, parent_id, level_no, sort_order)
            VALUES
                (6, 'BRANCH', 'ZY', '郑东新区', 1, 2, 2),
                (7, 'GRID', 'AQ702', '航海网格', 2, 3, 2),
                (8, 'CHANNEL_MANAGER', 'M002&AQ701', '李经理', 3, 4, 2),
                (9, 'CHANNEL', 'C002', '其他经理渠道', 8, 5, 2),
                (10, 'CITY', 'B', '开封市', NULL, 1, 2),
                (11, 'BRANCH', 'BQ', '开封分公司', 10, 2, 1)
        """))

    branch = get_drill_down(
        engine,
        parent_id=2,
        parent_node_type="BRANCH",
        indicator_codes=["channel_count"],
        include_acc=False,
    )
    grid = get_drill_down(
        engine,
        parent_id=3,
        parent_node_type="GRID",
        indicator_codes=["channel_count"],
        include_acc=False,
    )
    manager = get_drill_down(
        engine,
        parent_id=4,
        parent_node_type="CHANNEL_MANAGER",
        indicator_codes=["channel_count"],
        include_acc=False,
    )

    assert [row["node_code"] for row in branch["rows"]] == [
        "AQ", "ZY", "AQ701", "AQ702", "M001&AQ701", "M002&AQ701", "C001", "C002",
    ]
    assert [row["node_code"] for row in grid["rows"]] == [
        "AQ", "ZY", "AQ701", "AQ702", "M001&AQ701", "M002&AQ701", "C001", "C002",
    ]
    assert [row["node_code"] for row in manager["rows"]] == [
        "AQ", "ZY", "AQ701", "AQ702", "M001&AQ701", "M002&AQ701", "C001",
    ]


def test_historical_drill_down_matches_realtime_descendant_scope():
    engine = _engine()
    with engine.begin() as connection:
        connection.execute(text("""
            INSERT INTO hierarchy_node
                (id, node_type, node_code, node_name, parent_id, level_no, sort_order)
            VALUES
                (6, 'BRANCH', 'ZY', '郑东新区', 1, 2, 2),
                (7, 'GRID', 'AQ702', '航海网格', 2, 3, 2),
                (8, 'CHANNEL_MANAGER', 'M002&AQ701', '李经理', 3, 4, 2),
                (9, 'CHANNEL', 'C002', '其他经理渠道', 8, 5, 2),
                (10, 'CITY', 'B', '开封市', NULL, 1, 2),
                (11, 'BRANCH', 'BQ', '开封分公司', 10, 2, 1)
        """))

    common = {
        "engine": engine,
        "as_of": datetime(2026, 6, 30, 10, 5),
        "indicator_codes": ["channel_count"],
    }
    branch = get_historical_with_changes(
        **common, parent_id=2, parent_node_type="BRANCH",
    )
    grid = get_historical_with_changes(
        **common, parent_id=3, parent_node_type="GRID",
    )
    manager = get_historical_with_changes(
        **common, parent_id=4, parent_node_type="CHANNEL_MANAGER",
    )

    assert [row["node_code"] for row in branch["rows"]] == [
        "AQ", "ZY", "AQ701", "AQ702", "M001&AQ701", "M002&AQ701", "C001", "C002",
    ]
    assert [row["node_code"] for row in grid["rows"]] == [
        "AQ", "ZY", "AQ701", "AQ702", "M001&AQ701", "M002&AQ701", "C001", "C002",
    ]
    assert [row["node_code"] for row in manager["rows"]] == [
        "AQ", "ZY", "AQ701", "AQ702", "M001&AQ701", "M002&AQ701", "C001",
    ]


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


def test_historical_time_uses_v1_completed_batch_and_started_anchor():
    engine = _engine()
    with engine.begin() as connection:
        connection.execute(text("""
            INSERT INTO metric_snapshot
                (id, collected_at, collection_run_id, node_id, indicator_id, metric_value)
            VALUES
                (6, '2026-06-30 10:05:00', 1, 2, 1, 95),
                (7, '2026-06-30 10:10:00', 2, 2, 1, 100),
                (8, '2026-06-30 10:12:00', 2, 2, 1, 110)
        """))

    result = get_historical_with_changes(
        engine,
        as_of=datetime(2026, 6, 30, 10, 14, 30),
        node_type="BRANCH",
        indicator_codes=["channel_count"],
        change_windows=[5],
    )

    row = result["rows"][0]
    assert row["metrics"]["channel_count"] == 100
    assert row["changes"]["channel_count"]["change_5min"] == {
        "value": 5,
        "rate": 5 / 95,
    }
    assert result["history_meta"]["fallback_seconds"] == 240


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


def test_acc_uses_latest_stat_date_through_yesterday_and_attaches_targets(monkeypatch):
    engine = _engine()
    monkeypatch.setattr(
        dashboard_v2_query_service,
        "_yesterday_shanghai",
        lambda: date(2026, 6, 30),
    )
    with engine.begin() as connection:
        connection.execute(text("""
            INSERT INTO metric_acc
                (id, period_type, stat_date, node_id, indicator_id,
                 collection_run_id, metric_value, collected_at)
            VALUES
                (1, 'DAY_ACC', '2026-06-29', 4, 1, 2, 18, '2026-06-30 08:00:00'),
                (2, 'DAY_ACC', '2026-07-01', 4, 1, 2, 99, '2026-07-01 08:00:00')
        """))

    result = get_acc_wide_table(
        engine,
        period_type="DAY_ACC",
        node_type="CHANNEL_MANAGER",
        indicator_codes=["channel_count"],
    )

    assert result["rows"][0]["metrics"]["channel_count"] == 18
    assert result["rows"][0]["targets"]["channel_count"] == 30
    assert result["through_date"] == "2026-06-30"
    assert result["stat_date"] == "2026-06-29"
    assert result["is_fallback"] is True


def test_acc_returns_empty_rows_when_no_data_exists_before_yesterday(monkeypatch):
    engine = _engine()
    monkeypatch.setattr(
        dashboard_v2_query_service,
        "_yesterday_shanghai",
        lambda: date(2025, 12, 31),
    )

    result = get_acc_wide_table(engine, period_type="DAY_ACC")

    assert result["rows"] == []
    assert result["row_count"] == 0
    assert result["stat_date"] is None
    assert result["is_fallback"] is False
