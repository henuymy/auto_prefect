from __future__ import annotations

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from models.dashboard_request_target import RequestTarget
from services.dashboard_collection_service import CollectionTarget
from services.dashboard_collection_orchestrator import (
    AreaCoverageError,
    AreaIdResolutionError,
    _merge_subtree_retry_collection,
    attach_area_ids_in_session,
    collect_validate_metric_rows_simple,
    affected_grid_targets,
    ensure_complete_area_coverage,
    sync_structure_in_session,
    validate_rows_with_candidate_area_map,
)


def test_complete_area_coverage_accepts_exact_unique_rows():
    ensure_complete_area_coverage(
        {
            "matched_area_count": 4128,
            "matched_row_count": 4128,
        },
        4128,
    )


@pytest.mark.parametrize(
    ("area_count", "row_count"),
    [
        (4127, 4127),
        (4128, 4129),
    ],
)
def test_complete_area_coverage_rejects_missing_or_duplicate_rows(
    area_count,
    row_count,
):
    with pytest.raises(AreaCoverageError):
        ensure_complete_area_coverage(
            {
                "matched_area_count": area_count,
                "matched_row_count": row_count,
            },
            4128,
        )


def test_new_channel_and_failed_manager_locate_parent_grid():
    grid = CollectionTarget(10, "G1", "网格1", "GRID", 10, 2, 10)
    manager = CollectionTarget(
        20,
        "M1",
        "经理1",
        "CHANNEL_MANAGER",
        None,
        10,
        10,
    )

    result = affected_grid_targets(
        [grid, manager],
        {
            "recoverable_errors": [
                {"target_type": "CHANNEL_MANAGER", "target_code": "M1"}
            ],
            "rows": [
                {
                    "level_type": "CHANNEL",
                    "area_code": "C-NEW",
                    "parent_request_code": "M1",
                }
            ],
        },
        {},
    )

    assert result == [grid]


def test_changed_manager_list_locates_grid_from_grid_observation():
    grid = CollectionTarget(10, "G1", "网格1", "GRID", 10, 2, 10)
    old_manager = CollectionTarget(
        20,
        "M-OLD",
        "旧经理",
        "CHANNEL_MANAGER",
        None,
        10,
        10,
    )

    result = affected_grid_targets(
        [grid, old_manager],
        {
            "recoverable_errors": [],
            "rows": [],
            "structure_observations": [
                {
                    "parent_type": "GRID",
                    "parent_code": "G1",
                    "child_type": "CHANNEL_MANAGER",
                    "child_code": "M-NEW",
                    "child_name": "新经理",
                }
            ],
        },
        {},
    )

    assert result == [grid]


def test_subtree_retry_drops_retried_base_recoverable_errors():
    base = {
        "rows": [
            {
                "level_type": "GRID",
                "area_code": "G1",
                "area_name": "网格1",
            },
            {
                "level_type": "CHANNEL",
                "area_code": "C-OLD",
                "area_name": "旧渠道",
                "parent_request_code": "M1",
            },
        ],
        "structure_observations": [
            {
                "parent_type": "GRID",
                "parent_code": "G1",
                "child_type": "CHANNEL_MANAGER",
                "child_code": "M1",
                "child_name": "经理1",
            }
        ],
        "request_count": 2,
        "recoverable_errors": [
            {
                "target_type": "CHANNEL_MANAGER",
                "target_code": "M1",
                "response_code": "1104",
                "error": "平台处理中",
            }
        ],
    }
    retry = {
        "rows": [
            {
                "level_type": "GRID",
                "area_code": "G1",
                "area_name": "网格1",
            },
            {
                "level_type": "CHANNEL",
                "area_code": "C-NEW",
                "area_name": "新渠道",
                "parent_request_code": "M1",
            },
        ],
        "structure_observations": [
            {
                "parent_type": "GRID",
                "parent_code": "G1",
                "child_type": "CHANNEL_MANAGER",
                "child_code": "M1",
                "child_name": "经理1",
            },
            {
                "parent_type": "CHANNEL_MANAGER",
                "parent_code": "M1",
                "child_type": "CHANNEL",
                "child_code": "C-NEW",
                "child_name": "新渠道",
            },
        ],
        "request_count": 2,
        "recoverable_errors": [],
    }

    merged = _merge_subtree_retry_collection(
        base,
        retry,
        {"G1"},
        {"M1"},
    )

    assert merged["recoverable_errors"] == []
    assert [row["area_code"] for row in merged["rows"]] == ["G1", "C-NEW"]


