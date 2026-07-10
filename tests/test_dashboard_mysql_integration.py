from __future__ import annotations

import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from infrastructure.dashboard_mysql import dashboard_mysql_lock
from services.dashboard_retention_service import _maintain_mysql_snapshot_partitions


pytestmark = pytest.mark.mysql_integration
PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _mysql_error_code(exc: OperationalError) -> int | None:
    original = exc.orig
    return int(original.args[0]) if getattr(original, "args", None) else None


@pytest.fixture(scope="module")
def mysql_engine():
    raw_url = os.environ.get("DASHBOARD_TEST_MYSQL_URL", "").strip()
    if not raw_url:
        pytest.skip("DASHBOARD_TEST_MYSQL_URL 未配置，跳过真实 MySQL 集成测试")
    url = make_url(raw_url)
    database = str(url.database or "")
    if not re.search(r"(^test|test$|_test$|_ci$)", database, re.IGNORECASE):
        pytest.fail("MySQL 集成测试只允许连接名称以 test 开头或以 _test/_ci 结尾的库")

    engine = create_engine(url, pool_pre_ping=True)
    with engine.begin() as connection:
        connection.exec_driver_sql("SET FOREIGN_KEY_CHECKS=0")
        for table_name in inspect(connection).get_table_names():
            escaped = table_name.replace("`", "``")
            connection.exec_driver_sql(f"DROP TABLE `{escaped}`")
        connection.exec_driver_sql("SET FOREIGN_KEY_CHECKS=1")

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
    os.environ.update({
        "DASHBOARD_MYSQL_HOST": str(url.host or "127.0.0.1"),
        "DASHBOARD_MYSQL_PORT": str(url.port or 3306),
        "DASHBOARD_MYSQL_DATABASE": database,
        "DASHBOARD_MYSQL_USER": str(url.username or "root"),
        "DASHBOARD_MYSQL_PASSWORD": str(url.password or ""),
    })
    try:
        alembic_config = Config(str(PROJECT_ROOT / "alembic_dashboard.ini"))
        command.upgrade(alembic_config, "head")
        yield engine
    finally:
        engine.dispose()
        for name, value in previous_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def test_mysql_migrations_create_partitioned_snapshot(mysql_engine):
    with mysql_engine.connect() as connection:
        revision = connection.scalar(text("SELECT version_num FROM alembic_version"))
        partitions = list(connection.scalars(text("""
            SELECT PARTITION_NAME
            FROM information_schema.PARTITIONS
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME = 'metric_snapshot'
              AND PARTITION_NAME IS NOT NULL
        """)))
        primary_columns = list(connection.scalars(text("""
            SELECT COLUMN_NAME
            FROM information_schema.STATISTICS
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME = 'metric_snapshot'
              AND INDEX_NAME = 'PRIMARY'
            ORDER BY SEQ_IN_INDEX
        """)))

    assert revision == "20260627_0023"
    assert "p_future" in partitions
    assert primary_columns == ["id", "collected_at"]


def test_mysql_partition_maintenance_executes_against_real_server(mysql_engine):
    with Session(mysql_engine) as session:
        result = _maintain_mysql_snapshot_partitions(
            session,
            now=datetime(2026, 7, 15),
            retention_days=7,
        )
        session.commit()

    assert result["partition_create_count"] >= 0


def test_mysql_named_lock_blocks_second_connection(mysql_engine):
    with dashboard_mysql_lock(
        mysql_engine, lock_name="auto_notify_ci_lock"
    ) as lease:
        lease.assert_held()
        assert lease.connection_id is not None
        with mysql_engine.connect() as second:
            acquired = second.scalar(
                text("SELECT GET_LOCK('auto_notify_ci_lock', 0)")
            )
            assert acquired == 0
    with mysql_engine.connect() as second:
        assert second.scalar(text("SELECT GET_LOCK('auto_notify_ci_lock', 0)")) == 1
        assert second.scalar(text("SELECT RELEASE_LOCK('auto_notify_ci_lock')")) == 1


def _prepare_lock_probe(engine) -> None:
    with engine.begin() as connection:
        connection.exec_driver_sql("""
            CREATE TABLE IF NOT EXISTS integration_lock_probe (
                id INT PRIMARY KEY,
                value INT NOT NULL
            ) ENGINE=InnoDB
        """)
        connection.exec_driver_sql(
            "INSERT INTO integration_lock_probe (id, value) VALUES (1, 0), (2, 0) "
            "ON DUPLICATE KEY UPDATE value = 0"
        )


def test_mysql_lock_wait_timeout_is_observable(mysql_engine):
    _prepare_lock_probe(mysql_engine)
    with mysql_engine.connect() as first, mysql_engine.connect() as second:
        first.exec_driver_sql("SET SESSION innodb_lock_wait_timeout = 2")
        second.exec_driver_sql("SET SESSION innodb_lock_wait_timeout = 1")
        first.commit()
        second.commit()
        first.exec_driver_sql(
            "UPDATE integration_lock_probe SET value = value + 1 WHERE id = 1"
        )
        with pytest.raises(OperationalError) as exc_info:
            second.exec_driver_sql(
                "UPDATE integration_lock_probe SET value = value + 1 WHERE id = 1"
            )
        assert _mysql_error_code(exc_info.value) == 1205
        second.rollback()
        first.rollback()


def test_mysql_deadlock_is_detected(mysql_engine):
    _prepare_lock_probe(mysql_engine)
    barrier = threading.Barrier(2)

    def worker(first_id: int, second_id: int) -> int | None:
        with mysql_engine.connect() as connection:
            transaction = connection.begin()
            try:
                connection.exec_driver_sql(
                    "UPDATE integration_lock_probe SET value = value + 1 WHERE id = %s",
                    (first_id,),
                )
                barrier.wait(timeout=5)
                connection.exec_driver_sql(
                    "UPDATE integration_lock_probe SET value = value + 1 WHERE id = %s",
                    (second_id,),
                )
                transaction.commit()
                return None
            except OperationalError as exc:
                transaction.rollback()
                return _mysql_error_code(exc)

    with ThreadPoolExecutor(max_workers=2) as executor:
        codes = list(executor.map(lambda pair: worker(*pair), [(1, 2), (2, 1)]))

    assert 1213 in codes
