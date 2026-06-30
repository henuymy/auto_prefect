"""Collection-run persistence for the Dashboard V2 schema."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import Engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from infrastructure.dashboard_mysql import create_dashboard_engine
from models.dashboard_v2 import CollectionRunV2


def serialize_v2_collection_run(record: CollectionRunV2) -> dict[str, Any]:
    return {
        "id": record.id,
        "batch_no": record.batch_no,
        "run_type": record.run_type,
        "trigger_type": record.trigger_type,
        "prefect_flow_run_id": record.prefect_flow_run_id,
        "status": record.status,
        "phase": record.phase,
        "session_status": record.session_status,
        "stat_date": _serialize(record.stat_date),
        "started_at": _serialize(record.started_at),
        "finished_at": _serialize(record.finished_at),
        "request_count": record.request_count,
        "node_count": record.node_count,
        "row_count": record.row_count,
        "current_upsert_count": record.current_upsert_count,
        "snapshot_insert_count": record.snapshot_insert_count,
        "acc_upsert_count": record.acc_upsert_count,
        "structure_change_summary": record.structure_change_summary,
        "error_type": record.error_type,
        "error_message": record.error_message,
        "created_at": _serialize(record.created_at),
        "updated_at": _serialize(record.updated_at),
    }


def _serialize(value: date | datetime | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat(timespec="milliseconds")
    return value.isoformat()


def _database_datetime(value: Any) -> Any:
    if isinstance(value, datetime) and value.tzinfo is not None:
        return value.replace(tzinfo=None)
    return value


class MySQLV2CollectionRunStore:
    """Use one short transaction for every externally visible run update."""

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
        record = CollectionRunV2(
            batch_no=batch_no,
            trigger_type=trigger_type,
            run_type=run_type,
            status="PENDING",
            phase="TRIGGER",
            started_at=datetime.now(),
        )
        with self.session_factory() as session:
            session.add(record)
            try:
                session.commit()
            except IntegrityError as exc:
                session.rollback()
                raise ValueError(f"驾驶舱 V2 批次号已存在: {batch_no}") from exc
            session.refresh(record)
            return serialize_v2_collection_run(record)

    def update(self, batch_no: str, **changes: Any) -> dict[str, Any]:
        columns = {column.name for column in CollectionRunV2.__table__.columns}
        protected = {"id", "batch_no", "created_at"}
        invalid = (set(changes) - columns) | (set(changes) & protected)
        if invalid:
            raise ValueError(f"V2 批次字段不允许更新: {sorted(invalid)}")
        with self.session_factory() as session:
            record = session.scalar(
                select(CollectionRunV2).where(CollectionRunV2.batch_no == batch_no)
            )
            if record is None:
                raise FileNotFoundError(f"驾驶舱 V2 批次不存在: {batch_no}")
            for name, value in changes.items():
                setattr(record, name, _database_datetime(value))
            session.commit()
            session.refresh(record)
            return serialize_v2_collection_run(record)

    def get(self, batch_no: str) -> dict[str, Any]:
        with self.session_factory() as session:
            record = session.scalar(
                select(CollectionRunV2).where(CollectionRunV2.batch_no == batch_no)
            )
            if record is None:
                raise FileNotFoundError(f"驾驶舱 V2 批次不存在: {batch_no}")
            return serialize_v2_collection_run(record)

    def latest(self) -> dict[str, Any] | None:
        with self.session_factory() as session:
            record = session.scalar(
                select(CollectionRunV2)
                .order_by(CollectionRunV2.created_at.desc(), CollectionRunV2.id.desc())
                .limit(1)
            )
            return serialize_v2_collection_run(record) if record else None

    def close(self) -> None:
        if self._owns_engine:
            self.engine.dispose()