def test_collect_validate_builds_candidate_structure_for_retry(tmp_path):
    """验证新流程：检测到结构漂移后构建候选结构用于重采，而不是立即写库"""

    class RunStore:
        def __init__(self):
            self.updates = []

        def update(self, batch_no, **kwargs):
            self.updates.append((batch_no, kwargs))

    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    with engine.begin() as connection:
        connection.execute(text("""
            CREATE TABLE area (
                id INTEGER PRIMARY KEY,
                area_code VARCHAR(100) NOT NULL,
                area_name VARCHAR(200) NOT NULL,
                level_type VARCHAR(20) NOT NULL,
                level_no INTEGER NOT NULL,
                parent_id INTEGER,
                enabled BOOLEAN NOT NULL DEFAULT 1,
                sort_order INTEGER NOT NULL DEFAULT 0,
                last_seen_at DATETIME,
                missing_count INTEGER NOT NULL DEFAULT 0,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(level_type, area_code)
            )
        """))
        connection.execute(text("""
            CREATE TABLE request_target (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                target_code VARCHAR(100) NOT NULL,
                target_name VARCHAR(200) NOT NULL,
                target_type VARCHAR(20) NOT NULL,
                area_id INTEGER,
                parent_target_id INTEGER,
                enabled BOOLEAN NOT NULL DEFAULT 1,
                sort_order INTEGER NOT NULL DEFAULT 0,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(target_type, target_code)
            )
        """))
        connection.execute(text("""
            INSERT INTO area
                (id, area_code, area_name, level_type, level_no, sort_order)
            VALUES
                (1, 'A', '郑州市', 'CITY', 1, 10),
                (2, 'AQ', '中原区', 'BRANCH', 2, 20),
                (3, 'AQ701', '须水网格', 'GRID', 3, 30)
        """))
        connection.execute(text("""
            INSERT INTO request_target
                (target_code, target_name, target_type, area_id, parent_target_id, enabled, sort_order)
            VALUES
                ('A', '郑州市', 'CITY', 1, NULL, 1, 10),
                ('AQ', '中原区', 'BRANCH', 2, 1, 1, 20),
                ('AQ701', '须水网格', 'GRID', 3, 2, 1, 30)
        """))

    attempts = {"count": 0}

    def fetch(target):
        if target.target_type == "GRID":
            return {
                "reCode": "0000",
                "result": {
                    "tableData": [
                        {"areaCode": "AQ701", "areaName": "须水网格", "metric": "5"},
                        {"areaCode": "M001", "areaName": "经理1", "metric": "0"},
                    ]
                },
            }
        if target.target_type == "CHANNEL_MANAGER":
            attempts["count"] += 1
            return {
                "reCode": "0000",
                "result": {
                    "tableData": [
                        {"areaCode": "M001", "areaName": "经理1", "metric": "0"},
                        {"areaCode": "C001", "areaName": "渠道1", "metric": "3"},
                    ]
                },
            }
        return {
            "reCode": "0000",
            "result": {
                "tableData": [
                    {"areaCode": target.target_code, "areaName": target.target_name, "metric": "1"}
                ]
            },
        }

    validation_calls = {"count": 0}

    def fake_validate(*args, **kwargs):
        validation_calls["count"] += 1
        if validation_calls["count"] == 1:
            raise type(
                "FakeAreaMismatch",
                (RuntimeError,),
                {
                    "result": {
                        "matched_area_count": 2,
                        "matched_row_count": 2,
                        "matched_rows": [],
                        "unmatched_area_count": 1,
                        "unmatched_areas": [
                            {
                                "level_type": "CHANNEL_MANAGER",
                                "area_code": "M001",
                                "area_name": "经理1",
                            }
                        ],
                    }
                },
            )("structure drift")
        return {
            "matched_area_count": 3,
            "matched_row_count": 3,
            "matched_rows": [
                {"area_id": 1, "metric": "1"},
                {"area_id": 2, "metric": "1"},
                {"area_id": 3, "metric": "5"},
            ],
        }

    from services import dashboard_collection_orchestrator as orchestrator

    original_validate = orchestrator.execute_area_validation_phase
    try:
        orchestrator.execute_area_validation_phase = fake_validate
        result = collect_validate_metric_rows_simple(
            engine=engine,
            run_store=RunStore(),
            batch_no="dashboard-test",
            targets=[
                CollectionTarget(1, "A", "郑州市", "CITY", 1, None, 10),
                CollectionTarget(2, "AQ", "中原区", "BRANCH", 2, 1, 20),
                CollectionTarget(3, "AQ701", "须水网格", "GRID", 3, 2, 30),
            ],
            indicator_codes=["metric"],
            fetch_metrics=fetch,
            max_workers=8,
            hard_limit=32,
            anomaly_directory=tmp_path,
            failure_directory=tmp_path / "failure_reports",
        )
    finally:
        orchestrator.execute_area_validation_phase = original_validate

    assert result["attempts"] == 2
    assert attempts["count"] == 1
    assert result["structure_changed"] is True
    assert result["attempt_timings"][0].get("structure_drift_detected", False) is True
    assert result["attempt_timings"][0].get("candidate_build_seconds") is not None
    with Session(engine) as session:
        manager = session.scalar(
            select(RequestTarget).where(
                RequestTarget.target_type == "CHANNEL_MANAGER",
                RequestTarget.target_code == "M001",
            )
        )
        assert manager is None
    engine.dispose()


def test_collect_validate_uses_candidate_graph_on_second_validation(tmp_path):
    class RunStore:
        def update(self, *_args, **_kwargs):
            pass

    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    with engine.begin() as connection:
        connection.execute(text("""
            CREATE TABLE area (
                id INTEGER PRIMARY KEY,
                area_code VARCHAR(100) NOT NULL,
                area_name VARCHAR(200) NOT NULL,
                level_type VARCHAR(20) NOT NULL,
                level_no INTEGER NOT NULL,
                parent_id INTEGER,
                enabled BOOLEAN NOT NULL DEFAULT 1,
                sort_order INTEGER NOT NULL DEFAULT 0,
                last_seen_at DATETIME,
                missing_count INTEGER NOT NULL DEFAULT 0,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(level_type, area_code)
            )
        """))
        connection.execute(text("""
            INSERT INTO area
                (id, area_code, area_name, level_type, level_no, sort_order)
            VALUES
                (1, 'A', '郑州市', 'CITY', 1, 10),
                (2, 'AQ', '中原区', 'BRANCH', 2, 20),
                (3, 'AQ701', '须水网格', 'GRID', 3, 30)
        """))

    def fetch(target):
        if target.target_type == "GRID":
            table_data = [
                {"areaCode": "AQ701", "areaName": "须水网格", "metric": "1"},
                {"areaCode": "M001", "areaName": "经理1", "metric": "0"},
            ]
        elif target.target_type == "CHANNEL_MANAGER":
            table_data = [
                {"areaCode": "M001", "areaName": "经理1", "metric": "0"},
                {"areaCode": "C001", "areaName": "渠道1", "metric": "3"},
            ]
        else:
            table_data = [
                {"areaCode": target.target_code, "areaName": target.target_name, "metric": "1"}
            ]
        return {"reCode": "0000", "result": {"tableData": table_data}}

    validation_calls = {"count": 0}

    def fake_validate(*args, **kwargs):
        validation_calls["count"] += 1
        if validation_calls["count"] == 1:
            raise type(
                "FakeAreaMismatch",
                (RuntimeError,),
                {
                    "result": {
                        "matched_area_count": 2,
                        "matched_row_count": 2,
                        "matched_rows": [{"area_id": 1, "metric": "1"}],
                        "unmatched_area_count": 1,
                        "unmatched_areas": [
                            {
                                "level_type": "CHANNEL_MANAGER",
                                "area_code": "M001",
                                "area_name": "经理1",
                            }
                        ],
                    }
                },
            )("structure drift")
        return {
            "matched_area_count": 1,
            "matched_row_count": 1,
            "matched_rows": [
                {"area_id": 1, "metric": "1"},
            ],
        }

    from services import dashboard_collection_orchestrator as orchestrator

    original_validate = orchestrator.execute_area_validation_phase
    try:
        orchestrator.execute_area_validation_phase = fake_validate
        result = collect_validate_metric_rows_simple(
            engine=engine,
            run_store=RunStore(),
            batch_no="dashboard-candidate",
            targets=[
                CollectionTarget(1, "A", "郑州市", "CITY", 1, None, 10),
                CollectionTarget(2, "AQ701", "须水网格", "GRID", 3, 1, 20),
            ],
            indicator_codes=["metric"],
            fetch_metrics=fetch,
            max_workers=8,
            hard_limit=32,
            anomaly_directory=tmp_path,
            failure_directory=tmp_path / "failure_reports",
        )
    finally:
        orchestrator.execute_area_validation_phase = original_validate
    assert result["attempts"] == 2
    assert result["structure_changed"] is True
    assert result["validation"]["matched_area_count"] == 3
    engine.dispose()


