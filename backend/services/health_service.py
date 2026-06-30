from __future__ import annotations

import os
import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

from backend.services import prefect_runner
from infrastructure.dashboard_mysql import check_dashboard_mysql


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MIN_FREE_BYTES = 100 * 1024 * 1024


def check_runtime_storage() -> dict[str, Any]:
    runtime_dir = PROJECT_ROOT / "runtime"
    probe_dir = runtime_dir / "health"
    minimum_free = int(
        os.environ.get("HEALTH_MIN_FREE_BYTES", str(DEFAULT_MIN_FREE_BYTES))
    )
    try:
        probe_dir.mkdir(parents=True, exist_ok=True)
        descriptor, probe_name = tempfile.mkstemp(dir=probe_dir, prefix="probe-")
        try:
            with os.fdopen(descriptor, "wb") as probe:
                probe.write(b"ok")
                probe.flush()
                os.fsync(probe.fileno())
        finally:
            Path(probe_name).unlink(missing_ok=True)
        usage = shutil.disk_usage(runtime_dir)
        enough_space = usage.free >= minimum_free
        return {
            "ok": enough_space,
            "writable": True,
            "free_bytes": usage.free,
            "minimum_free_bytes": minimum_free,
            "message": "运行目录可写" if enough_space else "运行目录剩余空间不足",
        }
    except (OSError, ValueError) as exc:
        return {
            "ok": False,
            "writable": False,
            "message": f"运行目录检查失败: {type(exc).__name__}",
        }


def readiness_status() -> dict[str, Any]:
    mysql = check_dashboard_mysql()
    prefect = prefect_runner.check_prefect_status()
    storage = check_runtime_storage()
    ok = all(bool(item.get("ok")) for item in (mysql, prefect, storage))
    return {
        "ok": ok,
        "checked_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "checks": {
            "dashboard_mysql": mysql,
            "prefect": prefect,
            "storage": storage,
        },
    }
