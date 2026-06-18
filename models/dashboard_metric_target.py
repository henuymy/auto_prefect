"""Dashboard metric target values."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, UniqueConstraint, text
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import Mapped, mapped_column

from models.dashboard_base import DashboardBase


class MetricTarget(DashboardBase):
    __tablename__ = "metric_target"
    __table_args__ = (
        CheckConstraint(
            "period_type IN ('REALTIME', 'DAY_ACC', 'MONTH')",
            name="valid_metric_target_period_type",
        ),
        UniqueConstraint(
            "period_type",
            "area_id",
            "indicator_id",
            name="uq_metric_target_period_area_indicator",
        ),
        Index(
            "ix_metric_target_area_indicator_period_enabled",
            "area_id",
            "indicator_id",
            "period_type",
            "enabled",
        ),
    )

    id: Mapped[int] = mapped_column(
        mysql.BIGINT(unsigned=True),
        primary_key=True,
        autoincrement=True,
    )
    period_type: Mapped[str] = mapped_column(String(16), nullable=False)
    area_id: Mapped[int] = mapped_column(
        mysql.BIGINT(unsigned=True),
        ForeignKey("area.id", ondelete="RESTRICT"),
        nullable=False,
    )
    indicator_id: Mapped[int] = mapped_column(
        mysql.BIGINT(unsigned=True),
        ForeignKey("indicator.id", ondelete="RESTRICT"),
        nullable=False,
    )
    target_value: Mapped[Decimal] = mapped_column(
        mysql.DECIMAL(20, 4),
        nullable=False,
    )
    enabled: Mapped[bool] = mapped_column(
        mysql.BOOLEAN,
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
