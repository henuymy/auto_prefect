"""Dashboard organization area model."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, UniqueConstraint, text
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import Mapped, mapped_column, relationship

from models.dashboard_base import DashboardBase


class Area(DashboardBase):
    __tablename__ = "area"
    __table_args__ = (
        CheckConstraint(
            "level_type IN ('CITY', 'BRANCH', 'GRID', 'CHANNEL')",
            name="valid_level_type",
        ),
        UniqueConstraint(
            "level_type",
            "area_code",
            name="uq_area_level_type_area_code",
        ),
        Index("ix_area_parent_id_enabled", "parent_id", "enabled"),
        Index("ix_area_level_type_enabled", "level_type", "enabled"),
    )

    id: Mapped[int] = mapped_column(
        mysql.BIGINT(unsigned=True),
        primary_key=True,
        autoincrement=True,
    )
    area_code: Mapped[str] = mapped_column(
        String(100, collation="utf8mb4_bin"),
        nullable=False,
    )
    area_name: Mapped[str] = mapped_column(String(200), nullable=False)
    level_type: Mapped[str] = mapped_column(String(20), nullable=False)
    level_no: Mapped[int] = mapped_column(
        mysql.TINYINT(unsigned=True),
        nullable=False,
    )
    parent_id: Mapped[int | None] = mapped_column(
        mysql.BIGINT(unsigned=True),
        ForeignKey("area.id", ondelete="RESTRICT"),
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

    parent: Mapped[Area | None] = relationship(
        "Area",
        remote_side="Area.id",
        back_populates="children",
    )
    children: Mapped[list[Area]] = relationship(
        "Area",
        back_populates="parent",
    )