def test_attach_area_ids_raises_when_synced_area_missing():
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    with engine.begin() as connection:
        connection.execute(text("""
            CREATE TABLE area (
                id INTEGER PRIMARY KEY,
                area_code VARCHAR(100) NOT NULL,
                area_name VARCHAR(200) NOT NULL,
                level_type VARCHAR(20) NOT NULL,
                level_no INTEGER NOT NULL,
                parent_id INTEGER,
                enabled BOOLEAN NOT NULL DEFAULT 1,
                sort_order INTEGER NOT NULL DEFAULT 0,
                last_seen_at DATETIME,
                missing_count INTEGER NOT NULL DEFAULT 0,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(level_type, area_code)
            )
        """))
    with Session(engine) as session:
        with pytest.raises(AreaIdResolutionError):
            attach_area_ids_in_session(
                session,
                [
                    {
                        "level_type": "CHANNEL",
                        "area_code": "C001",
                        "area_name": "渠道1",
                        "metric": "3",
                    }
                ],
            )
    engine.dispose()


def test_channel_shrink_builds_change_plan_for_retry(tmp_path):
    class RunStore:
        def update(self, *_args, **_kwargs):
            pass

    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    with engine.begin() as connection:
        connection.execute(text("""
            CREATE TABLE area (
                id INTEGER PRIMARY KEY,
                area_code VARCHAR(100) NOT NULL,
                area_name VARCHAR(200) NOT NULL,
                level_type VARCHAR(20) NOT NULL,
                level_no INTEGER NOT NULL,
                parent_id INTEGER,
                enabled BOOLEAN NOT NULL DEFAULT 1,
                sort_order INTEGER NOT NULL DEFAULT 0,
                last_seen_at DATETIME,
                missing_count INTEGER NOT NULL DEFAULT 0,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(level_type, area_code)
            )
        """))
        connection.execute(text("""
            INSERT INTO area
                (id, area_code, area_name, level_type, level_no, parent_id, sort_order)
            VALUES
                (1, 'A', '郑州市', 'CITY', 1, NULL, 10),
                (2, 'AQ701', '须水网格', 'GRID', 3, 1, 20),
                (3, 'C001', '渠道1', 'CHANNEL', 4, 2, 30),
                (4, 'C002', '渠道2', 'CHANNEL', 4, 2, 40)
        """))

    def fetch(target):
        if target.target_type == "CITY":
            table_data = [
                {"areaCode": "A", "areaName": "郑州市", "metric": "9"},
            ]
        elif target.target_type == "GRID":
            table_data = [
                {"areaCode": "AQ701", "areaName": "须水网格", "metric": "5"},
                {"areaCode": "M001", "areaName": "经理1", "metric": "0"},
            ]
        else:
            table_data = [
                {"areaCode": "M001", "areaName": "经理1", "metric": "0"},
                {"areaCode": "C001", "areaName": "渠道1", "metric": "3"},
            ]
        return {
            "reCode": "0000",
            "result": {"tableData": table_data},
        }

    result = collect_validate_metric_rows_simple(
        engine=engine,
        run_store=RunStore(),
        batch_no="dashboard-shrink",
        targets=[
            CollectionTarget(1, "A", "郑州市", "CITY", 1, None, 10),
            CollectionTarget(2, "AQ701", "须水网格", "GRID", 2, 1, 20),
            CollectionTarget(3, "M001", "经理1", "CHANNEL_MANAGER", None, 2, 30),
        ],
        indicator_codes=["metric"],
        fetch_metrics=fetch,
        max_workers=8,
        hard_limit=32,
        anomaly_directory=tmp_path,
        failure_directory=tmp_path / "failure_reports",
    )

    assert result["attempts"] == 2
    assert result["structure_changed"] is True
    assert result["change_plan"]["removed_areas"] == [
        {
            "level_type": "CHANNEL",
            "area_code": "C002",
            "area_name": "渠道2",
            "area_id": 4,
        }
    ]
    engine.dispose()


def test_new_manager_expansion_retries_and_accepts_new_channel(tmp_path):
    class RunStore:
        def update(self, *_args, **_kwargs):
            pass

    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    with engine.begin() as connection:
        connection.execute(text("""
            CREATE TABLE area (
                id INTEGER PRIMARY KEY,
                area_code VARCHAR(100) NOT NULL,
                area_name VARCHAR(200) NOT NULL,
                level_type VARCHAR(20) NOT NULL,
                level_no INTEGER NOT NULL,
                parent_id INTEGER,
                enabled BOOLEAN NOT NULL DEFAULT 1,
                sort_order INTEGER NOT NULL DEFAULT 0,
                last_seen_at DATETIME,
                missing_count INTEGER NOT NULL DEFAULT 0,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(level_type, area_code)
            )
        """))
        connection.execute(text("""
            INSERT INTO area
                (id, area_code, area_name, level_type, level_no, parent_id, sort_order)
            VALUES
                (1, 'A', '郑州市', 'CITY', 1, NULL, 10),
                (2, 'AQ701', '须水网格', 'GRID', 3, 1, 20)
        """))

    manager_requests = {"count": 0}
    requested_targets = []

    def fetch(target):
        requested_targets.append((target.target_type, target.target_code))
        if target.target_type == "CITY":
            table_data = [
                {"areaCode": "A", "areaName": "郑州市", "metric": "9"},
            ]
        elif target.target_type == "GRID":
            table_data = [
                {"areaCode": "AQ701", "areaName": "须水网格", "metric": "5"},
                {"areaCode": "M001", "areaName": "经理1", "metric": "0"},
            ]
        elif target.target_type == "CHANNEL_MANAGER":
            manager_requests["count"] += 1
            table_data = [
                {"areaCode": "M001", "areaName": "经理1", "metric": "0"},
                {"areaCode": "C001", "areaName": "渠道1", "metric": "3"},
            ]
        else:
            table_data = []
        return {"reCode": "0000", "result": {"tableData": table_data}}

    result = collect_validate_metric_rows_simple(
        engine=engine,
        run_store=RunStore(),
        batch_no="dashboard-expand",
        targets=[
            CollectionTarget(1, "A", "郑州市", "CITY", 1, None, 10),
            CollectionTarget(2, "AQ701", "须水网格", "GRID", 2, 1, 20),
        ],
        indicator_codes=["metric"],
        fetch_metrics=fetch,
        max_workers=8,
        hard_limit=32,
        anomaly_directory=tmp_path,
        failure_directory=tmp_path / "failure_reports",
    )

    assert result["attempts"] == 2
    assert result["structure_changed"] is True
    assert result["change_plan"]["expanded"] is True
    assert manager_requests["count"] == 1
    assert result["validation"]["matched_area_count"] == 3
    assert requested_targets.count(("CITY", "A")) == 1
    assert requested_targets.count(("GRID", "AQ701")) == 2
    assert requested_targets.count(("CHANNEL_MANAGER", "M001")) == 1
    engine.dispose()


