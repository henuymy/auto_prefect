"""Independent MySQL configuration and connectivity for the dashboard."""

from __future__ import annotations

import json
import logging
import os
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from functools import lru_cache
from threading import Event, Lock, Thread
from typing import Any, Mapping

from sqlalchemy import URL, create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError


ENV_PREFIX = "DASHBOARD_MYSQL_"
logger = logging.getLogger(__name__)
REQUIRED_ENV_NAMES = (
    "DASHBOARD_MYSQL_HOST",
    "DASHBOARD_MYSQL_DATABASE",
    "DASHBOARD_MYSQL_USER",
    "DASHBOARD_MYSQL_PASSWORD",
)


class DashboardMySQLConfigError(ValueError):
    pass


class DashboardMySQLLockLostError(RuntimeError):
    """Raised when a collector no longer owns its server-side named lock."""

    error_type = "DASHBOARD_MYSQL_LOCK_LOST"


class DashboardMySQLLockLease:
    """Thread-safe ownership handle for one MySQL named lock."""

    def __init__(
        self,
        *,
        connection: Any,
        lock_name: str,
        backend: str,
        connection_id: int | None = None,
    ) -> None:
        self._connection = connection
        self.name = lock_name
        self.backend = backend
        self.connection_id = connection_id
        self._connection_guard = Lock()
        self._lost = Event()
        self._loss_type: str | None = None

    def mark_lost(self, reason: object) -> None:
        self._loss_type = (
            reason if isinstance(reason, str) else type(reason).__name__
        )
        self._lost.set()

    def assert_held(self) -> None:
        if self.backend not in {"mysql", "mariadb"}:
            return
        if self._lost.is_set():
            raise DashboardMySQLLockLostError(
                f"MySQL 驾驶舱采集锁已丢失: {self.name} "
                f"({self._loss_type or 'UNKNOWN'})"
            )
        try:
            with self._connection_guard:
                owner = self._connection.scalar(
                    text("SELECT IS_USED_LOCK(:lock_name)"),
                    {"lock_name": self.name},
                )
        except SQLAlchemyError as exc:
            self.mark_lost(exc)
            raise DashboardMySQLLockLostError(
                f"MySQL 驾驶舱采集锁校验失败: {self.name} "
                f"({type(exc).__name__})"
            ) from exc
        if owner != self.connection_id:
            self.mark_lost("OWNER_MISMATCH")
            raise DashboardMySQLLockLostError(
                f"MySQL 驾驶舱采集锁所有权已丢失: {self.name}"
            )

    def as_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "backend": self.backend,
            "connection_id": self.connection_id,
        }


@dataclass(frozen=True)
class DashboardMySQLSettings:
    host: str
    port: int
    database: str
    user: str
    password: str
    connect_timeout_seconds: int = 5
    io_timeout_seconds: int = 60
    pool_recycle_seconds: int = 1800
    pool_size: int = 5
    max_overflow: int = 5
    charset: str = "utf8mb4"

    @classmethod
    def from_env(
        cls,
        environ: Mapping[str, str] | None = None,
    ) -> "DashboardMySQLSettings":
        values = environ if environ is not None else os.environ
        missing = [name for name in REQUIRED_ENV_NAMES if not values.get(name, "").strip()]
        if missing:
            raise DashboardMySQLConfigError(
                f"缺少驾驶舱 MySQL 环境变量: {', '.join(missing)}"
            )
        return cls(
            host=values["DASHBOARD_MYSQL_HOST"].strip(),
            port=_positive_int(values.get("DASHBOARD_MYSQL_PORT", "3306"), "DASHBOARD_MYSQL_PORT"),
            database=values["DASHBOARD_MYSQL_DATABASE"].strip(),
            user=values["DASHBOARD_MYSQL_USER"].strip(),
            password=values["DASHBOARD_MYSQL_PASSWORD"],
            connect_timeout_seconds=_positive_int(
                values.get("DASHBOARD_MYSQL_CONNECT_TIMEOUT_SECONDS", "5"),
                "DASHBOARD_MYSQL_CONNECT_TIMEOUT_SECONDS",
            ),
            io_timeout_seconds=_positive_int(
                values.get("DASHBOARD_MYSQL_IO_TIMEOUT_SECONDS", "60"),
                "DASHBOARD_MYSQL_IO_TIMEOUT_SECONDS",
            ),
            pool_recycle_seconds=_positive_int(
                values.get("DASHBOARD_MYSQL_POOL_RECYCLE_SECONDS", "1800"),
                "DASHBOARD_MYSQL_POOL_RECYCLE_SECONDS",
            ),
            pool_size=_positive_int(
                values.get("DASHBOARD_MYSQL_POOL_SIZE", "5"),
                "DASHBOARD_MYSQL_POOL_SIZE",
            ),
            max_overflow=_non_negative_int(
                values.get("DASHBOARD_MYSQL_MAX_OVERFLOW", "5"),
                "DASHBOARD_MYSQL_MAX_OVERFLOW",
            ),
            charset=(values.get("DASHBOARD_MYSQL_CHARSET") or "utf8mb4").strip(),
        )

    def sqlalchemy_url(self) -> URL:
        return URL.create(
            drivername="mysql+pymysql",
            username=self.user,
            password=self.password,
            host=self.host,
            port=self.port,
            database=self.database,
            query={"charset": self.charset},
        )

    def public_summary(self) -> dict[str, object]:
        return {
            "host": self.host,
            "port": self.port,
            "database": self.database,
            "user": self.user,
            "driver": "mysql+pymysql",
            "charset": self.charset,
        }


