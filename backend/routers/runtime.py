from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from backend.services import runtime_store
from backend.services.run_log_store import append_log


router = APIRouter(prefix="/api/runtime", tags=["runtime"])


@router.get("")
def list_runtime(path: str = Query(default="")):
    try:
        return runtime_store.list_runtime(path)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("")
def delete_runtime(path: str = Query(...)):
    try:
        runtime_store.delete_runtime(path)
        append_log("success", "清理 runtime", f"已删除 {path}")
        return {"ok": True}
    except (FileNotFoundError, ValueError) as exc:
        append_log("failed", "清理 runtime 失败", str(exc))
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/cleanup-preview")
def cleanup_preview(days: int = Query(default=7), path: str = Query(default="")):
    try:
        return runtime_store.preview_cleanup(days=days, relative_path=path)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/cleanup")
def cleanup_runtime(days: int = Query(default=7), path: str = Query(default="")):
    try:
        result = runtime_store.run_cleanup(days=days, relative_path=path)
        append_log("success", "批量清理 runtime", f"已删除 {len(result.get('deleted') or [])} 项")
        return result
    except (FileNotFoundError, ValueError) as exc:
        append_log("failed", "批量清理 runtime 失败", str(exc))
        raise HTTPException(status_code=400, detail=str(exc)) from exc
