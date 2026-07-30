"""Dashboard V2 unified hierarchy and metric models."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    Computed,
    Date,
    DateTime,
    ForeignKey,
    Index,
    PrimaryKeyConstraint,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import Mapped, mapped_column

from models.dashboard_v2_base import DashboardV2Base


DATETIME_MS = mysql.DATETIME(fsp=3).with_variant(DateTime(), "sqlite")


def _created_at_column() -> Mapped[datetime]:
    return mapped_column(
        DATETIME_MS,
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP(3)"),
    )


def _updated_at_column() -> Mapped[datetime]:
    return mapped_column(
        DATETIME_MS,
        nullable=False,
        server_default=text(
            "CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3)"
        ),
        server_onupdate=text("CURRENT_TIMESTAMP(3)"),
    )


class CollectionRunV2(DashboardV2Base):
    __tablename__ = "collection_run"
    __table_args__ = (
        CheckConstraint(
            "run_type IN ('REALTIME','DAY_ACC','MONTH','INDICATOR_SYNC')",
            name="valid_run_type",
        ),
        CheckConstraint(
            "trigger_type IN ('SCHEDULED','MANUAL')",
            name="valid_trigger_type",
        ),
        CheckConstraint(
            "status IN ('PENDING','RUNNING','SUCCESS','FAILED')",
            name="valid_status",
        ),
        UniqueConstraint("batch_no", name="uq_collection_run_batch_no"),
        UniqueConstraint(
            "prefect_flow_run_id",
            name="uq_collection_run_prefect_flow_run_id",
        ),
        Index("ix_collection_run_status_started", "status", "started_at"),
        Index("ix_collection_run_stat_date_status", "stat_date", "status"),
    )

    id: Mapped[int] = mapped_column(
        mysql.BIGINT(unsigned=True), primary_key=True, autoincrement=True
    )
    batch_no: Mapped[str] = mapped_column(String(64), nullable=False)
    run_type: Mapped[str] = mapped_column(String(20), nullable=False)
    trigger_type: Mapped[str] = mapped_column(String(16), nullable=False)
    prefect_flow_run_id: Mapped[str | None] = mapped_column(String(36))
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    phase: Mapped[str] = mapped_column(String(32), nullable=False)
    session_status: Mapped[str | None] = mapped_column(String(32))
    stat_date: Mapped[date | None] = mapped_column(Date)
    started_at: Mapped[datetime] = mapped_column(
        DATETIME_MS, nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(DATETIME_MS)
    request_count: Mapped[int] = mapped_column(
        mysql.INTEGER(unsigned=True), nullable=False, server_default=text("0")
    )
    node_count: Mapped[int] = mapped_column(
        mysql.INTEGER(unsigned=True), nullable=False, server_default=text("0")
    )
    row_count: Mapped[int] = mapped_column(
        mysql.INTEGER(unsigned=True), nullable=False, server_default=text("0")
    )
    current_upsert_count: Mapped[int] = mapped_column(
        mysql.INTEGER(unsigned=True), nullable=False, server_default=text("0")
    )
    snapshot_insert_count: Mapped[int] = mapped_column(
        mysql.INTEGER(unsigned=True), nullable=False, server_default=text("0")
    )
    acc_upsert_count: Mapped[int] = mapped_column(
        mysql.INTEGER(unsigned=True), nullable=False, server_default=text("0")
    )
    structure_change_summary: Mapped[dict | None] = mapped_column(mysql.JSON)
    error_type: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[dict | None] = mapped_column(mysql.JSON)
    created_at: Mapped[datetime] = _created_at_column()
    updated_at: Mapped[datetime] = _updated_at_column()


class HierarchyNode(DashboardV2Base):
    __tablename__ = "hierarchy_node"
    __table_args__ = (
        CheckConstraint(
            "node_type IN "
            "('CITY','BRANCH','GRID','CHANNEL_MANAGER','CHANNEL')",
            name="valid_node_type",
        ),
        CheckConstraint("level_no BETWEEN 1 AND 5", name="valid_level_no"),
        UniqueConstraint(
            "node_type", "node_code", name="uq_hierarchy_node_type_code"
        ),
        Index(
            "ix_hierarchy_node_parent_enabled_order",
            "parent_id",
            "enabled",
            "sort_order",
        ),
        Index(
            "ix_hierarchy_node_type_enabled_order",
            "node_type",
            "enabled",
            "sort_order",
        ),
        Index(
            "ix_hierarchy_node_request_enabled", "request_enabled", "enabled"
        ),
        Index("ix_hierarchy_node_metric_enabled", "metric_enabled", "enabled"),
    )

    id: Mapped[int] = mapped_column(
        mysql.BIGINT(unsigned=True), primary_key=True, autoincrement=True
    )
    node_type: Mapped[str] = mapped_column(String(32), nullable=False)
    node_code: Mapped[str] = mapped_column(
        String(100, collation="utf8mb4_bin"), nullable=False
    )
    node_name: Mapped[str] = mapped_column(String(200), nullable=False)
    parent_id: Mapped[int | None] = mapped_column(
        mysql.BIGINT(unsigned=True),
        ForeignKey("hierarchy_node.id", ondelete="RESTRICT"),
    )
    level_no: Mapped[int] = mapped_column(
        mysql.TINYINT(unsigned=True), nullable=False
    )
    request_enabled: Mapped[bool] = mapped_column(
        mysql.BOOLEAN, nullable=False, server_default=text("0")
    )
    metric_enabled: Mapped[bool] = mapped_column(
        mysql.BOOLEAN, nullable=False, server_default=text("1")
    )
    enabled: Mapped[bool] = mapped_column(
        mysql.BOOLEAN, nullable=False, server_default=text("1")
    )
    sort_order: Mapped[int] = mapped_column(
        mysql.INTEGER(unsigned=True), nullable=False, server_default=text("0")
    )
    last_seen_at: Mapped[datetime | None] = mapped_column(DATETIME_MS)
    missing_count: Mapped[int] = mapped_column(
        mysql.INTEGER(unsigned=True), nullable=False, server_default=text("0")
    )
    created_at: Mapped[datetime] = _created_at_column()
    updated_at: Mapped[datetime] = _updated_at_column()


class HierarchyParentHistory(DashboardV2Base):
    __tablename__ = "hierarchy_parent_history"
    __table_args__ = (
        CheckConstraint(
            "change_type IN ('CREATED','MOVED','RESTORED')",
            name="valid_change_type",
        ),
        CheckConstraint(
            "valid_to IS NULL OR valid_to >= valid_from",
            name="valid_history_dates",
        ),
        UniqueConstraint(
            "active_child_node_id", name="uq_hierarchy_history_active_child"
        ),
        Index(
            "ix_hierarchy_history_child_validity",
            "child_node_id",
            "valid_from",
            "valid_to",
        ),
        Index(
            "ix_hierarchy_history_parent_validity",
            "parent_node_id",
            "valid_from",
            "valid_to",
        ),
    )

    id: Mapped[int] = mapped_column(
        mysql.BIGINT(unsigned=True), primary_key=True, autoincrement=True
    )
    child_node_id: Mapped[int] = mapped_column(
        mysql.BIGINT(unsigned=True),
        ForeignKey("hierarchy_node.id", ondelete="RESTRICT"),
        nullable=False,
    )
    parent_node_id: Mapped[int] = mapped_column(
        mysql.BIGINT(unsigned=True),
        ForeignKey("hierarchy_node.id", ondelete="RESTRICT"),
        nullable=False,
    )
    valid_from: Mapped[datetime] = mapped_column(
        DATETIME_MS, nullable=False
    )
    valid_to: Mapped[datetime | None] = mapped_column(DATETIME_MS)
    collection_run_id: Mapped[int | None] = mapped_column(
        mysql.BIGINT(unsigned=True),
        ForeignKey("collection_run.id", ondelete="SET NULL"),
    )
    change_type: Mapped[str] = mapped_column(String(32), nullable=False)
    active_child_node_id: Mapped[int | None] = mapped_column(
        mysql.BIGINT(unsigned=True),
        Computed(
            "CASE WHEN valid_to IS NULL THEN child_node_id ELSE NULL END",
            persisted=True,
        ),
    )
    created_at: Mapped[datetime] = _created_at_column()


class IndicatorV2(DashboardV2Base):
    __tablename__ = "indicator"
    __table_args__ = (
        CheckConstraint(
            "indicator_type IN ('SOURCE','CUSTOM')",
            name="valid_indicator_type",
        ),
        CheckConstraint(
            "storage_mode IN ('STORE','COMPONENT')",
            name="valid_storage_mode",
        ),
        UniqueConstraint("code", name="uq_indicator_code"),
        Index("ix_indicator_enabled_sort_order", "enabled", "sort_order"),
        Index(
            "ix_indicator_source_active_sort_order", "source_active", "sort_order"
        ),
    )

    id: Mapped[int] = mapped_column(
        mysql.BIGINT(unsigned=True), primary_key=True, autoincrement=True
    )
    code: Mapped[str] = mapped_column(
        String(100, collation="utf8mb4_bin"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    indicator_type: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'SOURCE'")
    )
    storage_mode: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'STORE'")
    )
    enabled: Mapped[bool] = mapped_column(
        mysql.BOOLEAN, nullable=False, server_default=text("1")
    )
    source_active: Mapped[bool] = mapped_column(
        mysql.BOOLEAN, nullable=False, server_default=text("1")
    )
    sort_order: Mapped[int] = mapped_column(
        mysql.INTEGER(unsigned=True), nullable=False, server_default=text("0")
    )
    removed_at: Mapped[datetime | None] = mapped_column(DATETIME_MS)
    created_at: Mapped[datetime] = _created_at_column()
    updated_at: Mapped[datetime] = _updated_at_column()


class IndicatorFormulaComponent(DashboardV2Base):
    __tablename__ = "indicator_formula_component"
    __table_args__ = (
        UniqueConstraint(
            "custom_indicator_id",
            "source_indicator_id",
            name="uq_indicator_formula_component_pair",
        ),
        Index("ix_indicator_formula_component_source", "source_indicator_id"),
    )

    id: Mapped[int] = mapped_column(
        mysql.BIGINT(unsigned=True), primary_key=True, autoincrement=True
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
        mysql.DECIMAL(20, 4), nullable=False, server_default=text("1")
    )
    sort_order: Mapped[int] = mapped_column(
        mysql.INTEGER(unsigned=True), nullable=False, server_default=text("0")
    )
    created_at: Mapped[datetime] = _created_at_column()
    updated_at: Mapped[datetime] = _updated_at_column()


class TargetPlan(DashboardV2Base):
    __tablename__ = "target_plan"
    __table_args__ = (
        CheckConstraint("scenario IN ('NORMAL','PK')", name="valid_scenario"),
        CheckConstraint(
            "period_type IN ('DAY','MONTH')", name="valid_period_type"
        ),
        CheckConstraint(
            "status IN ('DRAFT','ACTIVE','RETIRED')", name="valid_status"
        ),
        CheckConstraint(
            "effective_to IS NULL OR effective_to >= effective_from",
            name="valid_effective_dates",
        ),
        UniqueConstraint(
            "scenario",
            "period_type",
            "plan_name",
            "status",
            "version_no",
            name="uq_target_plan_status_version",
        ),
        Index(
            "ix_target_plan_lookup",
            "scenario",
            "period_type",
            "status",
            "effective_from",
            "effective_to",
            "priority",
        ),
        Index(
            "ix_target_plan_realtime_lookup",
            "scenario",
            "period_type",
            "status",
            "is_realtime",
            "effective_from",
            "effective_to",
            "priority",
        ),
    )

    id: Mapped[int] = mapped_column(
        mysql.BIGINT(unsigned=True), primary_key=True, autoincrement=True
    )
    plan_name: Mapped[str] = mapped_column(String(200), nullable=False)
    scenario: Mapped[str] = mapped_column(String(16), nullable=False)
    period_type: Mapped[str] = mapped_column(String(16), nullable=False)
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    effective_to: Mapped[date | None] = mapped_column(Date)
    priority: Mapped[int] = mapped_column(
        mysql.INTEGER, nullable=False, server_default=text("0")
    )
    version_no: Mapped[int] = mapped_column(
        mysql.INTEGER(unsigned=True), nullable=False, server_default=text("1")
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'DRAFT'")
    )
    is_realtime: Mapped[bool] = mapped_column(
        mysql.TINYINT(1), nullable=False, server_default=text("0")
    )
    supersedes_plan_id: Mapped[int | None] = mapped_column(
        mysql.BIGINT(unsigned=True),
        ForeignKey("target_plan.id", ondelete="RESTRICT"),
    )
    activated_at: Mapped[datetime | None] = mapped_column(DATETIME_MS)
    retired_at: Mapped[datetime | None] = mapped_column(DATETIME_MS)
    created_at: Mapped[datetime] = _created_at_column()
    updated_at: Mapped[datetime] = _updated_at_column()


class MetricTargetValue(DashboardV2Base):
    __tablename__ = "metric_target_value"
    __table_args__ = (
        UniqueConstraint(
            "plan_id",
            "node_id",
            "indicator_id",
            name="uq_metric_target_value_plan_node_indicator",
        ),
        Index("ix_metric_target_value_node_indicator", "node_id", "indicator_id"),
    )

    id: Mapped[int] = mapped_column(
        mysql.BIGINT(unsigned=True), primary_key=True, autoincrement=True
    )
    plan_id: Mapped[int] = mapped_column(
        mysql.BIGINT(unsigned=True),
        ForeignKey("target_plan.id", ondelete="CASCADE"),
        nullable=False,
    )
    node_id: Mapped[int] = mapped_column(
        mysql.BIGINT(unsigned=True),
        ForeignKey("hierarchy_node.id", ondelete="RESTRICT"),
        nullable=False,
    )
    indicator_id: Mapped[int] = mapped_column(
        mysql.BIGINT(unsigned=True),
        ForeignKey("indicator.id", ondelete="RESTRICT"),
        nullable=False,
    )
    target_value: Mapped[Decimal] = mapped_column(
        mysql.DECIMAL(20, 4), nullable=False
    )
    created_at: Mapped[datetime] = _created_at_column()
    updated_at: Mapped[datetime] = _updated_at_column()


class MetricCurrentV2(DashboardV2Base):
    __tablename__ = "metric_current"
    __table_args__ = (
        UniqueConstraint(
            "node_id", "indicator_id", name="uq_metric_current_node_indicator"
        ),
        Index("ix_metric_current_indicator_value", "indicator_id", "metric_value"),
        Index(
            "ix_metric_current_stat_date_indicator", "stat_date", "indicator_id"
        ),
    )

    id: Mapped[int] = mapped_column(
        mysql.BIGINT(unsigned=True), primary_key=True, autoincrement=True
    )
    node_id: Mapped[int] = mapped_column(
        mysql.BIGINT(unsigned=True),
        ForeignKey("hierarchy_node.id", ondelete="RESTRICT"),
        nullable=False,
    )
    indicator_id: Mapped[int] = mapped_column(
        mysql.BIGINT(unsigned=True),
        ForeignKey("indicator.id", ondelete="RESTRICT"),
        nullable=False,
    )
    collection_run_id: Mapped[int | None] = mapped_column(
        mysql.BIGINT(unsigned=True),
        ForeignKey("collection_run.id", ondelete="SET NULL"),
    )
    metric_value: Mapped[Decimal] = mapped_column(
        mysql.DECIMAL(20, 4), nullable=False
    )
    stat_date: Mapped[date] = mapped_column(Date, nullable=False)
    collected_at: Mapped[datetime] = mapped_column(
        DATETIME_MS, nullable=False
    )
    updated_at: Mapped[datetime] = _updated_at_column()


class MetricSnapshotV2(DashboardV2Base):
    __tablename__ = "metric_snapshot"
    __table_args__ = (
        PrimaryKeyConstraint("id", "collected_at", name="pk_metric_snapshot"),
        UniqueConstraint(
            "collection_run_id",
            "node_id",
            "indicator_id",
            "collected_at",
            name="uq_metric_snapshot_run_node_indicator_collected",
        ),
        Index(
            "ix_metric_snapshot_node_indicator_collected",
            "node_id",
            "indicator_id",
            "collected_at",
        ),
        Index("ix_metric_snapshot_collected_at", "collected_at"),
    )

    id: Mapped[int] = mapped_column(
        mysql.BIGINT(unsigned=True), nullable=False, autoincrement=True
    )
    collected_at: Mapped[datetime] = mapped_column(
        DATETIME_MS, nullable=False
    )
    collection_run_id: Mapped[int] = mapped_column(
        mysql.BIGINT(unsigned=True), nullable=False
    )
    node_id: Mapped[int] = mapped_column(
        mysql.BIGINT(unsigned=True), nullable=False
    )
    indicator_id: Mapped[int] = mapped_column(
        mysql.BIGINT(unsigned=True), nullable=False
    )
    metric_value: Mapped[Decimal] = mapped_column(
        mysql.DECIMAL(20, 4), nullable=False
    )


class MetricAccV2(DashboardV2Base):
    __tablename__ = "metric_acc"
    __table_args__ = (
        CheckConstraint(
            "period_type IN ('DAY_ACC','MONTH')", name="valid_period_type"
        ),
        UniqueConstraint(
            "period_type",
            "stat_date",
            "node_id",
            "indicator_id",
            name="uq_metric_acc_period_date_node_indicator",
        ),
        Index(
            "ix_metric_acc_node_indicator_period_date",
            "node_id",
            "indicator_id",
            "period_type",
            "stat_date",
        ),
        Index(
            "ix_metric_acc_period_date_indicator",
            "period_type",
            "stat_date",
            "indicator_id",
            "metric_value",
        ),
        Index("ix_metric_acc_stat_date", "stat_date"),
    )

    id: Mapped[int] = mapped_column(
        mysql.BIGINT(unsigned=True), primary_key=True, autoincrement=True
    )
    period_type: Mapped[str] = mapped_column(String(16), nullable=False)
    stat_date: Mapped[date] = mapped_column(Date, nullable=False)
    node_id: Mapped[int] = mapped_column(
        mysql.BIGINT(unsigned=True),
        ForeignKey("hierarchy_node.id", ondelete="RESTRICT"),
        nullable=False,
    )
    indicator_id: Mapped[int] = mapped_column(
        mysql.BIGINT(unsigned=True),
        ForeignKey("indicator.id", ondelete="RESTRICT"),
        nullable=False,
    )
    collection_run_id: Mapped[int | None] = mapped_column(
        mysql.BIGINT(unsigned=True),
        ForeignKey("collection_run.id", ondelete="SET NULL"),
    )
    metric_value: Mapped[Decimal] = mapped_column(
        mysql.DECIMAL(20, 4), nullable=False
    )
    collected_at: Mapped[datetime] = mapped_column(
        DATETIME_MS, nullable=False
    )
    updated_at: Mapped[datetime] = _updated_at_column()


__all__ = [
    "CollectionRunV2",
    "HierarchyNode",
    "HierarchyParentHistory",
    "IndicatorFormulaComponent",
    "IndicatorV2",
    "MetricAccV2",
    "MetricCurrentV2",
    "MetricSnapshotV2",
    "MetricTargetValue",
    "TargetPlan",
]
