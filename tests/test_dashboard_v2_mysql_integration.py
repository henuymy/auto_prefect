from __future__ import annotations

import os
import re
from datetime import datetime
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from infrastructure.dashboard_v2_run_store import MySQLV2CollectionRunStore
from services.dashboard_v2_hierarchy import (
    initialize_base_hierarchy_in_session,
    load_v2_collection_targets,
    validate_bootstrap_manifest,
)
from services.dashboard_v2_orchestrator import collect_validate_metric_rows_v2
from services.dashboard_v2_readiness import check_dashboard_v2_schema
from services.dashboard_v2_retention_service import maintain_v2_snapshot_partitions


pytestmark = pytest.mark.mysql_integration
PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def v2_mysql_engine():
    raw_url = os.environ.get("DASHBOARD_TEST_MYSQL_URL", "").strip()
    if not raw_url:
        pytest.skip("DASHBOARD_TEST_MYSQL_URL 未配置，跳过 V2 MySQL 集成测试")
    url = make_url(raw_url)
    database = str(url.database or "")
    if not re.search(r"(^test|test$|_test$|_ci$)", database, re.IGNORECASE):
        pytest.fail("V2 MySQL 测试只允许连接 test/ci 库")

    engine = create_engine(url, pool_pre_ping=True)
    previous_env = {
        name: os.environ.get(name)
        for name in (
            "DASHBOARD_MYSQL_HOST",
            "DASHBOARD_MYSQL_PORT",
            "DASHBOARD_MYSQL_DATABASE",
            "DASHBOARD_MYSQL_USER",
            "DASHBOARD_MYSQL_PASSWORD",
        )
    }
    os.environ.update(
        {
            "DASHBOARD_MYSQL_HOST": str(url.host or "127.0.0.1"),
            "DASHBOARD_MYSQL_PORT": str(url.port or 3306),
            "DASHBOARD_MYSQL_DATABASE": database,
            "DASHBOARD_MYSQL_USER": str(url.username or "root"),
            "DASHBOARD_MYSQL_PASSWORD": str(url.password or ""),
        }
    )
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql("SET FOREIGN_KEY_CHECKS=0")
            for table_name in inspect(connection).get_table_names():
                escaped = table_name.replace("`", "``")
                connection.exec_driver_sql(f"DROP TABLE `{escaped}`")
            connection.exec_driver_sql("SET FOREIGN_KEY_CHECKS=1")

        config = Config(str(PROJECT_ROOT / "alembic_dashboard_v2.ini"))
        command.upgrade(config, "head")
        yield engine, config
    finally:
        engine.dispose()
        for name, value in previous_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def test_v2_baseline_creates_exact_schema_and_partition(v2_mysql_engine):
    engine, _ = v2_mysql_engine
    with engine.connect() as connection:
        tables = set(inspect(connection).get_table_names())
        revision = connection.scalar(text("SELECT version_num FROM alembic_version"))
        primary_columns = list(
            connection.scalars(
                text(
                    """
                    SELECT COLUMN_NAME
                    FROM information_schema.STATISTICS
                    WHERE TABLE_SCHEMA = DATABASE()
                      AND TABLE_NAME = 'metric_snapshot'
                      AND INDEX_NAME = 'PRIMARY'
                    ORDER BY SEQ_IN_INDEX
                    """
                )
            )
        )
        partitions = set(
            connection.scalars(
                text(
                    """
                    SELECT PARTITION_NAME
                    FROM information_schema.PARTITIONS
                    WHERE TABLE_SCHEMA = DATABASE()
                      AND TABLE_NAME = 'metric_snapshot'
                      AND PARTITION_NAME IS NOT NULL
                    """
                )
            )
        )

    assert tables == EXPECTED_MYSQL_TABLES
    assert revision == "20260805_0006"
    assert primary_columns == ["id", "collected_at"]
    assert partitions == {"p_future"}


