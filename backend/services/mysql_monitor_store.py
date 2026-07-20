"""SQLAlchemy persistence adapter for monitor-center MySQL tables."""

from __future__ import annotations

from datetime import datetime, timezone
from threading import Lock
from typing import Any

from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from infrastructure.dashboard_mysql import create_dashboard_engine
from models.monitor import MonitorEvent, MonitorRun, MonitorStep


_EVENT_WRITE_LOCK = Lock()


def _dt(value: str | None) -> datetime | None:
    return _mysql_datetime(datetime.fromisoformat(value)) if value else None


def _mysql_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc).isoformat(timespec="seconds")


def _run_dict(row: MonitorRun) -> dict[str, Any]:
    return {
        "id": row.id, "source": row.source, "external_run_id": row.external_run_id,
        "task_name": row.task_name, "target_kind": row.target_kind, "target_id": row.target_id,
        "trigger": row.trigger, "status": row.status,
        "scheduled_at": _iso(row.scheduled_at), "started_at": _iso(row.started_at),
        "finished_at": _iso(row.finished_at), "state_occurred_at": _iso(row.state_occurred_at),
        "current_step": row.current_step,
        "business_error_summary": row.business_error_summary,
        "technical_error_summary": row.technical_error_summary,
    }


class MySQLMonitorStore:
    def __init__(self, engine: Engine | None = None) -> None:
        self.engine = engine or create_dashboard_engine()
        self._owns_engine = engine is None
        self.session_factory = sessionmaker(bind=self.engine, expire_on_commit=False, class_=Session)

    def create_or_update_run(self, run: dict[str, Any]) -> dict[str, Any]:
        with self.session_factory() as session:
            row = session.get(MonitorRun, run["id"])
            if row is None:
                row = MonitorRun(
                    id=run["id"],
                    source=run["source"],
                    external_run_id=run["external_run_id"],
                    task_name=run["task_name"],
                    target_kind=run["target_kind"],
                    target_id=run["target_id"],
                    trigger=run["trigger"],
                    status=run["status"],
                )
                session.add(row)
            for name in ("source", "external_run_id", "task_name", "target_kind", "target_id", "trigger", "status", "current_step", "business_error_summary", "technical_error_summary"):
                setattr(row, name, run.get(name) or ("尚未开始" if name == "current_step" else getattr(row, name, None)))
            for name in ("scheduled_at", "started_at", "finished_at"):
                setattr(row, name, _dt(run.get(name)))
            if "state_occurred_at" in run:
                row.state_occurred_at = _dt(run.get("state_occurred_at"))
            session.commit()
            session.refresh(row)
            return _run_dict(row)

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self.session_factory() as session:
            row = session.get(MonitorRun, run_id)
            if row is None:
                return None
            payload = _run_dict(row)
            steps = session.scalars(select(MonitorStep).where(MonitorStep.run_id == run_id).order_by(MonitorStep.id)).all()
            events = session.scalars(select(MonitorEvent).where(MonitorEvent.run_id == run_id).order_by(MonitorEvent.stream_sequence)).all()
            payload["steps"] = [{"name": item.name, "status": item.status, "message": item.message, "started_at": _iso(item.started_at), "finished_at": _iso(item.finished_at)} for item in steps]
            payload["logs"] = [{"at": _iso(item.at), "level": item.level, "message": item.message, "details": item.details, "stream_sequence": item.stream_sequence} for item in events]
            return payload

    def find_by_external_id(self, source: str, external_run_id: str) -> dict[str, Any] | None:
        with self.session_factory() as session:
            row = session.scalar(select(MonitorRun).where(MonitorRun.source == source, MonitorRun.external_run_id == external_run_id))
            return _run_dict(row) if row else None

    def append_step(self, run_id: str, step: dict[str, Any]) -> dict[str, Any]:
        with self.session_factory() as session:
            row = session.scalar(select(MonitorStep).where(MonitorStep.run_id == run_id, MonitorStep.name == step["name"]))
            if row is None:
                row = MonitorStep(run_id=run_id, name=step["name"], status=step["status"], message=step.get("message") or "")
                session.add(row)
            row.status, row.message = step["status"], step.get("message") or ""
            row.started_at, row.finished_at = _dt(step.get("started_at")), _dt(step.get("finished_at"))
            session.commit()
            return {**step}

    def append_event(self, run_id: str, event: dict[str, Any]) -> dict[str, Any]:
        with _EVENT_WRITE_LOCK:
            with self.session_factory() as session:
                next_sequence = int(session.scalar(select(func.coalesce(func.max(MonitorEvent.stream_sequence), 0))) or 0) + 1
                row = MonitorEvent(
                    run_id=run_id,
                    source_event_id=event.get("source_event_id"),
                    stream_sequence=next_sequence,
                    at=_dt(event["at"]) or datetime.now(),
                    level=event["level"],
                    message=event["message"],
                    details=event.get("details") or "",
                )
                session.add(row)
                session.commit()
                return {**event, "stream_sequence": next_sequence}

    def apply_prefect_event(
        self,
        *,
        event_id: str,
        occurred_at: datetime,
        normalized_run: dict[str, Any],
        message: str,
    ) -> tuple[dict[str, Any], bool]:
        occurred_at = _mysql_datetime(occurred_at)
        with _EVENT_WRITE_LOCK:
            with self.session_factory() as session:
                duplicate = session.scalar(
                    select(MonitorEvent).where(MonitorEvent.source_event_id == event_id)
                )
                existing = session.scalar(
                    select(MonitorRun)
                    .where(
                        MonitorRun.source == "prefect",
                        MonitorRun.external_run_id == normalized_run["external_run_id"],
                    )
                    .with_for_update()
                )
                if duplicate is not None:
                    return _run_dict(existing) if existing is not None else normalized_run, False
                if (
                    existing is not None
                    and existing.state_occurred_at is not None
                    and occurred_at <= existing.state_occurred_at
                ):
                    session.commit()
                    return _run_dict(existing), False

                row = existing
                if row is None:
                    row = MonitorRun(
                        id=normalized_run["id"],
                        source="prefect",
                        external_run_id=normalized_run["external_run_id"],
                        task_name=normalized_run["task_name"],
                        target_kind=normalized_run["target_kind"],
                        target_id=normalized_run["target_id"],
                        trigger=normalized_run["trigger"],
                        status=normalized_run["status"],
                    )
                    session.add(row)
                for name in (
                    "task_name",
                    "target_kind",
                    "target_id",
                    "trigger",
                    "status",
                    "current_step",
                    "business_error_summary",
                    "technical_error_summary",
                ):
                    setattr(row, name, normalized_run.get(name) or getattr(row, name, None))
                for name in ("scheduled_at", "started_at", "finished_at"):
                    setattr(row, name, _dt(normalized_run.get(name)))
                row.state_occurred_at = occurred_at
                next_sequence = int(
                    session.scalar(select(func.coalesce(func.max(MonitorEvent.stream_sequence), 0)))
                    or 0
                ) + 1
                session.add(MonitorEvent(
                    run_id=row.id,
                    source_event_id=event_id,
                    stream_sequence=next_sequence,
                    at=occurred_at,
                    level="INFO",
                    message=message,
                    details="",
                ))
                session.commit()
                session.refresh(row)
                return _run_dict(row), True

    def list_runs(self) -> list[dict[str, Any]]:
        with self.session_factory() as session:
            rows = session.scalars(select(MonitorRun)).all()
            return [_run_dict(row) for row in rows]

    def close(self) -> None:
        if self._owns_engine:
            self.engine.dispose()
