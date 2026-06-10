from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter

from backend.services import prefect_runner
from infrastructure.dashboard_mysql import check_dashboard_mysql
from infrastructure.dashboard_run_store import get_collection_run_status


router = APIRouter(prefix="/api/status", tags=["status"])


@router.get("")
def get_status():
    dashboard_mysql = check_dashboard_mysql()
    if dashboard_mysql.get("ok"):
        dashboard_mysql["collection_run"] = get_collection_run_status()
    return {
        "backend": {
            "ok": True,
            "message": "FastAPI 后端正常",
            "checked_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        },
        "prefect": prefect_runner.check_prefect_status(),
        "dashboard_mysql": dashboard_mysql,
    }