def test_v2_migration_matches_orm_metadata(v2_mysql_engine):
    _, config = v2_mysql_engine

    command.check(config)


def test_v2_bootstrap_is_idempotent_and_loads_request_targets(v2_mysql_engine):
    engine, _ = v2_mysql_engine
    manifest = validate_bootstrap_manifest(
        [
            {
                "node_type": "CITY",
                "node_code": "A",
                "node_name": "城市",
                "parent_node_code": None,
                "sort_order": 1,
            },
            {
                "node_type": "BRANCH",
                "node_code": "B1",
                "node_name": "分局1",
                "parent_node_code": "A",
                "sort_order": 2,
            },
            {
                "node_type": "GRID",
                "node_code": "G1",
                "node_name": "网格1",
                "parent_node_code": "B1",
                "sort_order": 3,
            },
        ]
    )
    with Session(engine) as session, session.begin():
        first = initialize_base_hierarchy_in_session(
            session,
            manifest,
            initialized_at=datetime(2026, 6, 30, 12, 0),
        )
    with Session(engine) as session, session.begin():
        second = initialize_base_hierarchy_in_session(
            session,
            manifest,
            initialized_at=datetime(2026, 6, 30, 12, 5),
        )

    targets = load_v2_collection_targets(engine)
    assert first == {
        "created": 3,
        "reused": 0,
        "history_created": 2,
        "node_count": 3,
    }
    assert second == {
        "created": 0,
        "reused": 3,
        "history_created": 0,
        "node_count": 3,
    }
    assert [(target.target_type, target.target_code) for target in targets] == [
        ("CITY", "A"),
        ("BRANCH", "B1"),
        ("GRID", "G1"),
    ]


