"""Dashboard platform request target model."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, UniqueConstraint, text
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import Mapped, mapped_column, relationship

from models.dashboard_base import DashboardBase


class RequestTarget(DashboardBase):
    __tablename__ = "request_target"
    __table_args__ = (
        CheckConstraint(
            "target_type IN ('CITY', 'BRANCH', 'GRID', 'CHANNEL_MANAGER')",
            name="valid_target_type",
        ),
        UniqueConstraint(
            "target_type",
            "target_code",
            name="uq_request_target_type_code",
        ),
        Index(
            "ix_request_target_enabled_type_order",
            "enabled",
            "target_type",
            "sort_order",
        ),
        Index("ix_request_target_parent_id", "parent_target_id"),
    )

    id: Mapped[int] = mapped_column(
        mysql.BIGINT(unsigned=True),
        primary_key=True,
        autoincrement=True,
    )
    target_code: Mapped[str] = mapped_column(
        String(100, collation="utf8mb4_bin"),
        nullable=False,
    )
    target_name: Mapped[str] = mapped_column(String(200), nullable=False)
    target_type: Mapped[str] = mapped_column(String(20), nullable=False)
    area_id: Mapped[int | None] = mapped_column(
        mysql.BIGINT(unsigned=True),
        ForeignKey("area.id", ondelete="RESTRICT"),
    )
    parent_target_id: Mapped[int | None] = mapped_column(
        mysql.BIGINT(unsigned=True),
        ForeignKey("request_target.id", ondelete="RESTRICT"),
    )
    enabled: Mapped[bool] = mapped_column(
        mysql.BOOLEAN,
        nullable=False,
        server_default=text("1"),
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

    parent: Mapped[RequestTarget | None] = relationship(
        "RequestTarget",
        remote_side="RequestTarget.id",
        back_populates="children",
    )
    children: Mapped[list[RequestTarget]] = relationship(
        "RequestTarget",
        back_populates="parent",
    )
