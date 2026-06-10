"""Dashboard collection run model."""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    CheckConstraint,
    Date,
    Index,
    String,
    Text,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects import mysql

from models.dashboard_base import DashboardBase


class CollectionRun(DashboardBase):
    __tablename__ = "collection_run"
    __table_args__ = (
        CheckConstraint(
            "trigger_type IN ('MANUAL', 'SCHEDULED')",
            name="valid_trigger_type",
        ),
        CheckConstraint(
            "status IN ('PENDING', 'RUNNING', 'SUCCESS', 'FAILED')",
            name="valid_status",
        ),
        Index("ix_collection_run_status_started_at", "status", "started_at"),
        Index("ix_collection_run_stat_date_status", "stat_date", "status"),
    )

    id: Mapped[int] = mapped_column(
        mysql.BIGINT(unsigned=True),
        primary_key=True,
        autoincrement=True,
    )
    batch_no: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    trigger_type: Mapped[str] = mapped_column(String(16), nullable=False)
    prefect_flow_run_id: Mapped[str | None] = mapped_column(String(36), unique=True)
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        server_default=text("'PENDING'"),
    )
    phase: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        server_default=text("'TRIGGER'"),
    )
    session_status: Mapped[str | None] = mapped_column(String(32))
    stat_date: Mapped[date | None] = mapped_column(Date)
    started_at: Mapped[datetime | None] = mapped_column(mysql.DATETIME(fsp=3))
    finished_at: Mapped[datetime | None] = mapped_column(mysql.DATETIME(fsp=3))
    request_count: Mapped[int] = mapped_column(
        mysql.INTEGER(unsigned=True),
        nullable=False,
        server_default=text("0"),
    )
    area_count: Mapped[int] = mapped_column(
        mysql.INTEGER(unsigned=True),
        nullable=False,
        server_default=text("0"),
    )
    row_count: Mapped[int] = mapped_column(
        mysql.INTEGER(unsigned=True),
        nullable=False,
        server_default=text("0"),
    )
    current_upsert_count: Mapped[int] = mapped_column(
        mysql.INTEGER(unsigned=True),
        nullable=False,
        server_default=text("0"),
    )
    snapshot_insert_count: Mapped[int] = mapped_column(
        mysql.INTEGER(unsigned=True),
        nullable=False,
        server_default=text("0"),
    )
    error_type: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        mysql.DATETIME(fsp=3),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP(3)"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        mysql.DATETIME(fsp=3),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP(3)"),
        server_onupdate=text("CURRENT_TIMESTAMP(3)"),
    )
