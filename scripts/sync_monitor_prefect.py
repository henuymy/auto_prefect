"""Read Prefect flow-run data into the MySQL monitor schema.

Run from the project root after applying the dashboard_v2 migration.  This is
read-only toward Prefect and does not start, retry, pause, or cancel flows.
"""

from __future__ import annotations

from backend.services.monitor_event_service import MonitorEventService
from backend.services.mysql_monitor_store import MySQLMonitorStore
from backend.services.prefect_monitor_adapter import PrefectMonitorAdapter


def main() -> None:
    store = MySQLMonitorStore()
    try:
        adapter = PrefectMonitorAdapter(MonitorEventService(store=store))
        synced = adapter.fetch_and_sync()
    finally:
        store.close()
    print(f"已同步 {len(synced)} 条 Prefect 运行到 MySQL monitor_* 表。")


if __name__ == "__main__":
    main()