def test_full_retry_strategy_retries_all_candidate_targets(tmp_path):
    class RunStore:
        def update(self, *_args, **_kwargs):
            pass

    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    with engine.begin() as connection:
        connection.execute(text("""
            CREATE TABLE area (
                id INTEGER PRIMARY KEY,
                area_code VARCHAR(100) NOT NULL,
                area_name VARCHAR(200) NOT NULL,
                level_type VARCHAR(20) NOT NULL,
                level_no INTEGER NOT NULL,
                parent_id INTEGER,
                enabled BOOLEAN NOT NULL DEFAULT 1,
                sort_order INTEGER NOT NULL DEFAULT 0,
                last_seen_at DATETIME,
                missing_count INTEGER NOT NULL DEFAULT 0,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(level_type, area_code)
            )
        """))
        connection.execute(text("""
            INSERT INTO area
                (id, area_code, area_name, level_type, level_no, parent_id, sort_order)
            VALUES
                (1, 'A', '郑州市', 'CITY', 1, NULL, 10),
                (2, 'AQ701', '须水网格', 'GRID', 3, 1, 20)
        """))

    requested_targets = []

    def fetch(target):
        requested_targets.append((target.target_type, target.target_code))
        if target.target_type == "CITY":
            table_data = [
                {"areaCode": "A", "areaName": "郑州市", "metric": "9"},
            ]
        elif target.target_type == "GRID":
            table_data = [
                {"areaCode": "AQ701", "areaName": "须水网格", "metric": "5"},
                {"areaCode": "M001", "areaName": "经理1", "metric": "0"},
            ]
        elif target.target_type == "CHANNEL_MANAGER":
            table_data = [
                {"areaCode": "M001", "areaName": "经理1", "metric": "0"},
                {"areaCode": "C001", "areaName": "渠道1", "metric": "3"},
            ]
        else:
            table_data = []
        return {"reCode": "0000", "result": {"tableData": table_data}}

    result = collect_validate_metric_rows_simple(
        engine=engine,
        run_store=RunStore(),
        batch_no="dashboard-full-retry",
        targets=[
            CollectionTarget(1, "A", "郑州市", "CITY", 1, None, 10),
            CollectionTarget(2, "AQ701", "须水网格", "GRID", 2, 1, 20),
        ],
        indicator_codes=["metric"],
        fetch_metrics=fetch,
        max_workers=8,
        hard_limit=32,
        anomaly_directory=tmp_path,
        failure_directory=tmp_path / "failure_reports",
        retry_strategy="full",
    )

    assert result["attempts"] == 2
    assert result["validation"]["matched_area_count"] == 3
    assert requested_targets.count(("CITY", "A")) == 2
    assert requested_targets.count(("GRID", "AQ701")) == 2
    assert requested_targets.count(("CHANNEL_MANAGER", "M001")) == 1
    assert result["attempt_timings"][0]["retry_strategy"] == "full"
    assert result["attempt_timings"][0]["subtree_retry"]["affected_grid_codes"] == []
    engine.dispose()


