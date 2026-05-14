from __future__ import annotations

from fastapi import APIRouter, Query

from backend.services import run_log_store


router = APIRouter(prefix="/api/run-logs", tags=["run-logs"])


@router.get("")
def list_run_logs(limit: int = Query(default=100, ge=1, le=200)):
    return run_log_store.list_logs(limit=limit)
