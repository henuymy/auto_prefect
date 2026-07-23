"""Read-only monitor-center REST and WebSocket endpoints."""

from __future__ import annotations

import os
import secrets
import urllib.parse
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any

from fastapi import APIRouter, Header, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.exc import SQLAlchemyError

from backend.services.monitor_api_schema import (
    to_frontend_pending_queue,
    to_frontend_run,
)
from backend.services.mysql_monitor_store import MySQLMonitorStore
from backend.services.prefect_monitor_event_service import (
    InvalidPrefectMonitorEvent,
    PrefectMonitorEventProcessor,
    ProcessedPrefectEvent,
)
from backend.services.prefect_monitor_detail_service import PrefectMonitorDetailService
from backend.services.prefect_runner import _prefect_api_request
from backend.services.monitor_snapshot_service import (
    build_monitor_snapshot,
    build_run_update,
)
from backend.services.monitor_stream import (
    MonitorRealtimeStatus,
    MonitorStreamHub,
    build_upstream_update,
)


router = APIRouter(prefix="/api/monitor", tags=["monitor"])

REPORT_HISTORY_DAYS = 30
REPORT_TASK_PREFIX = "通报 · "


def _raw_runs() -> list[dict[str, Any]]:
    store = MySQLMonitorStore()
    try:
        return store.list_runs()
    finally:
        store.close()


stream_hub = MonitorStreamHub(
    load_update=lambda run_id: build_run_update(_raw_runs(), run_id, upstream=realtime_status.as_dict()),
)
realtime_status = MonitorRealtimeStatus()
prefect_detail_service = PrefectMonitorDetailService()


def _prefect_resource(path: str) -> dict[str, Any]:
    return _prefect_api_request("GET", path) or {}


def _process_prefect_event(payload: dict[str, Any]) -> ProcessedPrefectEvent:
    store = MySQLMonitorStore()
    try:
        processor = PrefectMonitorEventProcessor(
            store=store,
            fetch_flow_run=lambda run_id: _prefect_resource(
                f"flow_runs/{urllib.parse.quote(run_id, safe='')}"
            ),
            fetch_deployment=lambda deployment_id: _prefect_resource(
                f"deployments/{urllib.parse.quote(deployment_id, safe='')}"
            ),
            fetch_flow=lambda flow_id: _prefect_resource(
                f"flows/{urllib.parse.quote(flow_id, safe='')}"
            ),
        )
        return processor.process(payload)
    finally:
        store.close()


def _filter_runs(
    runs: list[dict[str, Any]], *, source: str | None, status: str | None,
    task: str | None, start_at: str | None, end_at: str | None,
) -> list[dict[str, Any]]:
    def include(run: dict[str, Any]) -> bool:
        reference_time = run.get("started_at") or run.get("scheduled_at") or ""
        if source and run.get("source") != source:
            return False
        if status and run.get("status") != status:
            return False
        if task and task.lower() not in str(run.get("task_name") or "").lower():
            return False
        if start_at and reference_time < start_at:
            return False
        if end_at and reference_time > end_at:
            return False
        return True

    return [run for run in runs if include(run)]


