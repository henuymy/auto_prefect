from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from services.runtime_paths import runtime_root

RUNTIME_DIR = runtime_root()
PROTECTED_TOP_LEVEL = {
    "config",
    "health",
    "logs",
    "prefect",
    "processes",
    "session",
    "starter_templates",
}


def _resolve_runtime_path(relative_path: str | None = None) -> Path:
    target = (RUNTIME_DIR / (relative_path or "")).resolve()
    root = RUNTIME_DIR.resolve()
    if target != root and root not in target.parents:
        raise ValueError("只能访问 runtime 目录内的文件")
    return target


def _entry(path: Path) -> dict[str, Any]:
    stat = path.stat()
    return {
        "name": path.name,
        "path": str(path.relative_to(RUNTIME_DIR)),
        "type": "directory" if path.is_dir() else "file",
        "size": stat.st_size if path.is_file() else None,
        "modifiedAt": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
    }


def _dir_size(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    total = 0
    for item in path.rglob("*"):
        if item.is_file():
            total += item.stat().st_size
    return total


def _is_protected(relative_path: str) -> bool:
    top_level = Path(relative_path).parts[0] if Path(relative_path).parts else ""
    return top_level in PROTECTED_TOP_LEVEL


def list_runtime(relative_path: str | None = None) -> dict[str, Any]:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    target = _resolve_runtime_path(relative_path)
    if not target.exists():
        raise FileNotFoundError(f"runtime 路径不存在: {relative_path or ''}")
    if target.is_file():
        return {"current": _entry(target), "items": []}
    items = sorted(target.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower()))
    return {"current": _entry(target), "items": [_entry(item) for item in items]}


def delete_runtime(relative_path: str) -> None:
    if not relative_path:
        raise ValueError("不允许删除 runtime 根目录")
    if _is_protected(relative_path):
        top_level = Path(relative_path).parts[0]
        raise ValueError(f"runtime/{top_level} 是受保护目录，不允许从页面删除")
    target = _resolve_runtime_path(relative_path)
    if not target.exists():
        raise FileNotFoundError(f"runtime 路径不存在: {relative_path}")
    if target.is_dir():
        shutil.rmtree(target)
    else:
        target.unlink()


def preview_cleanup(days: int = 7, relative_path: str | None = None) -> dict[str, Any]:
    if days < 0:
        raise ValueError("days 不能小于 0")
    root = _resolve_runtime_path(relative_path)
    if not root.exists():
        raise FileNotFoundError(f"runtime 路径不存在: {relative_path or ''}")
    cutoff = datetime.now().timestamp() - days * 24 * 60 * 60
    candidates = []
    for item in root.iterdir() if root.is_dir() else [root]:
        rel = str(item.relative_to(RUNTIME_DIR))
        if _is_protected(rel):
            continue
        if item.stat().st_mtime <= cutoff:
            candidates.append(
                {
                    **_entry(item),
                    "size": _dir_size(item),
                }
            )
    return {
        "days": days,
        "path": str(root.relative_to(RUNTIME_DIR)) if root != RUNTIME_DIR else "",
        "count": len(candidates),
        "totalSize": sum(int(item.get("size") or 0) for item in candidates),
        "items": candidates,
    }


def run_cleanup(days: int = 7, relative_path: str | None = None) -> dict[str, Any]:
    preview = preview_cleanup(days=days, relative_path=relative_path)
    deleted = []
    for item in preview["items"]:
        delete_runtime(item["path"])
        deleted.append(item)
    return {
        **preview,
        "deleted": deleted,
    }
