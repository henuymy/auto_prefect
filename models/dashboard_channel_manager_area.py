"""Dashboard channel-manager to channel relationship model."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import ForeignKey, Index, UniqueConstraint, text
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import Mapped, mapped_column

from models.dashboard_base import DashboardBase


class ChannelManagerArea(DashboardBase):
    __tablename__ = "channel_manager_area"
    __table_args__ = (
        UniqueConstraint(
            "manager_target_id",
            "channel_area_id",
            name="uq_channel_manager_area_manager_channel",
        ),
        Index(
            "ix_channel_manager_area_manager_enabled",
            "manager_target_id",
            "enabled",
        ),
        Index(
            "ix_channel_manager_area_channel_enabled",
            "channel_area_id",
            "enabled",
        ),
        Index("ix_channel_manager_area_grid", "grid_area_id"),
    )

    id: Mapped[int] = mapped_column(
        mysql.BIGINT(unsigned=True),
        primary_key=True,
        autoincrement=True,
    )
    manager_target_id: Mapped[int] = mapped_column(
        mysql.BIGINT(unsigned=True),
        ForeignKey("request_target.id", ondelete="RESTRICT"),
        nullable=False,
    )
    channel_area_id: Mapped[int] = mapped_column(
        mysql.BIGINT(unsigned=True),
        ForeignKey("area.id", ondelete="RESTRICT"),
        nullable=False,
    )
    grid_area_id: Mapped[int | None] = mapped_column(
        mysql.BIGINT(unsigned=True),
        ForeignKey("area.id", ondelete="RESTRICT"),
    )
    enabled: Mapped[bool] = mapped_column(
        mysql.BOOLEAN,
        nullable=False,
        server_default=text("1"),
    )
    last_seen_at: Mapped[datetime | None] = mapped_column(mysql.DATETIME(fsp=3))
    missing_count: Mapped[int] = mapped_column(
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
