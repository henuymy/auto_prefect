from __future__ import annotations

import re
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse

from backend.services.run_log_store import append_log


PROJECT_ROOT = Path(__file__).resolve().parents[2]
TEMPLATES_DIR = PROJECT_ROOT / "templates"
ALLOWED_SUFFIXES = {".xlsx", ".xlsm", ".xls"}

router = APIRouter(prefix="/api/templates", tags=["templates"])


def _safe_filename(filename: str) -> str:
    name = Path(filename).name.strip()
    cleaned = re.sub(r'[\\/:*?"<>|]+', "_", name)
    return cleaned or "template.xlsx"


def _template_path(filename: str) -> Path:
    safe_name = _safe_filename(filename)
    path = (TEMPLATES_DIR / safe_name).resolve()
    templates_root = TEMPLATES_DIR.resolve()
    if templates_root not in path.parents:
        raise HTTPException(status_code=400, detail="模板文件路径不合法")
    if path.suffix.lower() not in ALLOWED_SUFFIXES:
        raise HTTPException(status_code=400, detail="只允许管理 Excel 模板文件")
    return path


@router.get("")
def list_templates():
    TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)
    templates = []
    for path in sorted(TEMPLATES_DIR.iterdir(), key=lambda item: item.stat().st_mtime, reverse=True):
        if not path.is_file() or path.suffix.lower() not in ALLOWED_SUFFIXES:
            continue
        templates.append(
            {
                "filename": path.name,
                "path": path.relative_to(PROJECT_ROOT).as_posix(),
                "size": path.stat().st_size,
                "modifiedAt": path.stat().st_mtime,
            }
        )
    return templates


@router.get("/download")
def download_template(filename: str = Query(..., min_length=1)):
    target = _template_path(filename)
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail=f"模板文件不存在: {filename}")
    return FileResponse(
        target,
        filename=target.name,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@router.post("/upload")
async def upload_template(file: UploadFile = File(...)):
    filename = _safe_filename(file.filename or "")
    suffix = Path(filename).suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise HTTPException(status_code=400, detail="只支持上传 .xlsx、.xlsm 或 .xls 模板文件")

    TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)
    target = TEMPLATES_DIR / filename
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="上传文件为空")

    target.write_bytes(content)
    relative_path = target.relative_to(PROJECT_ROOT).as_posix()
    append_log("success", "上传模板", f"已上传 {relative_path}")
    return {
        "filename": filename,
        "path": relative_path,
        "size": target.stat().st_size,
    }


@router.delete("")
def delete_template(filename: str = Query(..., min_length=1)):
    target = _template_path(filename)
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail=f"模板文件不存在: {filename}")

    relative_path = target.relative_to(PROJECT_ROOT).as_posix()
    target.unlink()
    append_log("success", "删除模板", f"已删除 {relative_path}")
    return {"ok": True, "path": relative_path}
