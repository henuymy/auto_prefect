"""Independent Prefect flow for Dashboard V2 partition maintenance."""

from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from prefect import flow, get_run_logger

from tasks.dashboard_maintenance_tasks import (
    run_dashboard_v2_partition_maintenance_task,
)


@flow(name="dashboard-v2-partition-maintenance-flow")
def dashboard_v2_partition_maintenance_flow(
    config_path: str = "config/dashboard/session.json",
):
    logger = get_run_logger()
    logger.info("启动驾驶舱 V2 独立分区与保留维护")
    return run_dashboard_v2_partition_maintenance_task(config_path=config_path)


if __name__ == "__main__":
    dashboard_v2_partition_maintenance_flow()
