from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.routers.configs import router as configs_router
from backend.routers.dashboard import router as dashboard_router
from backend.routers.runtime import router as runtime_router
from backend.routers.run_logs import router as run_logs_router
from backend.routers.status import router as status_router
from backend.routers.templates import router as templates_router


app = FastAPI(title="自动化任务配置中心 API")

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


@app.get("/api/health")
def health():
    return {"ok": True}