def _positive_int(value: object, env_name: str) -> int:
    try:
        parsed = int(str(value))
    except (TypeError, ValueError) as exc:
        raise DashboardMySQLConfigError(f"{env_name} 必须是整数") from exc
    if parsed <= 0:
        raise DashboardMySQLConfigError(f"{env_name} 必须大于 0")
    return parsed


def _non_negative_int(value: object, env_name: str) -> int:
    try:
        parsed = int(str(value))
    except (TypeError, ValueError) as exc:
        raise DashboardMySQLConfigError(f"{env_name} 必须是整数") from exc
    if parsed < 0:
        raise DashboardMySQLConfigError(f"{env_name} 不能小于 0")
    return parsed


def create_dashboard_engine(
    settings: DashboardMySQLSettings | None = None,
) -> Engine:
    resolved = settings or DashboardMySQLSettings.from_env()
    return create_engine(
        resolved.sqlalchemy_url(),
        pool_pre_ping=True,
        pool_recycle=resolved.pool_recycle_seconds,
        pool_size=resolved.pool_size,
        max_overflow=resolved.max_overflow,
        connect_args={
            "connect_timeout": resolved.connect_timeout_seconds,
            "read_timeout": resolved.io_timeout_seconds,
            "write_timeout": resolved.io_timeout_seconds,
        },
    )


@contextmanager
def dashboard_mysql_lock(
    engine: Engine,
    *,
    lock_name: str = "auto_notify_dashboard_collection",
    wait_seconds: int = 5,
    heartbeat_seconds: int = 30,
    idle_timeout_seconds: int = 300,
):
    """Hold a server-wide MySQL named lock on one leased connection.

    The heartbeat prevents network or database idle-timeout layers from
    silently abandoning a live lock connection.  If the process disappears,
    the session-level idle timeout makes MySQL close the orphaned connection
    and release its named lock automatically.
    """
    normalized_name = str(lock_name or "").strip()
    if not normalized_name or len(normalized_name) > 64:
        raise ValueError("MySQL 锁名称长度必须为 1-64 个字符")
    wait_seconds = max(0, int(wait_seconds))
    heartbeat_seconds = max(0, int(heartbeat_seconds))
    idle_timeout_seconds = max(0, int(idle_timeout_seconds))
    if (
        heartbeat_seconds > 0
        and idle_timeout_seconds > 0
        and heartbeat_seconds >= idle_timeout_seconds
    ):
        raise ValueError("MySQL 锁心跳间隔必须小于空闲断开时间")
    if engine.dialect.name not in {"mysql", "mariadb"}:
        yield DashboardMySQLLockLease(
            connection=None,
            lock_name=normalized_name,
            backend=engine.dialect.name,
        )
        return

    with engine.connect() as connection:
        if idle_timeout_seconds > 0:
            connection.exec_driver_sql(
                f"SET SESSION wait_timeout = {idle_timeout_seconds}"
            )
        acquired = connection.scalar(
            text("SELECT GET_LOCK(:lock_name, :wait_seconds)"),
            {"lock_name": normalized_name, "wait_seconds": wait_seconds},
        )
        if acquired != 1:
            raise TimeoutError(
                f"等待 MySQL 驾驶舱采集锁超时: {normalized_name}"
            )
        connection_id = connection.scalar(text("SELECT CONNECTION_ID()"))
        if connection_id is None:
            raise RuntimeError("MySQL 未返回命名锁连接 ID")
        lease = DashboardMySQLLockLease(
            connection=connection,
            lock_name=normalized_name,
            backend=engine.dialect.name,
            connection_id=int(connection_id),
        )
        heartbeat_stop = Event()
        heartbeat_thread: Thread | None = None
        if heartbeat_seconds > 0:
            def keep_lock_connection_alive() -> None:
                while not heartbeat_stop.wait(heartbeat_seconds):
                    try:
                        lease.assert_held()
                    except DashboardMySQLLockLostError as exc:
                        logger.warning(
                            "MySQL 驾驶舱采集锁心跳失败: %s (%s)",
                            normalized_name,
                            type(exc).__name__,
                        )
                        return

            heartbeat_thread = Thread(
                target=keep_lock_connection_alive,
                name=f"mysql-lock-heartbeat-{normalized_name}",
                daemon=True,
            )
            heartbeat_thread.start()
        try:
            yield lease
        finally:
            heartbeat_stop.set()
            if heartbeat_thread is not None:
                heartbeat_thread.join(timeout=max(1, heartbeat_seconds + 1))
            try:
                with lease._connection_guard:
                    released = connection.scalar(
                        text("SELECT RELEASE_LOCK(:lock_name)"),
                        {"lock_name": normalized_name},
                    )
                if released != 1:
                    logger.warning(
                        "MySQL 驾驶舱采集锁释放结果异常: %s", normalized_name
                    )
            except SQLAlchemyError as exc:
                # A dead dedicated connection is already subject to the
                # session idle timeout.  Do not turn completed business work
                # into a failed batch merely because release acknowledgement
                # was lost.
                logger.warning(
                    "MySQL 驾驶舱采集锁释放失败，等待服务端自动清理: %s (%s)",
                    normalized_name,
                    type(exc).__name__,
                )


