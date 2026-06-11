"""support monthly cumulative metrics

Revision ID: 20260611_0009
Revises: 20260611_0008
Create Date: 2026-06-11
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision: str = "20260611_0009"
down_revision: Union[str, Sequence[str], None] = "20260611_0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    run_checks = {
        item["name"]: item["sqltext"]
        for item in inspect(bind).get_check_constraints("collection_run")
    }
    run_check = run_checks.get("ck_collection_run_valid_run_type", "")
    if "MONTHLY" not in run_check:
        if run_check:
            op.drop_constraint(
                op.f("ck_collection_run_valid_run_type"),
                "collection_run",
                type_="check",
            )
        op.create_check_constraint(
            op.f("ck_collection_run_valid_run_type"),
            "collection_run",
            "run_type IN ('REALTIME', 'DAILY', 'MONTHLY')",
        )

    tables = set(inspect(bind).get_table_names())
    if "metric_daily" in tables and "metric_acc" not in tables:
        op.rename_table("metric_daily", "metric_acc")

    columns = {
        item["name"]
        for item in inspect(bind).get_columns("metric_acc")
    }
    if "period_type" not in columns:
        op.add_column(
            "metric_acc",
            sa.Column(
                "period_type",
                sa.String(length=16),
                server_default=sa.text("'DAY_ACC'"),
                nullable=False,
            ),
        )

    uniques = {
        item["name"]
        for item in inspect(bind).get_unique_constraints("metric_acc")
    }
    if "uq_metric_daily_date_area_indicator" in uniques:
        op.drop_constraint(
            "uq_metric_daily_date_area_indicator",
            "metric_acc",
            type_="unique",
        )

    checks = {
        item["name"]
        for item in inspect(bind).get_check_constraints("metric_acc")
    }
    if "ck_metric_acc_valid_period_type" not in checks:
        op.create_check_constraint(
            op.f("ck_metric_acc_valid_period_type"),
            "metric_acc",
            "period_type IN ('DAY_ACC', 'MONTH')",
        )

    uniques = {
        item["name"]
        for item in inspect(bind).get_unique_constraints("metric_acc")
    }
    if "uq_metric_acc_period_date_area_indicator" not in uniques:
        op.create_unique_constraint(
            "uq_metric_acc_period_date_area_indicator",
            "metric_acc",
            ["period_type", "stat_date", "area_id", "indicator_id"],
        )

    indexes = {
        item["name"]
        for item in inspect(bind).get_indexes("metric_acc")
    }
    if "ix_metric_acc_area_indicator_date" not in indexes:
        op.create_index(
            "ix_metric_acc_area_indicator_date",
            "metric_acc",
            ["area_id", "indicator_id", "period_type", "stat_date"],
            unique=False,
        )
    if "ix_metric_acc_period_date_indicator_value" not in indexes:
        op.create_index(
            "ix_metric_acc_period_date_indicator_value",
            "metric_acc",
            ["period_type", "stat_date", "indicator_id", "metric_value"],
            unique=False,
        )

    indexes = {
        item["name"]
        for item in inspect(bind).get_indexes("metric_acc")
    }
    if "ix_metric_daily_area_indicator_date" in indexes:
        op.drop_index(
            "ix_metric_daily_area_indicator_date",
            table_name="metric_acc",
        )
    if "ix_metric_daily_date_indicator_value" in indexes:
        op.drop_index(
            "ix_metric_daily_date_indicator_value",
            table_name="metric_acc",
        )


def downgrade() -> None:
    op.drop_index(
        "ix_metric_acc_period_date_indicator_value",
        table_name="metric_acc",
    )
    op.drop_index(
        "ix_metric_acc_area_indicator_date",
        table_name="metric_acc",
    )
    op.drop_constraint(
        "uq_metric_acc_period_date_area_indicator",
        "metric_acc",
        type_="unique",
    )
    op.drop_constraint(
        op.f("ck_metric_acc_valid_period_type"),
        "metric_acc",
        type_="check",
    )
    op.drop_column("metric_acc", "period_type")
    op.create_unique_constraint(
        "uq_metric_daily_date_area_indicator",
        "metric_acc",
        ["stat_date", "area_id", "indicator_id"],
    )
    op.create_index(
        "ix_metric_daily_area_indicator_date",
        "metric_acc",
        ["area_id", "indicator_id", "stat_date"],
        unique=False,
    )
    op.create_index(
        "ix_metric_daily_date_indicator_value",
        "metric_acc",
        ["stat_date", "indicator_id", "metric_value"],
        unique=False,
    )
    op.rename_table("metric_acc", "metric_daily")

    op.drop_constraint(
        op.f("ck_collection_run_valid_run_type"),
        "collection_run",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_collection_run_valid_run_type"),
        "collection_run",
        "run_type IN ('REALTIME', 'DAILY')",
    )
