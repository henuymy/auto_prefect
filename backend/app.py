from __future__ import annotations

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
from infrastructure.dashboard_mysql import dispose_dashboard_engine


@asynccontextmanager
async def lifespan(_app: FastAPI):
    yield
    dispose_dashboard_engine()


app = FastAPI(title="自动化任务配置中心 API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:5173",
        "http://localhost:5173",
        "http://127.0.0.1:5174",
        "http://localhost:5174",
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


@app.get("/api/live")
def live():
    """Process liveness only; dependency failures do not affect this endpoint."""
    return {"ok": True}


@app.get("/api/health")
def health():
    """Readiness check for MySQL, Prefect and writable runtime storage."""
    result = readiness_status()
    return JSONResponse(status_code=200 if result["ok"] else 503, content=result)
