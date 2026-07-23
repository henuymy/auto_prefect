from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from backend.services.health_service import readiness_status
from backend.routers.configs import router as configs_router
from backend.routers.dashboard import router as dashboard_router
from backend.routers.runtime import router as runtime_router
from backend.routers.run_logs import router as run_logs_router
from backend.routers.status import router as status_router
from backend.routers.templates import router as templates_router
from backend.routers.monitor import (
    realtime_status,
    router as monitor_router,
    stream_hub,
)
from infrastructure.dashboard_mysql import dispose_dashboard_engine
from backend.services.monitor_sync_service import MonitorSyncLoop, sync_prefect_monitor
from backend.services.monitor_stream import build_upstream_update


@asynccontextmanager
async def lifespan(_app: FastAPI):
    def publish_synced_runs(runs):
        for run in runs:
            stream_hub.publish_from_thread(run["id"])

    def publish_upstream_status() -> None:
        stream_hub.publish_payload_from_thread(
            build_upstream_update(realtime_status.as_dict())
        )

    def record_reconciled() -> None:
        realtime_status.record_reconciled()
        publish_upstream_status()

    def record_reconciliation_error(error: Exception) -> None:
        realtime_status.record_error("RECONCILIATION_FAILED", error)
        publish_upstream_status()

    sync_loop = MonitorSyncLoop(
        sync=sync_prefect_monitor,
        on_runs_changed=publish_synced_runs,
        on_reconciled=record_reconciled,
        on_error=record_reconciliation_error,
    )
    stream_hub.bind_loop(asyncio.get_running_loop())
    sync_loop.start()
    try:
        yield
    finally:
        sync_loop.stop()
        dispose_dashboard_engine()


app = FastAPI(title="自动化任务配置中心 API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:5173",
        "http://localhost:5173",
        "http://127.0.0.1:5174",
        "http://localhost:5174",
        "http://127.0.0.1:5175",
        "http://localhost:5175",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(configs_router)
app.include_router(dashboard_router)
app.include_router(runtime_router)
app.include_router(run_logs_router)
app.include_router(status_router)
app.include_router(templates_router)
app.include_router(monitor_router)


@app.get("/api/live")
def live():
    """Process liveness only; dependency failures do not affect this endpoint."""
    return {"ok": True, "monitorEvents": realtime_status.as_dict()}


@app.get("/api/health")
def health():
    """Readiness check for MySQL, Prefect and writable runtime storage."""
    result = readiness_status()
    return JSONResponse(status_code=200 if result["ok"] else 503, content=result)
