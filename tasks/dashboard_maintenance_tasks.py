"""Prefect task for Dashboard V2 partition and retention maintenance."""

from __future__ import annotations

from prefect import get_run_logger, task

from infrastructure.dashboard_mysql import create_dashboard_engine, dashboard_mysql_lock
from services.dashboard_v2_trigger import load_dashboard_config
from services.dashboard_v2_retention_service import execute_v2_retention_maintenance


@task(name="dashboard-v2-partition-maintenance")
def run_dashboard_v2_partition_maintenance_task(
    config_path: str = "config/dashboard/session.json",
):
    config, _ = load_dashboard_config(config_path)
    logger = get_run_logger()
    engine = create_dashboard_engine()
    try:
        # Collection and retention both update collection_run.  Use the
        # collection lock when it is configured so maintenance cannot hold
        # row locks while a realtime batch is starting.  Keep the partition
        # lock as a fallback for older configurations.
        lock_name = str(
            config.get("collection_database_lock_name")
            or config.get("partition_database_lock_name")
            or "auto_notify_dashboard_collection"
        )
        lock_wait_seconds = int(
            config.get("collection_database_lock_wait_seconds")
            or config.get("partition_database_lock_wait_seconds", 5)
            or 5
        )
        with dashboard_mysql_lock(
            engine,
            lock_name=lock_name,
            wait_seconds=lock_wait_seconds,
        ):
            result = execute_v2_retention_maintenance(
                engine,
                retention=config.get("retention") or {},
            )
            logger.info("驾驶舱 V2 分区维护完成 stats=%s", result)
            return result
    finally:
        engine.dispose()
