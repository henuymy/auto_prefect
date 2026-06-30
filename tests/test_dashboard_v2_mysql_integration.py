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

from services.dashboard_v2_hierarchy import (
    initialize_base_hierarchy_in_session,
    load_v2_collection_targets,
    validate_bootstrap_manifest,
)


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
    assert revision == "20260630_0001"
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


def test_v2_baseline_can_downgrade_and_upgrade(v2_mysql_engine):
    engine, config = v2_mysql_engine

    command.downgrade(config, "base")
    with engine.connect() as connection:
        assert set(inspect(connection).get_table_names()) == {"alembic_version"}

    command.upgrade(config, "head")
    with engine.connect() as connection:
        assert set(inspect(connection).get_table_names()) == EXPECTED_MYSQL_TABLES


EXPECTED_MYSQL_TABLES = {
    "alembic_version",
    "collection_run",
    "hierarchy_node",
    "hierarchy_parent_history",
    "indicator",
    "indicator_formula_component",
    "target_plan",
    "metric_target_value",
    "metric_current",
    "metric_snapshot",
    "metric_acc",
}