def test_empty_manager_channel_relation_table_bootstraps_without_retry(tmp_path):
    class RunStore:
        def update(self, *_args, **_kwargs):
            pass

    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    with engine.begin() as connection:
        connection.execute(text("""
            CREATE TABLE area (
                id INTEGER PRIMARY KEY,
                area_code VARCHAR(100) NOT NULL,
                area_name VARCHAR(200) NOT NULL,
                level_type VARCHAR(20) NOT NULL,
                level_no INTEGER NOT NULL,
                parent_id INTEGER,
                enabled BOOLEAN NOT NULL DEFAULT 1,
                sort_order INTEGER NOT NULL DEFAULT 0,
                last_seen_at DATETIME,
                missing_count INTEGER NOT NULL DEFAULT 0,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(level_type, area_code)
            )
        """))
        connection.execute(text("""
            CREATE TABLE request_target (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                target_code VARCHAR(100) NOT NULL,
                target_name VARCHAR(200) NOT NULL,
                target_type VARCHAR(20) NOT NULL,
                area_id INTEGER,
                parent_target_id INTEGER,
                enabled BOOLEAN NOT NULL DEFAULT 1,
                sort_order INTEGER NOT NULL DEFAULT 0,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(target_type, target_code)
            )
        """))
        connection.execute(text("""
            CREATE TABLE channel_manager_area (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                manager_target_id INTEGER NOT NULL,
                channel_area_id INTEGER NOT NULL,
                grid_area_id INTEGER,
                enabled BOOLEAN NOT NULL DEFAULT 1,
                last_seen_at DATETIME,
                missing_count INTEGER NOT NULL DEFAULT 0,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(manager_target_id, channel_area_id)
            )
        """))
        connection.execute(text("""
            INSERT INTO area
                (id, area_code, area_name, level_type, level_no, parent_id, sort_order)
            VALUES
                (1, 'A', '郑州市', 'CITY', 1, NULL, 10),
                (2, 'AQ701', '须水网格', 'GRID', 3, 1, 20),
                (3, 'C001', '渠道1', 'CHANNEL', 4, 2, 30)
        """))
        connection.execute(text("""
            INSERT INTO request_target
                (id, target_code, target_name, target_type, area_id, parent_target_id, enabled, sort_order)
            VALUES
                (1, 'A', '郑州市', 'CITY', 1, NULL, 1, 10),
                (2, 'AQ701', '须水网格', 'GRID', 2, 1, 1, 20),
                (3, 'M001', '经理1', 'CHANNEL_MANAGER', NULL, 2, 1, 30)
        """))

    requested_targets = []

    def fetch(target):
        requested_targets.append((target.target_type, target.target_code))
        if target.target_type == "CITY":
            table_data = [
                {"areaCode": "A", "areaName": "郑州市", "metric": "9"},
            ]
        elif target.target_type == "GRID":
            table_data = [
                {"areaCode": "AQ701", "areaName": "须水网格", "metric": "5"},
                {"areaCode": "M001", "areaName": "经理1", "metric": "0"},
            ]
        elif target.target_type == "CHANNEL_MANAGER":
            table_data = [
                {"areaCode": "M001", "areaName": "经理1", "metric": "0"},
                {"areaCode": "C001", "areaName": "渠道1", "metric": "3"},
            ]
        else:
            table_data = []
        return {"reCode": "0000", "result": {"tableData": table_data}}

    result = collect_validate_metric_rows_simple(
        engine=engine,
        run_store=RunStore(),
        batch_no="dashboard-bootstrap-relations",
        targets=[
            CollectionTarget(1, "A", "郑州市", "CITY", 1, None, 10),
            CollectionTarget(2, "AQ701", "须水网格", "GRID", 2, 1, 20),
            CollectionTarget(3, "M001", "经理1", "CHANNEL_MANAGER", None, 2, 30),
        ],
        indicator_codes=["metric"],
        fetch_metrics=fetch,
        max_workers=8,
        hard_limit=32,
        anomaly_directory=tmp_path,
        failure_directory=tmp_path / "failure_reports",
    )

    assert result["attempts"] == 1
    assert result["structure_changed"] is True
    assert result["change_plan"]["relation_bootstrap"] is True
    assert result["structure_change_summary"]["changed"] is True
    assert result["structure_change_summary"]["relation_bootstrap"] is True
    assert result["structure_change_summary"]["counts"]["added_relations"] == 0
    assert result["attempt_timings"][0]["manager_channel_relation_bootstrap"] is True
    assert result["attempt_timings"][0]["manager_channel_relation_drift"] is False
    assert requested_targets.count(("CITY", "A")) == 1
    assert requested_targets.count(("GRID", "AQ701")) == 1
    assert requested_targets.count(("CHANNEL_MANAGER", "M001")) == 1
    engine.dispose()


def test_candidate_validation_rejects_recoverable_collection_errors():
    with pytest.raises(AreaCoverageError, match="可恢复采集错误"):
        validate_rows_with_candidate_area_map(
            [
                {
                    "level_type": "CHANNEL",
                    "area_code": "C001",
                    "area_name": "渠道1",
                    "metric": "3",
                }
            ],
            ["metric"],
            [{"target_type": "CHANNEL_MANAGER", "target_code": "M001"}],
        )


def test_sync_structure_disables_removed_channel_area():
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    with engine.begin() as connection:
        connection.execute(text("""
            CREATE TABLE area (
                id INTEGER PRIMARY KEY,
                area_code VARCHAR(100) NOT NULL,
                area_name VARCHAR(200) NOT NULL,
                level_type VARCHAR(20) NOT NULL,
                level_no INTEGER NOT NULL,
                parent_id INTEGER,
                enabled BOOLEAN NOT NULL DEFAULT 1,
                sort_order INTEGER NOT NULL DEFAULT 0,
                last_seen_at DATETIME,
                missing_count INTEGER NOT NULL DEFAULT 0,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(level_type, area_code)
            )
        """))
        connection.execute(text("""
            CREATE TABLE request_target (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                target_code VARCHAR(100) NOT NULL,
                target_name VARCHAR(200) NOT NULL,
                target_type VARCHAR(20) NOT NULL,
                area_id INTEGER,
                parent_target_id INTEGER,
                enabled BOOLEAN NOT NULL DEFAULT 1,
                sort_order INTEGER NOT NULL DEFAULT 0,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(target_type, target_code)
            )
        """))
        connection.execute(text("""
            CREATE TABLE channel_manager_area (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                manager_target_id INTEGER NOT NULL,
                channel_area_id INTEGER NOT NULL,
                grid_area_id INTEGER,
                enabled BOOLEAN NOT NULL DEFAULT 1,
                last_seen_at DATETIME,
                missing_count INTEGER NOT NULL DEFAULT 0,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(manager_target_id, channel_area_id)
            )
        """))
        connection.execute(text("""
            INSERT INTO area
                (id, area_code, area_name, level_type, level_no, enabled, missing_count)
            VALUES
                (1, 'C002', '渠道2', 'CHANNEL', 4, 1, 0)
        """))

    with Session(engine) as session:
        with session.begin():
            result = sync_structure_in_session(
                session,
                rows=[],
                structure_observations=[],
                collected_at=__import__("datetime").datetime(2026, 6, 16, 10, 0),
                change_plan={
                    "removed_areas": [
                        {
                            "level_type": "CHANNEL",
                            "area_code": "C002",
                            "area_name": "渠道2",
                            "area_id": 1,
                        }
                    ]
                },
            )
        assert result["area"]["disabled"] == 1
        enabled, missing_count = session.execute(
            text("SELECT enabled, missing_count FROM area WHERE area_code='C002'")
        ).one()

    assert enabled == 0
    assert missing_count == 1
    engine.dispose()