@lru_cache(maxsize=1)
def get_dashboard_engine() -> Engine:
    """Return the process-wide dashboard connection pool."""
    return create_dashboard_engine()


def dispose_dashboard_engine() -> None:
    """Dispose and clear the shared dashboard connection pool."""
    if get_dashboard_engine.cache_info().currsize:
        get_dashboard_engine().dispose()
        get_dashboard_engine.cache_clear()


def check_dashboard_mysql(
    environ: Mapping[str, str] | None = None,
) -> dict[str, object]:
    try:
        settings = DashboardMySQLSettings.from_env(environ)
    except DashboardMySQLConfigError as exc:
        return {
            "ok": False,
            "configured": False,
            "message": str(exc),
        }

    engine = create_dashboard_engine(settings)
    try:
        with engine.connect() as connection:
            row = connection.execute(
                text(
                    "SELECT 1 AS connection_ok, "
                    "DATABASE() AS database_name, "
                    "VERSION() AS server_version, "
                    "CURRENT_USER() AS authenticated_user, "
                    "@@character_set_database AS database_charset, "
                    "@@session.time_zone AS session_time_zone, "
                    "TIMEDIFF(NOW(), UTC_TIMESTAMP()) AS utc_offset"
                )
            ).mappings().one()
        database_matches = row["database_name"] == settings.database
        authenticated_user = str(
            row.get("authenticated_user") or f"{settings.user}@unknown"
        )
        account_name = authenticated_user.split("@", 1)[0].lower()
        non_root_account = settings.user.lower() != "root" and account_name != "root"
        version_text = str(row["server_version"] or "")
        try:
            major_version = int(version_text.split(".", 1)[0])
        except ValueError:
            major_version = 0
        supported_version = major_version >= 8 and "mariadb" not in version_text.lower()
        database_charset = str(row.get("database_charset") or settings.charset)
        charset_matches = database_charset.lower() == "utf8mb4"
        utc_offset = str(row.get("utc_offset") or "08:00:00")
        timezone_matches = utc_offset in {"8:00:00", "08:00:00"}
        ok = all(
            (
                bool(row["connection_ok"]),
                database_matches,
                non_root_account,
                supported_version,
                charset_matches,
                timezone_matches,
            )
        )
        problems = []
        if not database_matches:
            problems.append("连接库名与配置不一致")
        if not non_root_account:
            problems.append("禁止使用 root 账号")
        if not supported_version:
            problems.append("需要 MySQL 8.0 以上")
        if not charset_matches:
            problems.append("数据库字符集必须为 utf8mb4")
        if not timezone_matches:
            problems.append("数据库会话时区不是东八区")
        return {
            "ok": ok,
            "configured": True,
            "message": (
                "驾驶舱 MySQL 连接与环境校验正常"
                if ok else "；".join(problems)
            ),
            "database_name": row["database_name"],
            "server_version": row["server_version"],
            "authenticated_user": authenticated_user,
            "database_charset": database_charset,
            "session_time_zone": str(row.get("session_time_zone") or ""),
            "utc_offset": utc_offset,
            **settings.public_summary(),
        }
    except SQLAlchemyError as exc:
        return {
            "ok": False,
            "configured": True,
            "message": f"驾驶舱 MySQL 连接失败: {type(exc).__name__}",
            **settings.public_summary(),
        }
    finally:
        engine.dispose()


def main() -> int:
    result = check_dashboard_mysql()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
