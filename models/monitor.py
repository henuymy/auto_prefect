"""MySQL-owned, normalized monitor-center records.

These models intentionally share the existing MySQL migration pipeline while
remaining independent from both dashboard business tables and Prefect's
PostgreSQL metadata schema.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import Mapped, mapped_column

from models.dashboard_v2_base import DashboardV2Base


DATETIME_MS = mysql.DATETIME(fsp=3).with_variant(DateTime(), "sqlite")


class MonitorRun(DashboardV2Base):
    __tablename__ = "monitor_runs"
    __table_args__ = (
        UniqueConstraint("source", "external_run_id", name="uq_monitor_runs_source_external"),
        Index("ix_monitor_runs_status_scheduled", "status", "scheduled_at"),
        Index("ix_monitor_runs_target_status", "target_kind", "status", "scheduled_at"),
        Index("ix_monitor_runs_task_started", "task_name", "started_at"),
    )

    id: Mapped[str] = mapped_column(String(48), primary_key=True)
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    external_run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    task_name: Mapped[str] = mapped_column(String(255), nullable=False)
    target_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    target_id: Mapped[str] = mapped_column(String(48), nullable=False)
    trigger: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    scheduled_at: Mapped[datetime | None] = mapped_column(DATETIME_MS)
    started_at: Mapped[datetime | None] = mapped_column(DATETIME_MS)
    finished_at: Mapped[datetime | None] = mapped_column(DATETIME_MS)
    state_occurred_at: Mapped[datetime | None] = mapped_column(DATETIME_MS)
    current_step: Mapped[str] = mapped_column(String(128), nullable=False, server_default=text("'尚未开始'"))
    business_error_summary: Mapped[str | None] = mapped_column(Text)
    technical_error_summary: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DATETIME_MS, nullable=False, server_default=text("CURRENT_TIMESTAMP(3)"))
    updated_at: Mapped[datetime] = mapped_column(DATETIME_MS, nullable=False, server_default=text("CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3)"))


class MonitorStep(DashboardV2Base):
    __tablename__ = "monitor_steps"
    __table_args__ = (UniqueConstraint("run_id", "name", name="uq_monitor_steps_run_name"),)

    id: Mapped[int] = mapped_column(mysql.BIGINT(unsigned=True), primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(48), ForeignKey("monitor_runs.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    started_at: Mapped[datetime | None] = mapped_column(DATETIME_MS)
    finished_at: Mapped[datetime | None] = mapped_column(DATETIME_MS)


class MonitorEvent(DashboardV2Base):
    __tablename__ = "monitor_events"
    __table_args__ = (Index("ix_monitor_events_run_sequence", "run_id", "stream_sequence"),)

    id: Mapped[int] = mapped_column(mysql.BIGINT(unsigned=True), primary_key=True, autoincrement=True)
    source_event_id: Mapped[str | None] = mapped_column(String(64), unique=True)
    stream_sequence: Mapped[int] = mapped_column(mysql.BIGINT(unsigned=True), nullable=False, unique=True)
    run_id: Mapped[str] = mapped_column(String(48), ForeignKey("monitor_runs.id", ondelete="CASCADE"), nullable=False)
    at: Mapped[datetime] = mapped_column(DATETIME_MS, nullable=False)
    level: Mapped[str] = mapped_column(String(8), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    details: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
