from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4


PROJECT_ROOT = Path(__file__).resolve().parents[2]
LOG_PATH = PROJECT_ROOT / "runtime" / "logs" / "web_runs.jsonl"
MAX_LOGS = 200


def append_log(status: str, title: str, message: str, details: str | None = None) -> dict[str, Any]:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    log = {
        "id": f"log_{uuid4().hex[:12]}",
        "status": status,
        "title": title,
        "message": message,
        "createdAt": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "details": details or "",
    }
    with LOG_PATH.open("a", encoding="utf-8") as file:
        file.write(json.dumps(log, ensure_ascii=False) + "\n")
    return log


def list_logs(limit: int = 100) -> list[dict[str, Any]]:
    if not LOG_PATH.exists():
        return []
    logs: list[dict[str, Any]] = []
    for line in LOG_PATH.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            logs.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return list(reversed(logs[-min(limit, MAX_LOGS):]))