def test_sync_structure_skips_manager_channel_relations_when_table_missing():
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    with engine.begin() as connection:
        connection.execute(text("""
            CREATE TABLE area (
                id INTEGER PRIMARY KEY,
                area_code VARCHAR(100) NOT NULL,
                area_name VARCHAR(200) NOT NULL,
                level_type VARCHAR(20) NOT NULL,
                level_no INTEGER NOT NULL,
                parent_id INTEGER,
                enabled BOOLEAN NOT NULL DEFAULT 1,
                sort_order INTEGER NOT NULL DEFAULT 0,
                last_seen_at DATETIME,
                missing_count INTEGER NOT NULL DEFAULT 0,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(level_type, area_code)
            )
        """))
        connection.execute(text("""
            CREATE TABLE request_target (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                target_code VARCHAR(100) NOT NULL,
                target_name VARCHAR(200) NOT NULL,
                target_type VARCHAR(20) NOT NULL,
                area_id INTEGER,
                parent_target_id INTEGER,
                enabled BOOLEAN NOT NULL DEFAULT 1,
                sort_order INTEGER NOT NULL DEFAULT 0,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(target_type, target_code)
            )
        """))
        connection.execute(text("""
            INSERT INTO area
                (id, area_code, area_name, level_type, level_no, parent_id, enabled)
            VALUES
                (1, 'AQ701', '须水网格', 'GRID', 3, NULL, 1),
                (2, 'C001', '渠道1', 'CHANNEL', 4, 1, 1)
        """))
        connection.execute(text("""
            INSERT INTO request_target
                (id, target_code, target_name, target_type, area_id, parent_target_id, enabled)
            VALUES
                (1, 'AQ701', '须水网格', 'GRID', 1, NULL, 1)
        """))

    with Session(engine) as session:
        with session.begin():
            result = sync_structure_in_session(
                session,
                rows=[
                    {
                        "level_type": "GRID",
                        "area_code": "AQ701",
                        "area_name": "须水网格",
                        "parent_request_code": "AQ701",
                    },
                    {
                        "level_type": "CHANNEL",
                        "area_code": "C001",
                        "area_name": "渠道1",
                        "parent_request_code": "M001",
                    },
                ],
                structure_observations=[
                    {
                        "parent_type": "GRID",
                        "parent_code": "AQ701",
                        "child_type": "CHANNEL_MANAGER",
                        "child_code": "M001",
                        "child_name": "经理1",
                    },
                    {
                        "parent_type": "CHANNEL_MANAGER",
                        "parent_code": "M001",
                        "child_type": "CHANNEL",
                        "child_code": "C001",
                        "child_name": "渠道1",
                    },
                ],
                collected_at=__import__("datetime").datetime(2026, 6, 16, 10, 0),
            )
        manager_count = session.execute(
            text(
                "SELECT COUNT(*) FROM request_target "
                "WHERE target_type='CHANNEL_MANAGER' AND target_code='M001'"
            )
        ).scalar_one()

    assert result["channel_manager_area"] == {
        "created": 0,
        "updated": 0,
        "disabled": 0,
        "skipped": 1,
    }
    assert manager_count == 1
    engine.dispose()


def test_manager_shrink_builds_removed_targets_for_retry(tmp_path):
    class RunStore:
        def update(self, *_args, **_kwargs):
            pass

    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    with engine.begin() as connection:
        connection.execute(text("""
            CREATE TABLE area (
                id INTEGER PRIMARY KEY,
                area_code VARCHAR(100) NOT NULL,
                area_name VARCHAR(200) NOT NULL,
                level_type VARCHAR(20) NOT NULL,
                level_no INTEGER NOT NULL,
                parent_id INTEGER,
                enabled BOOLEAN NOT NULL DEFAULT 1,
                sort_order INTEGER NOT NULL DEFAULT 0,
                last_seen_at DATETIME,
                missing_count INTEGER NOT NULL DEFAULT 0,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(level_type, area_code)
            )
        """))
        connection.execute(text("""
            CREATE TABLE request_target (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                target_code VARCHAR(100) NOT NULL,
                target_name VARCHAR(200) NOT NULL,
                target_type VARCHAR(20) NOT NULL,
                area_id INTEGER,
                parent_target_id INTEGER,
                enabled BOOLEAN NOT NULL DEFAULT 1,
                sort_order INTEGER NOT NULL DEFAULT 0,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(target_type, target_code)
            )
        """))
        connection.execute(text("""
            INSERT INTO area
                (id, area_code, area_name, level_type, level_no, parent_id, sort_order)
            VALUES
                (1, 'A', '郑州市', 'CITY', 1, NULL, 10),
                (2, 'AQ701', '须水网格', 'GRID', 3, 1, 20),
                (3, 'C001', '渠道1', 'CHANNEL', 4, 2, 30)
        """))
        connection.execute(text("""
            INSERT INTO request_target
                (id, target_code, target_name, target_type, area_id, parent_target_id, enabled, sort_order)
            VALUES
                (1, 'A', '郑州市', 'CITY', 1, NULL, 1, 10),
                (2, 'AQ701', '须水网格', 'GRID', 2, 1, 1, 20),
                (3, 'M001', '经理1', 'CHANNEL_MANAGER', NULL, 2, 1, 30),
                (4, 'M002', '经理2', 'CHANNEL_MANAGER', NULL, 2, 1, 40)
        """))

    requested_targets = []

    def fetch(target):
        requested_targets.append((target.target_type, target.target_code))
        if target.target_type == "CITY":
            table_data = [
                {"areaCode": "A", "areaName": "郑州市", "metric": "9"},
            ]
        elif target.target_type == "GRID":
            table_data = [
                {"areaCode": "AQ701", "areaName": "须水网格", "metric": "5"},
                {"areaCode": "M001", "areaName": "经理1", "metric": "0"},
            ]
        elif target.target_type == "CHANNEL_MANAGER":
            table_data = [
                {"areaCode": target.target_code, "areaName": target.target_name, "metric": "0"},
                {"areaCode": "C001", "areaName": "渠道1", "metric": "3"},
            ]
        else:
            table_data = []
        return {"reCode": "0000", "result": {"tableData": table_data}}

    result = collect_validate_metric_rows_simple(
        engine=engine,
        run_store=RunStore(),
        batch_no="dashboard-manager-shrink",
        targets=[
            CollectionTarget(1, "A", "郑州市", "CITY", 1, None, 10),
            CollectionTarget(2, "AQ701", "须水网格", "GRID", 2, 1, 20),
            CollectionTarget(3, "M001", "经理1", "CHANNEL_MANAGER", None, 2, 30),
            CollectionTarget(4, "M002", "经理2", "CHANNEL_MANAGER", None, 2, 40),
        ],
        indicator_codes=["metric"],
        fetch_metrics=fetch,
        max_workers=8,
        hard_limit=32,
        anomaly_directory=tmp_path,
        failure_directory=tmp_path / "failure_reports",
    )

    assert result["attempts"] == 2
    assert result["structure_changed"] is True
    assert result["change_plan"]["removed_targets"] == [
        {
            "target_type": "CHANNEL_MANAGER",
            "target_code": "M002",
            "target_name": "经理2",
            "parent_type": "GRID",
            "parent_code": "AQ701",
        }
    ]
    summary = result["structure_change_summary"]
    assert summary["changed"] is True
    assert summary["affected_grid_codes"] == ["AQ701"]
    assert summary["counts"]["removed_targets"] == 1
    assert summary["samples"]["removed_targets"] == result["change_plan"]["removed_targets"]
    assert requested_targets.count(("CITY", "A")) == 1
    assert requested_targets.count(("GRID", "AQ701")) == 2
    assert requested_targets.count(("CHANNEL_MANAGER", "M001")) == 2
    assert requested_targets.count(("CHANNEL_MANAGER", "M002")) == 1
    engine.dispose()


