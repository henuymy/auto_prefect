"""Independent MySQL configuration and connectivity for the dashboard."""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from typing import Mapping

from sqlalchemy import URL, create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError


ENV_PREFIX = "DASHBOARD_MYSQL_"
REQUIRED_ENV_NAMES = (
    "DASHBOARD_MYSQL_HOST",
    "DASHBOARD_MYSQL_DATABASE",
    "DASHBOARD_MYSQL_USER",
    "DASHBOARD_MYSQL_PASSWORD",
)


class DashboardMySQLConfigError(ValueError):
    pass


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
                    "VERSION() AS server_version"
                )
            ).mappings().one()
        return {
            "ok": bool(row["connection_ok"]),
            "configured": True,
            "message": "驾驶舱 MySQL 连接正常",
            "database_name": row["database_name"],
            "server_version": row["server_version"],
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
