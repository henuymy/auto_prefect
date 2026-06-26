"""Dashboard indicator definition model."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Index, String, text
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import Mapped, mapped_column

from models.dashboard_base import DashboardBase


class Indicator(DashboardBase):
    __tablename__ = "indicator"
    __table_args__ = (
        Index("ix_indicator_enabled_sort_order", "enabled", "sort_order"),
        Index(
            "ix_indicator_source_active_sort_order",
            "source_active",
            "sort_order",
        ),
    )

    id: Mapped[int] = mapped_column(
        mysql.BIGINT(unsigned=True),
        primary_key=True,
        autoincrement=True,
    )
    code: Mapped[str] = mapped_column(
        String(100, collation="utf8mb4_bin"),
        nullable=False,
        unique=True,
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    enabled: Mapped[bool] = mapped_column(
        mysql.BOOLEAN,
        nullable=False,
        server_default=text("1"),
    )
    source_active: Mapped[bool] = mapped_column(
        mysql.BOOLEAN,
        nullable=False,
        server_default=text("1"),
    )
    indicator_type: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        server_default=text("'SOURCE'"),
    )
    storage_mode: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        server_default=text("'STORE'"),
    )
    removed_at: Mapped[datetime | None] = mapped_column(
        mysql.DATETIME(fsp=3),
        nullable=True,
    )
    sort_order: Mapped[int] = mapped_column(
        mysql.INTEGER(unsigned=True),
        nullable=False,
        server_default=text("0"),
    )
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