def test_sync_structure_updates_manager_channel_relations():
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    with engine.begin() as connection:
        connection.execute(text("""
            CREATE TABLE area (
                id INTEGER PRIMARY KEY,
                area_code VARCHAR(100) NOT NULL,
                area_name VARCHAR(200) NOT NULL,
                level_type VARCHAR(20) NOT NULL,
                level_no INTEGER NOT NULL,
                parent_id INTEGER,
                enabled BOOLEAN NOT NULL DEFAULT 1,
                sort_order INTEGER NOT NULL DEFAULT 0,
                last_seen_at DATETIME,
                missing_count INTEGER NOT NULL DEFAULT 0,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(level_type, area_code)
            )
        """))
        connection.execute(text("""
            CREATE TABLE request_target (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                target_code VARCHAR(100) NOT NULL,
                target_name VARCHAR(200) NOT NULL,
                target_type VARCHAR(20) NOT NULL,
                area_id INTEGER,
                parent_target_id INTEGER,
                enabled BOOLEAN NOT NULL DEFAULT 1,
                sort_order INTEGER NOT NULL DEFAULT 0,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(target_type, target_code)
            )
        """))
        connection.execute(text("""
            CREATE TABLE channel_manager_area (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                manager_target_id INTEGER NOT NULL,
                channel_area_id INTEGER NOT NULL,
                grid_area_id INTEGER,
                enabled BOOLEAN NOT NULL DEFAULT 1,
                last_seen_at DATETIME,
                missing_count INTEGER NOT NULL DEFAULT 0,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(manager_target_id, channel_area_id)
            )
        """))
        connection.execute(text("""
            INSERT INTO area
                (id, area_code, area_name, level_type, level_no, parent_id, enabled)
            VALUES
                (1, 'AQ701', '须水网格', 'GRID', 3, NULL, 1),
                (2, 'C001', '渠道1', 'CHANNEL', 4, 1, 1),
                (3, 'C002', '渠道2', 'CHANNEL', 4, 1, 1)
        """))
        connection.execute(text("""
            INSERT INTO request_target
                (id, target_code, target_name, target_type, area_id, parent_target_id, enabled)
            VALUES
                (1, 'AQ701', '须水网格', 'GRID', 1, NULL, 1),
                (2, 'M001', '经理1', 'CHANNEL_MANAGER', NULL, 1, 1)
        """))
        connection.execute(text("""
            INSERT INTO channel_manager_area
                (id, manager_target_id, channel_area_id, grid_area_id, enabled, missing_count)
            VALUES
                (1, 2, 3, 1, 1, 0)
        """))

    with Session(engine) as session:
        with session.begin():
            result = sync_structure_in_session(
                session,
                rows=[
                    {
                        "level_type": "GRID",
                        "area_code": "AQ701",
                        "area_name": "须水网格",
                        "parent_request_code": "AQ701",
                    },
                    {
                        "level_type": "CHANNEL",
                        "area_code": "C001",
                        "area_name": "渠道1",
                        "parent_request_code": "M001",
                    },
                ],
                structure_observations=[
                    {
                        "parent_type": "GRID",
                        "parent_code": "AQ701",
                        "child_type": "CHANNEL_MANAGER",
                        "child_code": "M001",
                        "child_name": "经理1",
                    },
                    {
                        "parent_type": "CHANNEL_MANAGER",
                        "parent_code": "M001",
                        "child_type": "CHANNEL",
                        "child_code": "C001",
                        "child_name": "渠道1",
                    },
                ],
                collected_at=__import__("datetime").datetime(2026, 6, 16, 10, 0),
            )
        rows = session.execute(
            text(
                "SELECT manager_target_id, channel_area_id, enabled, missing_count "
                "FROM channel_manager_area ORDER BY channel_area_id"
            )
        ).all()

    assert result["channel_manager_area"] == {
        "created": 1,
        "updated": 0,
        "disabled": 0,
        "missing_incremented": 1,
        "missing_disable_threshold": 2,
    }
    assert rows == [(2, 2, 1, 0), (2, 3, 1, 1)]
    engine.dispose()


