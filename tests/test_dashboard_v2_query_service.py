from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import create_engine, text

from services import dashboard_v2_query_service

from services.dashboard_v2_query_service import (
    get_acc_options,
    get_acc_wide_table,
    get_current_with_changes,
    get_dashboard_matrix_page,
    get_dashboard_overview,
    get_drill_down,
    get_historical_with_changes,
    get_history_options,
    get_history_range,
    get_latest_dashboard_run,
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
            is_realtime BOOLEAN NOT NULL DEFAULT 0,
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
            VALUES (1, '日目标', 'NORMAL', 'DAY', '2026-06-01', 'ACTIVE')
        """))
        connection.execute(text("""
            INSERT INTO metric_target_value
                (id, plan_id, node_id, indicator_id, target_value)
            VALUES (1, 1, 4, 1, 30), (2, 1, 5, 1, 15)
        """))
    return engine


def test_overview_returns_v2_tree_with_manager_and_without_legacy_fields():
    engine = _engine()
    _insert_working_day_target(engine)
    result = get_dashboard_overview(
        engine, branch_code="AQ", indicator_codes=["channel_count"], include_acc=False
    )

    manager = next(row for row in result["rows"] if row["node_type"] == "CHANNEL_MANAGER")
    channel = next(row for row in result["rows"] if row["node_type"] == "CHANNEL")
    assert manager["id"] == 4
    assert channel["parent_id"] == manager["id"]
    assert manager["metrics"]["channel_count"] == 20
    assert manager["targets"]["channel_count"] == 30
    assert not ({"area_id", "area_code", "area_name", "level_type"} & manager.keys())


def test_target_scenario_is_explicit_instead_of_pk_fallback():
    engine = _engine()
    _insert_working_day_target(engine)
    with engine.begin() as connection:
        connection.execute(text("""
            INSERT INTO target_plan
                (id, plan_name, scenario, period_type, effective_from, status)
            VALUES (9, 'PK日目标', 'PK', 'DAY', '2026-06-01', 'DRAFT')
        """))
        connection.execute(text("UPDATE target_plan SET is_realtime=1 WHERE id=9"))
        connection.execute(text("""
            INSERT INTO metric_target_value
                (id, plan_id, node_id, indicator_id, target_value)
            VALUES (9, 9, 4, 1, 60)
        """))

    normal = get_dashboard_overview(
        engine,
        branch_code="AQ",
        indicator_codes=["channel_count"],
        include_acc=False,
        target_scenario="NORMAL",
    )
    pk = get_dashboard_overview(
        engine,
        branch_code="AQ",
        indicator_codes=["channel_count"],
        include_acc=False,
        target_scenario="PK",
    )

    normal_manager = next(row for row in normal["rows"] if row["node_type"] == "CHANNEL_MANAGER")
    pk_manager = next(row for row in pk["rows"] if row["node_type"] == "CHANNEL_MANAGER")
    assert normal_manager["targets"]["channel_count"] == 30
    assert pk_manager["targets"]["channel_count"] == 60


def test_historical_target_uses_business_effective_date_not_publish_time():
    engine = _engine()
    _insert_working_day_target(engine)
    with engine.begin() as connection:
        connection.execute(text("""
            UPDATE target_plan
            SET status = 'RETIRED', activated_at = '2026-06-01 00:00:00',
                retired_at = '2026-06-30 11:00:00', effective_to = '2026-06-29'
            WHERE id = 1
        """))
        connection.execute(text("""
            INSERT INTO target_plan
                (id, plan_name, scenario, period_type, effective_from,
                 version_no, status, activated_at)
            VALUES
                (2, '日目标', 'NORMAL', 'DAY', '2026-06-30',
                 2, 'ACTIVE', '2026-06-30 11:00:00')
        """))
        connection.execute(text("""
            INSERT INTO metric_target_value
                (id, plan_id, node_id, indicator_id, target_value)
            VALUES (10, 2, 4, 1, 60)
        """))
        connection.execute(text("UPDATE metric_target_value SET target_value=45 WHERE plan_id=10 AND node_id=4"))

    current = get_dashboard_overview(
        engine,
        branch_code="AQ",
        indicator_codes=["channel_count"],
        include_acc=False,
    )
    historical = get_historical_with_changes(
        engine,
        as_of=datetime(2026, 6, 30, 12, 0, 0),
        indicator_codes=["channel_count"],
    )

    current_manager = next(row for row in current["rows"] if row["id"] == 4)
    historical_manager = next(row for row in historical["rows"] if row["id"] == 4)
    assert current_manager["targets"]["channel_count"] == 45
    assert historical_manager["targets"]["channel_count"] == 60


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


def test_change_window_does_not_use_previous_business_date_baseline():
    engine = _engine()
    with engine.begin() as connection:
        connection.execute(text("""
            UPDATE collection_run
            SET stat_date = '2026-06-30',
                started_at = '2026-06-30 23:55:00',
                finished_at = '2026-06-30 23:55:00'
            WHERE id = 1
        """))
        connection.execute(text("""
            UPDATE collection_run
            SET stat_date = '2026-07-01',
                started_at = '2026-07-01 00:02:00',
                finished_at = '2026-07-01 00:02:00'
            WHERE id = 2
        """))
        connection.execute(text("""
            UPDATE metric_current
            SET stat_date = '2026-07-01', collected_at = '2026-07-01 00:02:00'
            WHERE collection_run_id = 2
        """))
        connection.execute(text("""
            UPDATE metric_snapshot
            SET collected_at = '2026-06-30 23:55:00', metric_value = 150
            WHERE id = 1
        """))

    without_today_baseline = get_current_with_changes(
        engine,
        node_type="BRANCH",
        indicator_codes=["channel_count"],
        change_windows=[5],
    )
    assert without_today_baseline["rows"][0]["changes"]["channel_count"]["change_5min"] == {
        "value": None,
        "rate": None,
    }

    with engine.begin() as connection:
        connection.execute(text("""
            INSERT INTO metric_snapshot
                (id, collected_at, collection_run_id, node_id, indicator_id, metric_value)
            VALUES (6, '2026-07-01 00:00:00', 2, 2, 1, 95)
        """))

    with_today_baseline = get_current_with_changes(
        engine,
        node_type="BRANCH",
        indicator_codes=["channel_count"],
        change_windows=[5],
    )
    assert with_today_baseline["rows"][0]["changes"]["channel_count"]["change_5min"] == {
        "value": 5,
        "rate": 5 / 95,
    }


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
    assert result["history_meta"]["time_basis"] == "BATCH_STARTED_AT"
    assert result["history_meta"]["change_tolerance_minutes"] == 3


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


def test_acc_historical_target_uses_business_effective_date_not_publish_time():
    engine = _engine()
    with engine.begin() as connection:
        connection.execute(text("""
            INSERT INTO metric_acc
                (id, period_type, stat_date, node_id, indicator_id,
                 collection_run_id, metric_value, collected_at)
            VALUES (1, 'DAY_ACC', '2026-06-30', 4, 1, 2, 18, '2026-06-30 08:00:00')
        """))
        connection.execute(text("""
            UPDATE target_plan
            SET status = 'RETIRED', activated_at = '2026-06-01 00:00:00',
                retired_at = '2026-06-30 11:00:00', effective_to = '2026-06-29'
            WHERE id = 1
        """))
        connection.execute(text("""
            INSERT INTO target_plan
                (id, plan_name, scenario, period_type, effective_from,
                 version_no, status, activated_at)
            VALUES
                (2, '日目标', 'NORMAL', 'DAY', '2026-06-30',
                 2, 'ACTIVE', '2026-06-30 11:00:00')
        """))
        connection.execute(text("""
            INSERT INTO metric_target_value
                (id, plan_id, node_id, indicator_id, target_value)
            VALUES (10, 2, 4, 1, 60)
        """))

    result = get_acc_wide_table(
        engine,
        period_type="DAY_ACC",
        stat_date="2026-06-30",
        node_type="CHANNEL_MANAGER",
        indicator_codes=["channel_count"],
    )

    assert result["rows"][0]["targets"]["channel_count"] == 60


def test_day_acc_metrics_can_use_working_or_assessment_month_targets():
    engine = _engine()
    _insert_month_target_and_acc(engine)
    with engine.begin() as connection:
        connection.execute(text("""
            INSERT INTO target_plan
                (id, plan_name, scenario, period_type, effective_from,
                 status, is_realtime)
            VALUES (3, '当前月目标', 'NORMAL', 'MONTH', '2026-06-30', 'DRAFT', 1)
        """))
        connection.execute(text("""
            INSERT INTO metric_target_value
                (id, plan_id, node_id, indicator_id, target_value)
            VALUES (4, 3, 2, 1, 250)
        """))

    working = get_acc_wide_table(
        engine,
        period_type="DAY_ACC",
        stat_date="2026-06-29",
        node_type="BRANCH",
        indicator_codes=["channel_count"],
        target_period="MONTH",
        target_source="WORKING",
    )
    assessment = get_acc_wide_table(
        engine,
        period_type="DAY_ACC",
        stat_date="2026-06-29",
        node_type="BRANCH",
        indicator_codes=["channel_count"],
        target_period="MONTH",
        target_source="ASSESSMENT",
    )

    assert working["rows"][0]["metrics"]["channel_count"] == 80
    assert working["rows"][0]["targets"]["channel_count"] == 250
    assert working["target_period"] == "MONTH"
    assert working["target_source"] == "WORKING"
    assert working["target_date"] == "2026-06-30"
    assert assessment["rows"][0]["targets"]["channel_count"] == 200
    assert assessment["target_period"] == "MONTH"
    assert assessment["target_source"] == "ASSESSMENT"
    assert assessment["target_date"] == "2026-06-29"


def test_realtime_overview_month_acc_uses_working_month_target():
    engine = _engine()
    _insert_month_target_and_acc(engine)
    with engine.begin() as connection:
        connection.execute(text("""
            INSERT INTO target_plan
                (id, plan_name, scenario, period_type, effective_from,
                 status, is_realtime)
            VALUES (3, '当前月目标', 'NORMAL', 'MONTH', '2026-06-30', 'DRAFT', 1)
        """))
        connection.execute(text("""
            INSERT INTO metric_target_value
                (id, plan_id, node_id, indicator_id, target_value)
            VALUES (4, 3, 2, 1, 250)
        """))

    result = get_dashboard_overview(
        engine,
        branch_code="AQ",
        indicator_codes=["channel_count"],
        include_acc=True,
    )

    acc_branch = next(row for row in result["acc_rows"] if row["id"] == 2)
    assert acc_branch["metrics"]["channel_count"] == 80
    assert acc_branch["targets"]["channel_count"] == 250


def test_acc_options_returns_distinct_daily_dates_in_descending_pages():
    engine = _engine()
    with engine.begin() as connection:
        connection.execute(text("""
            INSERT INTO metric_acc
                (id, period_type, stat_date, node_id, indicator_id,
                 collection_run_id, metric_value, collected_at)
            VALUES
                (1, 'DAY_ACC', '2026-06-30', 2, 1, 1, 10, '2026-06-30 08:00:00'),
                (2, 'DAY_ACC', '2026-06-30', 3, 1, 1, 20, '2026-06-30 08:00:00'),
                (3, 'DAY_ACC', '2026-06-29', 2, 1, 1, 10, '2026-06-29 08:00:00'),
                (4, 'DAY_ACC', '2026-06-28', 2, 1, 1, 10, '2026-06-28 08:00:00'),
                (5, 'MONTH', '2026-06-30', 2, 1, 1, 10, '2026-06-30 08:00:00')
        """))

    assert get_acc_options(engine, page=1, page_size=2) == {
        "dates": ["2026-06-30", "2026-06-29"],
        "page": 1,
        "page_size": 2,
        "total": 3,
        "has_more": True,
    }
    assert get_acc_options(engine, page=2, page_size=2) == {
        "dates": ["2026-06-28"],
        "page": 2,
        "page_size": 2,
        "total": 3,
        "has_more": False,
    }


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


def _insert_month_target_and_acc(
    engine, *, acc_date: str = "2026-06-29", acc_value: int = 80,
    include_working: bool = True,
):
    with engine.begin() as connection:
        connection.execute(text("""
            INSERT INTO target_plan
                (id, plan_name, scenario, period_type, effective_from, status)
            VALUES (2, '月目标', 'NORMAL', 'MONTH', '2026-06-01', 'ACTIVE')
        """))
        connection.execute(text("""
            INSERT INTO metric_target_value
                (id, plan_id, node_id, indicator_id, target_value)
            VALUES (3, 2, 2, 1, 200)
        """))
        connection.execute(
            text("""
                INSERT INTO metric_acc
                    (id, period_type, stat_date, node_id, indicator_id,
                     collection_run_id, metric_value, collected_at)
                VALUES
                    (1, 'DAY_ACC', :acc_date, 2, 1, 1, :acc_value,
                     '2026-06-30 08:00:00')
            """),
            {"acc_date": acc_date, "acc_value": acc_value},
        )
        if include_working:
            connection.execute(text("""
                INSERT INTO target_plan
                    (id, plan_name, scenario, period_type, effective_from,
                     status, is_realtime)
                VALUES (20, '实时月目标', 'NORMAL', 'MONTH', '2026-06-01',
                        'DRAFT', 1)
            """))
            connection.execute(text("""
                INSERT INTO metric_target_value
                    (id, plan_id, node_id, indicator_id, target_value)
                VALUES (20, 20, 2, 1, 200)
            """))


def _insert_working_day_target(engine, *, target_value: int = 30):
    with engine.begin() as connection:
        connection.execute(text("""
            INSERT INTO target_plan
                (id, plan_name, scenario, period_type, effective_from,
                 status, is_realtime)
            VALUES (10, '实时日目标', 'NORMAL', 'DAY', '2026-06-01',
                    'DRAFT', 1)
        """))
        connection.execute(text("""
            INSERT INTO metric_target_value
                (id, plan_id, node_id, indicator_id, target_value)
            VALUES (100, 10, 4, 1, :target_value),
                   (101, 10, 5, 1, :target_value)
        """), {"target_value": target_value})


def test_realtime_acc_adds_same_month_baseline_and_uses_month_target():
    engine = _engine()
    _insert_month_target_and_acc(engine)

    result = get_current_with_changes(
        engine,
        node_type="BRANCH",
        indicator_codes=["channel_count"],
        change_windows=[5],
        value_mode="REALTIME_ACC",
    )

    row = result["rows"][0]
    assert result["data_mode"] == "REALTIME_ACC"
    assert row["metrics"]["channel_count"] == 180
    assert row["targets"]["channel_count"] == 200
    assert row["changes"]["channel_count"]["change_5min"] == {
        "value": 10,
        "rate": 10 / 170,
    }
    assert result["accumulation_meta"] == {
        "through_date": "2026-06-29",
        "stat_date": "2026-06-29",
        "is_fallback": False,
        "baseline_zero": False,
        "baseline_missing": False,
        "target_period": "MONTH",
    }


def test_realtime_acc_prefers_selected_working_target():
    engine = _engine()
    _insert_month_target_and_acc(engine, include_working=False)
    with engine.begin() as connection:
        connection.execute(text("""
            INSERT INTO target_plan
                (id, plan_name, scenario, period_type, effective_from,
                 status, is_realtime)
            VALUES (3, '当前月目标', 'NORMAL', 'MONTH', '2026-06-01', 'DRAFT', 1)
        """))
        connection.execute(text("""
            INSERT INTO metric_target_value
                (id, plan_id, node_id, indicator_id, target_value)
            VALUES (4, 3, 2, 1, 250)
        """))

    result = get_current_with_changes(
        engine,
        node_type="BRANCH",
        indicator_codes=["channel_count"],
        change_windows=[5],
        value_mode="REALTIME_ACC",
    )

    assert result["rows"][0]["targets"]["channel_count"] == 250


def test_realtime_target_does_not_fallback_to_assessment_version():
    engine = _engine()
    _insert_month_target_and_acc(engine, include_working=False)

    result = get_current_with_changes(
        engine,
        node_type="BRANCH",
        indicator_codes=["channel_count"],
        change_windows=[5],
        value_mode="REALTIME_ACC",
    )

    assert result["rows"][0]["targets"]["channel_count"] is None


def test_realtime_acc_loads_only_month_target(monkeypatch):
    engine = _engine()
    _insert_month_target_and_acc(engine)
    loaded_periods: list[str] = []
    original = dashboard_v2_query_service._active_target_map

    def tracking_target_map(session, **kwargs):
        loaded_periods.append(kwargs["period_type"])
        return original(session, **kwargs)

    monkeypatch.setattr(
        dashboard_v2_query_service,
        "_active_target_map",
        tracking_target_map,
    )

    get_current_with_changes(
        engine,
        node_type="BRANCH",
        indicator_codes=["channel_count"],
        change_windows=[5],
        value_mode="REALTIME_ACC",
    )

    assert loaded_periods == ["MONTH"]


def test_realtime_acc_ignores_stale_current_value_from_previous_day():
    engine = _engine()
    _insert_month_target_and_acc(engine)
    with engine.begin() as connection:
        connection.execute(text("""
            UPDATE metric_current
            SET stat_date = '2026-06-29'
            WHERE node_id = 2 AND indicator_id = 1
        """))

    accumulated = get_current_with_changes(
        engine,
        node_type="BRANCH",
        indicator_codes=["channel_count"],
        change_windows=[5],
        value_mode="REALTIME_ACC",
    )
    realtime = get_current_with_changes(
        engine,
        node_type="BRANCH",
        indicator_codes=["channel_count"],
        change_windows=[5],
        value_mode="REALTIME",
    )

    accumulated_row = accumulated["rows"][0]
    assert accumulated_row["metrics"]["channel_count"] is None
    assert accumulated_row["changes"]["channel_count"]["change_5min"] == {
        "value": None,
        "rate": None,
    }
    assert realtime["rows"][0]["metrics"]["channel_count"] == 100


def test_realtime_acc_falls_back_only_within_current_month():
    engine = _engine()
    _insert_month_target_and_acc(engine, acc_date="2026-06-28", acc_value=70)

    fallback = get_current_with_changes(
        engine,
        node_type="BRANCH",
        indicator_codes=["channel_count"],
        change_windows=[5],
        value_mode="REALTIME_ACC",
    )
    assert fallback["rows"][0]["metrics"]["channel_count"] == 170
    assert fallback["accumulation_meta"]["is_fallback"] is True
    assert fallback["accumulation_meta"]["stat_date"] == "2026-06-28"

    with engine.begin() as connection:
        connection.execute(text("UPDATE collection_run SET stat_date='2026-07-02' WHERE id=2"))
    missing = get_current_with_changes(
        engine,
        node_type="BRANCH",
        indicator_codes=["channel_count"],
        change_windows=[5],
        value_mode="REALTIME_ACC",
    )
    assert missing["rows"][0]["metrics"]["channel_count"] is None
    assert missing["accumulation_meta"]["baseline_missing"] is True
    assert missing["accumulation_meta"]["stat_date"] is None


def test_realtime_acc_uses_zero_baseline_on_first_day_of_month():
    engine = _engine()
    _insert_month_target_and_acc(engine)
    with engine.begin() as connection:
        connection.execute(text("UPDATE collection_run SET stat_date='2026-07-01' WHERE id=2"))
        connection.execute(text("""
            UPDATE metric_current
            SET stat_date = '2026-07-01'
            WHERE collection_run_id = 2
        """))

    result = get_current_with_changes(
        engine,
        node_type="BRANCH",
        indicator_codes=["channel_count"],
        change_windows=[5],
        value_mode="REALTIME_ACC",
    )

    assert result["rows"][0]["metrics"]["channel_count"] == 100
    assert result["accumulation_meta"]["baseline_zero"] is True
    assert result["accumulation_meta"]["baseline_missing"] is False


def test_realtime_acc_applies_linear_custom_indicator_to_values_and_targets():
    engine = _engine()
    upsert_custom_indicator(
        engine,
        code="double_count",
        name="双倍渠道数",
        components=[{"source_code": "channel_count", "coefficient": 2}],
    )
    _insert_month_target_and_acc(engine)

    result = get_current_with_changes(
        engine,
        node_type="BRANCH",
        indicator_codes=["double_count"],
        change_windows=[5],
        value_mode="REALTIME_ACC",
    )

    row = result["rows"][0]
    assert row["metrics"]["double_count"] == 360
    assert row["targets"]["double_count"] == 400
    assert row["changes"]["double_count"]["change_5min"] == {
        "value": 20,
        "rate": 20 / 340,
    }


def test_realtime_acc_matrix_sorts_combined_values_and_month_progress():
    engine = _engine()
    _insert_month_target_and_acc(engine)
    with engine.begin() as connection:
        connection.execute(text("""
            INSERT INTO hierarchy_node
                (id, node_type, node_code, node_name, parent_id, level_no, sort_order)
            VALUES (6, 'BRANCH', 'ZY', '郑东新区', 1, 2, 2)
        """))
        connection.execute(text("""
            INSERT INTO metric_current
                (id, node_id, indicator_id, collection_run_id, metric_value,
                 stat_date, collected_at)
            VALUES (6, 6, 1, 2, 90, '2026-06-30', '2026-06-30 10:10:00')
        """))
        connection.execute(text("""
            INSERT INTO metric_snapshot
                (id, collected_at, collection_run_id, node_id, indicator_id, metric_value)
            VALUES (6, '2026-06-30 10:00:00', 1, 6, 1, 80)
        """))
        connection.execute(text("""
            INSERT INTO metric_acc
                (id, period_type, stat_date, node_id, indicator_id,
                 collection_run_id, metric_value, collected_at)
            VALUES
                (2, 'DAY_ACC', '2026-06-29', 6, 1, 1, 200,
                 '2026-06-30 08:00:00')
        """))
        connection.execute(text("""
            INSERT INTO metric_target_value
                (id, plan_id, node_id, indicator_id, target_value)
            VALUES (4, 2, 6, 1, 1000)
        """))

    by_done = get_dashboard_matrix_page(
        engine,
        node_type="BRANCH",
        scope_mode="all",
        indicator_codes=["channel_count"],
        change_window=5,
        sort_indicator="channel_count",
        sort_mode="doneDesc",
        value_mode="REALTIME_ACC",
    )
    by_progress = get_dashboard_matrix_page(
        engine,
        node_type="BRANCH",
        scope_mode="all",
        indicator_codes=["channel_count"],
        change_window=5,
        sort_indicator="channel_count",
        sort_mode="progressDesc",
        value_mode="REALTIME_ACC",
    )

    assert [row["node_code"] for row in by_done["rows"]] == ["ZY", "AQ"]
    assert [row["node_code"] for row in by_progress["rows"]] == ["AQ", "ZY"]


def test_realtime_cache_version_changes_when_accumulation_baseline_arrives():
    engine = _engine()
    before = get_latest_dashboard_run(engine)["data_version"]
    with engine.begin() as connection:
        connection.execute(text("""
            INSERT INTO metric_acc
                (id, period_type, stat_date, node_id, indicator_id,
                 collection_run_id, metric_value, collected_at)
            VALUES
                (1, 'DAY_ACC', '2026-06-29', 2, 1, 1, 80,
                 '2026-06-30 08:00:00')
        """))
    after = get_latest_dashboard_run(engine)["data_version"]

    assert before != after
    assert "2026-06-29" in after