def test_v2_stable_collection_and_manager_self_metric(v2_mysql_engine, tmp_path):
    engine, _ = v2_mysql_engine
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO hierarchy_node
                    (node_type, node_code, node_name, parent_id, level_no,
                     request_enabled, metric_enabled)
                SELECT 'CHANNEL_MANAGER', 'M1', '经理1', grid.id, 4, 1, 1
                FROM hierarchy_node grid
                WHERE grid.node_type='GRID' AND grid.node_code='G1'
                """
            )
        )
        manager_id = connection.scalar(
            text(
                "SELECT id FROM hierarchy_node "
                "WHERE node_type='CHANNEL_MANAGER' AND node_code='M1'"
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO hierarchy_node
                    (node_type, node_code, node_name, parent_id, level_no,
                     request_enabled, metric_enabled)
                VALUES ('CHANNEL', 'C1', '渠道1', :manager_id, 5, 0, 1)
                """
            ),
            {"manager_id": manager_id},
        )
        channel_id = connection.scalar(
            text(
                "SELECT id FROM hierarchy_node "
                "WHERE node_type='CHANNEL' AND node_code='C1'"
            )
        )
        grid_id = connection.scalar(
            text(
                "SELECT id FROM hierarchy_node "
                "WHERE node_type='GRID' AND node_code='G1'"
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO hierarchy_parent_history
                    (child_node_id, parent_node_id, valid_from, change_type)
                VALUES
                    (:manager_id, :grid_id, NOW(3), 'CREATED'),
                    (:channel_id, :manager_id, NOW(3), 'CREATED')
                """
            ),
            {
                "manager_id": manager_id,
                "grid_id": grid_id,
                "channel_id": channel_id,
            },
        )
        connection.execute(
            text(
                """
                INSERT INTO indicator
                    (code, name, indicator_type, storage_mode, enabled,
                     source_active, sort_order)
                VALUES ('m', '指标', 'SOURCE', 'STORE', 1, 1, 1)
                """
            )
        )

    run_store = MySQLV2CollectionRunStore(engine)
    run_store.create("v2-stable", "MANUAL", run_type="REALTIME")
    targets = load_v2_collection_targets(engine)
    result = collect_validate_metric_rows_v2(
        engine=engine,
        run_store=run_store,
        batch_no="v2-stable",
        targets=targets,
        indicator_codes=["m"],
        fetch_metrics=_stable_v2_fetch,
        max_workers=4,
        hard_limit=4,
        anomaly_directory=tmp_path,
        failure_directory=tmp_path,
    )

    assert result["structure_changed"] is False
    assert result["validation"]["matched_node_count"] == 5
    manager = next(
        row
        for row in result["validated_rows"]
        if row["node_type"] == "CHANNEL_MANAGER"
    )
    assert manager["m"] == 4
    assert manager["node_id"] == manager_id


def test_v2_new_manager_uses_affected_grid_second_pass(v2_mysql_engine, tmp_path):
    engine, _ = v2_mysql_engine
    run_store = MySQLV2CollectionRunStore(engine)
    run_store.create("v2-drift", "MANUAL", run_type="REALTIME")
    targets = load_v2_collection_targets(engine)
    requested: list[tuple[str, str]] = []

    def fetch(target):
        requested.append((target.target_type, target.target_code))
        return _drift_v2_fetch(target)

    result = collect_validate_metric_rows_v2(
        engine=engine,
        run_store=run_store,
        batch_no="v2-drift",
        targets=targets,
        indicator_codes=["m"],
        fetch_metrics=fetch,
        max_workers=4,
        hard_limit=4,
        anomaly_directory=tmp_path,
        failure_directory=tmp_path,
        retry_strategy="affected_grid",
    )

    assert result["structure_changed"] is True
    assert result["attempts"] == 2
    assert result["validation"]["matched_node_count"] == 7
    assert requested.count(("GRID", "G1")) == 2
    assert requested.count(("CHANNEL_MANAGER", "M2")) == 1
    assert ("CHANNEL_MANAGER", "M2") in result["candidate_graph"].areas
    assert ("CHANNEL", "C2") in result["candidate_graph"].areas


def test_v2_active_parent_constraint_is_enforced(v2_mysql_engine):
    engine, _ = v2_mysql_engine
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO hierarchy_node
                    (node_type, node_code, node_name, parent_id, level_no,
                     request_enabled, metric_enabled)
                SELECT 'BRANCH', 'B2', '分局2', city.id, 2, 1, 1
                FROM hierarchy_node city
                WHERE city.node_type = 'CITY' AND city.node_code = 'A'
                """
            )
        )
        branch_1_id = connection.scalar(
            text(
                "SELECT id FROM hierarchy_node "
                "WHERE node_type='BRANCH' AND node_code='B1'"
            )
        )
        branch_2_id = connection.scalar(
            text(
                "SELECT id FROM hierarchy_node "
                "WHERE node_type='BRANCH' AND node_code='B2'"
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO hierarchy_node
                    (node_type, node_code, node_name, parent_id, level_no,
                     request_enabled, metric_enabled)
                VALUES ('GRID', 'G2', '网格2', :parent_id, 3, 1, 1)
                """
            ),
            {"parent_id": branch_1_id},
        )
        grid_2_id = connection.scalar(
            text(
                "SELECT id FROM hierarchy_node "
                "WHERE node_type='GRID' AND node_code='G2'"
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO hierarchy_parent_history
                    (child_node_id, parent_node_id, valid_from, change_type)
                VALUES (:child_id, :parent_id, NOW(3), 'CREATED')
                """
            ),
            {"child_id": grid_2_id, "parent_id": branch_1_id},
        )
        with pytest.raises(IntegrityError):
            connection.execute(
                text(
                    """
                    INSERT INTO hierarchy_parent_history
                        (child_node_id, parent_node_id, valid_from, change_type)
                    VALUES (:child_id, :parent_id, NOW(3), 'MOVED')
                    """
                ),
                {"child_id": grid_2_id, "parent_id": branch_2_id},
            )


