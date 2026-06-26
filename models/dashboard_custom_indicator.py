"""Dashboard custom indicator composition model."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import ForeignKey, Index, UniqueConstraint, text
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import Mapped, mapped_column

from models.dashboard_base import DashboardBase


class CustomIndicatorComponent(DashboardBase):
    __tablename__ = "custom_indicator_component"
    __table_args__ = (
        UniqueConstraint(
            "custom_indicator_id",
            "source_indicator_id",
            name="uq_custom_indicator_component_pair",
        ),
        Index(
            "ix_custom_indicator_component_source",
            "source_indicator_id",
        ),
    )

    id: Mapped[int] = mapped_column(
        mysql.BIGINT(unsigned=True),
        primary_key=True,
        autoincrement=True,
    )
    custom_indicator_id: Mapped[int] = mapped_column(
        mysql.BIGINT(unsigned=True),
        ForeignKey("indicator.id", ondelete="CASCADE"),
        nullable=False,
    )
    source_indicator_id: Mapped[int] = mapped_column(
        mysql.BIGINT(unsigned=True),
        ForeignKey("indicator.id", ondelete="RESTRICT"),
        nullable=False,
    )
    coefficient: Mapped[Decimal] = mapped_column(
        mysql.DECIMAL(20, 4),
        nullable=False,
        server_default=text("1"),
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