def _as_utc(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def report_history_runs(
    runs: list[dict[str, Any]], *, now: datetime
) -> list[dict[str, Any]]:
    cutoff = now.astimezone(timezone.utc) - timedelta(days=REPORT_HISTORY_DAYS)
    history = []
    for run in runs:
        reference_time = _as_utc(run.get("started_at") or run.get("scheduled_at"))
        if (
            run.get("target_kind") == "report"
            and run.get("status") != "scheduled"
            and reference_time is not None
            and reference_time >= cutoff
        ):
            history.append(run)
    return history


def _snapshot_payload(
    runs: list[dict[str, Any]], *,
    now: datetime | None = None,
    upstream: dict[str, str | None] | None = None,
) -> dict[str, Any]:
    return build_monitor_snapshot(
        runs, now=now, upstream=upstream or realtime_status.as_dict()
    )


def _publish_upstream_status() -> None:
    stream_hub.publish_payload_background(build_upstream_update(realtime_status.as_dict()))


def _database_unavailable(exc: Exception) -> HTTPException:
    return HTTPException(status_code=503, detail="监控数据库暂不可用，请稍后重试。")


@router.get("/snapshot")
def get_snapshot():
    try:
        return _snapshot_payload(_raw_runs())
    except (SQLAlchemyError, OSError, ValueError) as exc:
        raise _database_unavailable(exc) from exc


@router.post("/events/prefect", status_code=202)
async def receive_prefect_event(
    payload: dict[str, Any],
    x_prefect_monitor_secret: Annotated[str | None, Header()] = None,
) -> dict[str, bool]:
    expected = os.environ.get("PREFECT_MONITOR_WEBHOOK_SECRET", "")
    if (
        not expected
        or not x_prefect_monitor_secret
        or not secrets.compare_digest(expected, x_prefect_monitor_secret)
    ):
        raise HTTPException(status_code=401, detail="监控事件认证失败")
    try:
        result = await run_in_threadpool(_process_prefect_event, payload)
    except InvalidPrefectMonitorEvent as exc:
        realtime_status.record_error("INVALID_EVENT", exc)
        _publish_upstream_status()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except (OSError, ValueError) as exc:
        realtime_status.record_error("PROCESSING_FAILED", exc)
        _publish_upstream_status()
        raise _database_unavailable(exc) from exc
    if result.accepted and result.run:
        realtime_status.record_accepted()
        stream_hub.publish_background(result.run["id"])
        _publish_upstream_status()
    return {"accepted": result.accepted, "duplicate": result.duplicate}


@router.get("/summary")
def get_summary():
    try:
        snapshot = _snapshot_payload(_raw_runs())
        return {"summary": snapshot["summary"], "updatedAt": snapshot["updatedAt"]}
    except (SQLAlchemyError, OSError, ValueError) as exc:
        raise _database_unavailable(exc) from exc


@router.get("/runs")
def list_runs(
    source: str | None = Query(default=None, pattern="^(prefect|web)$"),
    status: str | None = Query(default=None),
    task: str | None = Query(default=None, max_length=160),
    start_at: str | None = Query(default=None),
    end_at: str | None = Query(default=None),
):
    try:
        runs = _filter_runs(_raw_runs(), source=source, status=status, task=task, start_at=start_at, end_at=end_at)
        return {"items": [to_frontend_run(run) for run in runs], "total": len(runs)}
    except (SQLAlchemyError, OSError, ValueError) as exc:
        raise _database_unavailable(exc) from exc


@router.get("/scheduled")
def list_scheduled():
    try:
        return to_frontend_pending_queue([
            run for run in _raw_runs() if run.get("target_kind") == "report"
        ])
    except (SQLAlchemyError, OSError, ValueError) as exc:
        raise _database_unavailable(exc) from exc


@router.get("/runs/{run_id}")
def get_run(run_id: str):
    store = MySQLMonitorStore()
    try:
        run = store.get_run(run_id)
    except (SQLAlchemyError, OSError, ValueError) as exc:
        raise _database_unavailable(exc) from exc
    finally:
        store.close()
    if run is None:
        raise HTTPException(status_code=404, detail="运行记录不存在")
    if run.get("source") == "prefect":
        run = prefect_detail_service.get_detail(run)
    return to_frontend_run(run)


@router.websocket("/stream")
async def monitor_stream(websocket: WebSocket):
    """Send one snapshot, then push committed run changes."""
    try:
        snapshot = {"type": "snapshot", **_snapshot_payload(_raw_runs())}
        await stream_hub.connect(websocket, snapshot)
    except (SQLAlchemyError, OSError, ValueError):
        await websocket.accept()
        await websocket.send_json({"type": "connection", "connected": False})
        return
    try:
        while True:
            message = await websocket.receive()
            if message.get("type") == "websocket.disconnect":
                return
    except WebSocketDisconnect:
        return
    finally:
        stream_hub.disconnect(websocket)
