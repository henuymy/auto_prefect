from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter

from backend.services import prefect_runner


router = APIRouter(prefix="/api/status", tags=["status"])


@router.get("")
def get_status():
    return {
        "backend": {
            "ok": True,
            "message": "FastAPI 后端正常",
            "checked_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        },
        "prefect": prefect_runner.check_prefect_status(),
    }