def test_v2_partition_maintenance_creates_future_window_idempotently(
    v2_mysql_engine,
):
    engine, _ = v2_mysql_engine
    with Session(engine) as session:
        first = maintain_v2_snapshot_partitions(
            session,
            now=datetime(2026, 6, 30, 12),
            snapshot_retention_days=7,
            partition_ahead_days=30,
        )
        session.commit()
    with Session(engine) as session:
        second = maintain_v2_snapshot_partitions(
            session,
            now=datetime(2026, 6, 30, 12),
            snapshot_retention_days=7,
            partition_ahead_days=30,
        )
        session.commit()
        partitions = set(session.scalars(text("""
            SELECT PARTITION_NAME
            FROM information_schema.PARTITIONS
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME = 'metric_snapshot'
              AND PARTITION_NAME IS NOT NULL
        """)))

    assert first["partition_create_count"] == 38
    assert second["partition_create_count"] == 0
    assert {"p20260623", "p20260730", "p_future"} <= partitions
    assert check_dashboard_v2_schema(now=datetime(2026, 6, 30, 12))["ok"] is True


def test_v2_readiness_rejects_missing_critical_index(v2_mysql_engine):
    engine, _ = v2_mysql_engine
    index_name = "ix_metric_current_stat_date_indicator"
    with engine.begin() as connection:
        connection.exec_driver_sql(
            f"DROP INDEX `{index_name}` ON `metric_current`"
        )
    try:
        result = check_dashboard_v2_schema(now=datetime(2026, 6, 30, 12))
        assert result["ok"] is False
        assert f"metric_current.{index_name}" in result["missing_indexes"]
    finally:
        with engine.begin() as connection:
            connection.exec_driver_sql(
                f"CREATE INDEX `{index_name}` "
                "ON `metric_current` (`stat_date`, `indicator_id`)"
            )


def test_v2_baseline_can_downgrade_and_upgrade(v2_mysql_engine):
    engine, config = v2_mysql_engine

    command.downgrade(config, "base")
    with engine.connect() as connection:
        assert set(inspect(connection).get_table_names()) == {"alembic_version"}

    command.upgrade(config, "head")
    with engine.connect() as connection:
        assert set(inspect(connection).get_table_names()) == EXPECTED_MYSQL_TABLES


def _payload(*rows):
    return {"reCode": "0000", "result": {"tableData": list(rows)}}


def _stable_v2_fetch(target):
    if target.target_type == "CITY":
        return _payload({"areaCode": "A", "areaName": "城市", "m": 1})
    if target.target_type == "BRANCH":
        return _payload({"areaCode": "B1", "areaName": "分局1", "m": 2})
    if target.target_type == "GRID":
        return _payload(
            {"areaCode": "G1", "areaName": "网格1", "m": 3},
            {"areaCode": "M1", "areaName": "经理1", "m": 4},
        )
    if target.target_code == "M1":
        return _payload(
            {"areaCode": "M1", "areaName": "经理1", "m": 4},
            {"areaCode": "C1", "areaName": "渠道1", "m": 5},
        )
    raise AssertionError(f"unexpected target: {target}")


def _drift_v2_fetch(target):
    if target.target_type != "GRID" and target.target_code != "M2":
        return _stable_v2_fetch(target)
    if target.target_type == "GRID":
        return _payload(
            {"areaCode": "G1", "areaName": "网格1", "m": 3},
            {"areaCode": "M1", "areaName": "经理1", "m": 4},
            {"areaCode": "M2", "areaName": "经理2", "m": 6},
        )
    return _payload(
        {"areaCode": "M2", "areaName": "经理2", "m": 6},
        {"areaCode": "C2", "areaName": "渠道2", "m": 7},
    )


EXPECTED_MYSQL_TABLES = {
    "alembic_version",
    "collection_run",
    "hierarchy_node",
    "hierarchy_parent_history",
    "indicator",
    "indicator_formula_component",
    "channel_indicator_exclusion",
    "target_plan",
    "metric_target_value",
    "metric_current",
    "metric_snapshot",
    "metric_acc",
    "metric_caliber_override",
}
