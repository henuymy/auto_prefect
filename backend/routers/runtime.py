from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from backend.services import runtime_store


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
        return {"ok": True}
    except (FileNotFoundError, ValueError) as exc:
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
        return runtime_store.run_cleanup(days=days, relative_path=path)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
