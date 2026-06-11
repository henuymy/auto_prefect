"""Current and historical dashboard metric models."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Date, ForeignKey, Index, String, UniqueConstraint, text
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import Mapped, mapped_column

from models.dashboard_base import DashboardBase


class MetricCurrent(DashboardBase):
    __tablename__ = "metric_current"
    __table_args__ = (
        UniqueConstraint(
            "area_id",
            "indicator_id",
            name="uq_metric_current_area_indicator",
        ),
        Index(
            "ix_metric_current_indicator_value",
            "indicator_id",
            "metric_value",
        ),
        Index(
            "ix_metric_current_stat_date_indicator",
            "stat_date",
            "indicator_id",
        ),
    )

    id: Mapped[int] = mapped_column(
        mysql.BIGINT(unsigned=True),
        primary_key=True,
        autoincrement=True,
    )
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
    collection_run_id: Mapped[int] = mapped_column(
        mysql.BIGINT(unsigned=True),
        ForeignKey("collection_run.id", ondelete="RESTRICT"),
        nullable=False,
    )
    metric_value: Mapped[Decimal] = mapped_column(
        mysql.DECIMAL(20, 4),
        nullable=False,
    )
    stat_date: Mapped[date] = mapped_column(Date, nullable=False)
    collected_at: Mapped[datetime] = mapped_column(
        mysql.DATETIME(fsp=3),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        mysql.DATETIME(fsp=3),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP(3)"),
        server_onupdate=text("CURRENT_TIMESTAMP(3)"),
    )


class MetricSnapshot(DashboardBase):
    __tablename__ = "metric_snapshot"
    __table_args__ = (
        UniqueConstraint(
            "collection_run_id",
            "area_id",
            "indicator_id",
            name="uq_metric_snapshot_run_area_indicator",
        ),
        Index(
            "ix_metric_snapshot_area_indicator_collected",
            "area_id",
            "indicator_id",
            "collected_at",
        ),
    )

    id: Mapped[int] = mapped_column(
        mysql.BIGINT(unsigned=True),
        primary_key=True,
        autoincrement=True,
    )
    collection_run_id: Mapped[int] = mapped_column(
        mysql.BIGINT(unsigned=True),
        ForeignKey("collection_run.id", ondelete="RESTRICT"),
        nullable=False,
    )
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
    metric_value: Mapped[Decimal] = mapped_column(
        mysql.DECIMAL(20, 4),
        nullable=False,
    )
    collected_at: Mapped[datetime] = mapped_column(
        mysql.DATETIME(fsp=3),
        nullable=False,
    )


class MetricAcc(DashboardBase):
    __tablename__ = "metric_acc"
    __table_args__ = (
        UniqueConstraint(
            "period_type",
            "stat_date",
            "area_id",
            "indicator_id",
            name="uq_metric_acc_period_date_area_indicator",
        ),
        Index(
            "ix_metric_acc_area_indicator_date",
            "area_id",
            "indicator_id",
            "period_type",
            "stat_date",
        ),
        Index(
            "ix_metric_acc_period_date_indicator_value",
            "period_type",
            "stat_date",
            "indicator_id",
            "metric_value",
        ),
    )

    id: Mapped[int] = mapped_column(
        mysql.BIGINT(unsigned=True),
        primary_key=True,
        autoincrement=True,
    )
    period_type: Mapped[str] = mapped_column(String(16), nullable=False)
    stat_date: Mapped[date] = mapped_column(Date, nullable=False)
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
    collection_run_id: Mapped[int] = mapped_column(
        mysql.BIGINT(unsigned=True),
        ForeignKey("collection_run.id", ondelete="RESTRICT"),
        nullable=False,
    )
    metric_value: Mapped[Decimal] = mapped_column(
        mysql.DECIMAL(20, 4),
        nullable=False,
    )
    collected_at: Mapped[datetime] = mapped_column(
        mysql.DATETIME(fsp=3),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        mysql.DATETIME(fsp=3),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP(3)"),
        server_onupdate=text("CURRENT_TIMESTAMP(3)"),
    )