def test_sync_structure_moves_channel_between_managers_without_disabling_area():
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    with engine.begin() as connection:
        connection.execute(text("""
            CREATE TABLE area (
                id INTEGER PRIMARY KEY,
                area_code VARCHAR(100) NOT NULL,
                area_name VARCHAR(200) NOT NULL,
                level_type VARCHAR(20) NOT NULL,
                level_no INTEGER NOT NULL,
                parent_id INTEGER,
                enabled BOOLEAN NOT NULL DEFAULT 1,
                sort_order INTEGER NOT NULL DEFAULT 0,
                last_seen_at DATETIME,
                missing_count INTEGER NOT NULL DEFAULT 0,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(level_type, area_code)
            )
        """))
        connection.execute(text("""
            CREATE TABLE request_target (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                target_code VARCHAR(100) NOT NULL,
                target_name VARCHAR(200) NOT NULL,
                target_type VARCHAR(20) NOT NULL,
                area_id INTEGER,
                parent_target_id INTEGER,
                enabled BOOLEAN NOT NULL DEFAULT 1,
                sort_order INTEGER NOT NULL DEFAULT 0,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(target_type, target_code)
            )
        """))
        connection.execute(text("""
            CREATE TABLE channel_manager_area (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                manager_target_id INTEGER NOT NULL,
                channel_area_id INTEGER NOT NULL,
                grid_area_id INTEGER,
                enabled BOOLEAN NOT NULL DEFAULT 1,
                last_seen_at DATETIME,
                missing_count INTEGER NOT NULL DEFAULT 0,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(manager_target_id, channel_area_id)
            )
        """))
        connection.execute(text("""
            INSERT INTO area
                (id, area_code, area_name, level_type, level_no, parent_id, enabled, missing_count)
            VALUES
                (1, 'AQ701', '须水网格', 'GRID', 3, NULL, 1, 0),
                (2, 'C001', '渠道1', 'CHANNEL', 4, 1, 1, 0)
        """))
        connection.execute(text("""
            INSERT INTO request_target
                (id, target_code, target_name, target_type, area_id, parent_target_id, enabled)
            VALUES
                (1, 'AQ701', '须水网格', 'GRID', 1, NULL, 1),
                (2, 'M001', '经理1', 'CHANNEL_MANAGER', NULL, 1, 1),
                (3, 'M002', '经理2', 'CHANNEL_MANAGER', NULL, 1, 1)
        """))
        connection.execute(text("""
            INSERT INTO channel_manager_area
                (id, manager_target_id, channel_area_id, grid_area_id, enabled, missing_count)
            VALUES
                (1, 2, 2, 1, 1, 0)
        """))

    with Session(engine) as session:
        with session.begin():
            result = sync_structure_in_session(
                session,
                rows=[
                    {
                        "level_type": "GRID",
                        "area_code": "AQ701",
                        "area_name": "须水网格",
                        "parent_request_code": "AQ701",
                    },
                    {
                        "level_type": "CHANNEL",
                        "area_code": "C001",
                        "area_name": "渠道1",
                        "parent_request_code": "M002",
                    },
                ],
                structure_observations=[
                    {
                        "parent_type": "GRID",
                        "parent_code": "AQ701",
                        "child_type": "CHANNEL_MANAGER",
                        "child_code": "M001",
                        "child_name": "经理1",
                    },
                    {
                        "parent_type": "GRID",
                        "parent_code": "AQ701",
                        "child_type": "CHANNEL_MANAGER",
                        "child_code": "M002",
                        "child_name": "经理2",
                    },
                    {
                        "parent_type": "CHANNEL_MANAGER",
                        "parent_code": "M002",
                        "child_type": "CHANNEL",
                        "child_code": "C001",
                        "child_name": "渠道1",
                    },
                ],
                collected_at=__import__("datetime").datetime(2026, 6, 16, 10, 0),
            )
        area_enabled, area_missing = session.execute(
            text("SELECT enabled, missing_count FROM area WHERE area_code='C001'")
        ).one()
        relations = session.execute(
            text(
                "SELECT manager_target_id, channel_area_id, enabled, missing_count "
                "FROM channel_manager_area ORDER BY manager_target_id"
            )
        ).all()

    assert result["area"]["disabled"] == 0
    assert result["channel_manager_area"] == {
        "created": 1,
        "updated": 0,
        "disabled": 1,
        "missing_incremented": 0,
        "missing_disable_threshold": 2,
    }
    assert area_enabled == 1
    assert area_missing == 0
    assert relations == [(2, 2, 0, 1), (3, 2, 1, 0)]
    engine.dispose()


def test_sync_structure_disables_relation_after_missing_threshold():
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    with engine.begin() as connection:
        connection.execute(text("""
            CREATE TABLE area (
                id INTEGER PRIMARY KEY,
                area_code VARCHAR(100) NOT NULL,
                area_name VARCHAR(200) NOT NULL,
                level_type VARCHAR(20) NOT NULL,
                level_no INTEGER NOT NULL,
                parent_id INTEGER,
                enabled BOOLEAN NOT NULL DEFAULT 1,
                sort_order INTEGER NOT NULL DEFAULT 0,
                last_seen_at DATETIME,
                missing_count INTEGER NOT NULL DEFAULT 0,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(level_type, area_code)
            )
        """))
        connection.execute(text("""
            CREATE TABLE request_target (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                target_code VARCHAR(100) NOT NULL,
                target_name VARCHAR(200) NOT NULL,
                target_type VARCHAR(20) NOT NULL,
                area_id INTEGER,
                parent_target_id INTEGER,
                enabled BOOLEAN NOT NULL DEFAULT 1,
                sort_order INTEGER NOT NULL DEFAULT 0,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(target_type, target_code)
            )
        """))
        connection.execute(text("""
            CREATE TABLE channel_manager_area (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                manager_target_id INTEGER NOT NULL,
                channel_area_id INTEGER NOT NULL,
                grid_area_id INTEGER,
                enabled BOOLEAN NOT NULL DEFAULT 1,
                last_seen_at DATETIME,
                missing_count INTEGER NOT NULL DEFAULT 0,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(manager_target_id, channel_area_id)
            )
        """))
        connection.execute(text("""
            INSERT INTO area
                (id, area_code, area_name, level_type, level_no, parent_id, enabled)
            VALUES
                (1, 'AQ701', '须水网格', 'GRID', 3, NULL, 1),
                (2, 'C002', '渠道2', 'CHANNEL', 4, 1, 1)
        """))
        connection.execute(text("""
            INSERT INTO request_target
                (id, target_code, target_name, target_type, area_id, parent_target_id, enabled)
            VALUES
                (1, 'AQ701', '须水网格', 'GRID', 1, NULL, 1),
                (2, 'M001', '经理1', 'CHANNEL_MANAGER', NULL, 1, 1)
        """))
        connection.execute(text("""
            INSERT INTO channel_manager_area
                (id, manager_target_id, channel_area_id, grid_area_id, enabled, missing_count)
            VALUES
                (1, 2, 2, 1, 1, 1)
        """))

    with Session(engine) as session:
        with session.begin():
            result = sync_structure_in_session(
                session,
                rows=[
                    {
                        "level_type": "GRID",
                        "area_code": "AQ701",
                        "area_name": "须水网格",
                        "parent_request_code": "AQ701",
                    },
                ],
                structure_observations=[
                    {
                        "parent_type": "GRID",
                        "parent_code": "AQ701",
                        "child_type": "CHANNEL_MANAGER",
                        "child_code": "M001",
                        "child_name": "经理1",
                    },
                ],
                collected_at=__import__("datetime").datetime(2026, 6, 16, 10, 0),
                relation_missing_disable_threshold=2,
            )
        relation = session.execute(
            text(
                "SELECT enabled, missing_count FROM channel_manager_area "
                "WHERE manager_target_id=2 AND channel_area_id=2"
            )
        ).one()

    assert result["channel_manager_area"] == {
        "created": 0,
        "updated": 0,
        "disabled": 1,
        "missing_incremented": 1,
        "missing_disable_threshold": 2,
    }
    assert relation == (0, 2)
    engine.dispose()
