from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from infrastructure.dashboard_mysql import DashboardMySQLSettings
from models.dashboard_v2 import (  # noqa: F401
    ChannelIndicatorExclusion,
    CollectionRunV2,
    HierarchyNode,
    HierarchyParentHistory,
    IndicatorFormulaComponent,
    IndicatorV2,
    MetricAccV2,
    MetricCaliberOverride,
    MetricCurrentV2,
    MetricSnapshotV2,
    MetricTargetValue,
    TargetPlan,
)
from models.dashboard_v2_base import DashboardV2Base
from models.monitor import MonitorEvent, MonitorRun, MonitorStep  # noqa: F401


config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

settings = DashboardMySQLSettings.from_env()
config.set_main_option(
    "sqlalchemy.url",
    settings.sqlalchemy_url().render_as_string(hide_password=False).replace("%", "%%"),
)
target_metadata = DashboardV2Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        connect_args={
            "connect_timeout": settings.connect_timeout_seconds,
            "read_timeout": settings.io_timeout_seconds,
            "write_timeout": settings.io_timeout_seconds,
        },
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
