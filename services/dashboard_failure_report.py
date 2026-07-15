from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any


DEFAULT_FAILURE_DIRECTORY = "modules/dashboard/output/failure_reports"


def write_dashboard_failure_report(
    directory: str | Path,
    batch_no: str,
    *,
    phase: str,
    error_type: str,
    message: str,
    details: dict[str, Any] | None = None,
    now_provider=datetime.now,
) -> Path:
    target_dir = Path(directory).resolve()
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / f"{batch_no}.json"
    payload = {
        "batch_no": batch_no,
        "generated_at": now_provider().isoformat(timespec="milliseconds"),
        "phase": phase,
        "error_type": error_type,
        "message": str(message),
        "details": details or {},
    }
    temporary_path = path.with_suffix(".json.tmp")
    temporary_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary_path.replace(path)
    return path
