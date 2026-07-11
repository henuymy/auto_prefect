from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter

from backend.services import prefect_runner
from infrastructure.dashboard_mysql import check_dashboard_mysql
from infrastructure.dashboard_v2_run_store import MySQLV2CollectionRunStore


router = APIRouter(prefix="/api/status", tags=["status"])


@router.get("")
def get_status():
    dashboard_mysql = check_dashboard_mysql()
    if dashboard_mysql.get("ok"):
        run_store = None
        try:
            run_store = MySQLV2CollectionRunStore()
            dashboard_mysql["collection_run"] = {
                "schema_ready": True,
                "latest": run_store.latest(),
            }
        except Exception as exc:
            dashboard_mysql["collection_run"] = {
                "schema_ready": False,
                "latest": None,
                "message": f"collection_run 表不可用: {type(exc).__name__}",
            }
        finally:
            if run_store is not None:
                run_store.close()
    return {
        "backend": {
            "ok": True,
            "message": "FastAPI 后端正常",
            "checked_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        },
        "prefect": prefect_runner.check_prefect_status(),
        "dashboard_mysql": dashboard_mysql,
    }
