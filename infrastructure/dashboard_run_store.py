"""Persistence adapters for dashboard collection runs."""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

from sqlalchemy import Engine, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from infrastructure.dashboard_mysql import create_dashboard_engine
from models.dashboard_collection_run import CollectionRun


class CollectionRunStore(Protocol):
    def create(
        self,
        batch_no: str,
        trigger_type: str,
        run_type: str = "REALTIME",
    ) -> dict[str, Any]: ...

    def update(self, batch_no: str, **changes: Any) -> dict[str, Any]: ...

    def get(self, batch_no: str) -> dict[str, Any]: ...

    def latest(self) -> dict[str, Any] | None: ...

    def close(self) -> None: ...


def serialize_collection_run(record: CollectionRun) -> dict[str, Any]:
    return {
        "id": record.id,
        "batch_no": record.batch_no,
        "run_type": record.run_type,
        "trigger_type": record.trigger_type,
        "prefect_flow_run_id": record.prefect_flow_run_id,
        "status": record.status,
        "phase": record.phase,
        "session_status": record.session_status,
        "stat_date": _serialize_value(record.stat_date),
        "started_at": _serialize_value(record.started_at),
        "finished_at": _serialize_value(record.finished_at),
        "request_count": record.request_count,
        "area_count": record.area_count,
        "row_count": record.row_count,
        "current_upsert_count": record.current_upsert_count,
        "snapshot_insert_count": record.snapshot_insert_count,
        "acc_upsert_count": record.acc_upsert_count,
        "error_type": record.error_type,
        "error_message": record.error_message,
        "created_at": _serialize_value(record.created_at),
        "updated_at": _serialize_value(record.updated_at),
    }


def _serialize_value(value: date | datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat(timespec="milliseconds") if isinstance(value, datetime) else value.isoformat()


def _database_datetime(value: Any) -> Any:
    if isinstance(value, datetime) and value.tzinfo is not None:
        return value.replace(tzinfo=None)
    return value


class MySQLCollectionRunStore:
    def __init__(self, engine: Engine | None = None):
        self.engine = engine or create_dashboard_engine()
        self._owns_engine = engine is None
        self.session_factory = sessionmaker(
            bind=self.engine,
            expire_on_commit=False,
            class_=Session,
        )

    def create(
        self,
        batch_no: str,
        trigger_type: str,
        run_type: str = "REALTIME",
    ) -> dict[str, Any]:
        record = CollectionRun(
            batch_no=batch_no,
            trigger_type=trigger_type,
            run_type=run_type,
            status="PENDING",
            phase="TRIGGER",
        )
        with self.session_factory() as session:
            session.add(record)
            try:
                session.commit()
            except IntegrityError as exc:
                session.rollback()
                raise ValueError(f"驾驶舱批次号已存在: {batch_no}") from exc
            session.refresh(record)
            return serialize_collection_run(record)

    def update(self, batch_no: str, **changes: Any) -> dict[str, Any]:
        allowed_columns = {column.name for column in CollectionRun.__table__.columns}
        protected = {"id", "batch_no", "created_at"}
        invalid = set(changes) - allowed_columns | (set(changes) & protected)
        if invalid:
            raise ValueError(f"不允许更新 collection_run 字段: {', '.join(sorted(invalid))}")
        with self.session_factory() as session:
            record = session.scalar(
                select(CollectionRun).where(CollectionRun.batch_no == batch_no)
            )
            if record is None:
                raise FileNotFoundError(f"驾驶舱批次不存在: {batch_no}")
            for name, value in changes.items():
                setattr(record, name, _database_datetime(value))
            record.updated_at = datetime.now()
            session.commit()
            session.refresh(record)
            return serialize_collection_run(record)

    def get(self, batch_no: str) -> dict[str, Any]:
        with self.session_factory() as session:
            record = session.scalar(
                select(CollectionRun).where(CollectionRun.batch_no == batch_no)
            )
            if record is None:
                raise FileNotFoundError(f"驾驶舱批次不存在: {batch_no}")
            return serialize_collection_run(record)

    def latest(self) -> dict[str, Any] | None:
        with self.session_factory() as session:
            record = session.scalar(
                select(CollectionRun)
                .order_by(CollectionRun.created_at.desc(), CollectionRun.id.desc())
                .limit(1)
            )
            return serialize_collection_run(record) if record else None

    def close(self) -> None:
        if self._owns_engine:
            self.engine.dispose()


class JsonCollectionRunStore:
    """Runtime JSON adapter retained for isolated tests."""

    def __init__(self, directory: str | Path, now_provider):
        self.directory = Path(directory).resolve()
        self.now_provider = now_provider

    def path_for(self, batch_no: str) -> Path:
        return self.directory / f"{batch_no}.json"

    def create(
        self,
        batch_no: str,
        trigger_type: str,
        run_type: str = "REALTIME",
    ) -> dict[str, Any]:
        created_at = self.now_provider().isoformat(timespec="milliseconds")
        record = {
            "id": None,
            "batch_no": batch_no,
            "run_type": run_type,
            "trigger_type": trigger_type,
            "prefect_flow_run_id": None,
            "status": "PENDING",
            "phase": "TRIGGER",
            "session_status": None,
            "stat_date": None,
            "started_at": None,
            "finished_at": None,
            "request_count": 0,
            "area_count": 0,
            "row_count": 0,
            "current_upsert_count": 0,
            "snapshot_insert_count": 0,
            "acc_upsert_count": 0,
            "error_type": None,
            "error_message": None,
            "created_at": created_at,
            "updated_at": created_at,
        }
        self._write(self.path_for(batch_no), record)
        return record

    def update(self, batch_no: str, **changes: Any) -> dict[str, Any]:
        path = self.path_for(batch_no)
        if not path.exists():
            raise FileNotFoundError(f"驾驶舱批次不存在: {batch_no}")
        record = json.loads(path.read_text(encoding="utf-8"))
        record.update(
            {
                key: _serialize_value(value) if isinstance(value, (date, datetime)) else value
                for key, value in changes.items()
            }
        )
        record["updated_at"] = self.now_provider().isoformat(timespec="milliseconds")
        self._write(path, record)
        return record

    def get(self, batch_no: str) -> dict[str, Any]:
        path = self.path_for(batch_no)
        if not path.exists():
            raise FileNotFoundError(f"驾驶舱批次不存在: {batch_no}")
        return json.loads(path.read_text(encoding="utf-8"))

    def latest(self) -> dict[str, Any] | None:
        paths = sorted(
            self.directory.glob("*.json"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        ) if self.directory.exists() else []
        return json.loads(paths[0].read_text(encoding="utf-8")) if paths else None

    def close(self) -> None:
        return None

    @staticmethod
    def _write(path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = path.with_suffix(f"{path.suffix}.{uuid4().hex}.tmp")
        temporary_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary_path.replace(path)


def get_collection_run_status() -> dict[str, Any]:
    store: MySQLCollectionRunStore | None = None
    try:
        store = MySQLCollectionRunStore()
        return {
            "schema_ready": True,
            "latest": store.latest(),
        }
    except SQLAlchemyError as exc:
        return {
            "schema_ready": False,
            "latest": None,
            "message": f"collection_run 表不可用: {type(exc).__name__}",
        }
    finally:
        if store is not None:
            store.close()
