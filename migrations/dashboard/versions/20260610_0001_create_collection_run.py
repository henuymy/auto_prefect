"""create collection_run

Revision ID: 20260610_0001
Revises:
Create Date: 2026-06-10
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


revision: str = "20260610_0001"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "collection_run",
        sa.Column(
            "id",
            mysql.BIGINT(unsigned=True),
            autoincrement=True,
            nullable=False,
        ),
        sa.Column("batch_no", sa.String(length=64), nullable=False),
        sa.Column("trigger_type", sa.String(length=16), nullable=False),
        sa.Column("prefect_flow_run_id", sa.String(length=36), nullable=True),
        sa.Column(
            "status",
            sa.String(length=16),
            server_default=sa.text("'PENDING'"),
            nullable=False,
        ),
        sa.Column(
            "phase",
            sa.String(length=32),
            server_default=sa.text("'TRIGGER'"),
            nullable=False,
        ),
        sa.Column("session_status", sa.String(length=32), nullable=True),
        sa.Column("stat_date", sa.Date(), nullable=True),
        sa.Column("started_at", mysql.DATETIME(fsp=3), nullable=True),
        sa.Column("finished_at", mysql.DATETIME(fsp=3), nullable=True),
        sa.Column(
            "request_count",
            mysql.INTEGER(unsigned=True),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "area_count",
            mysql.INTEGER(unsigned=True),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "row_count",
            mysql.INTEGER(unsigned=True),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "current_upsert_count",
            mysql.INTEGER(unsigned=True),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "snapshot_insert_count",
            mysql.INTEGER(unsigned=True),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("error_type", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            mysql.DATETIME(fsp=3),
            server_default=sa.text("CURRENT_TIMESTAMP(3)"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            mysql.DATETIME(fsp=3),
            server_default=sa.text("CURRENT_TIMESTAMP(3)"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('PENDING', 'RUNNING', 'SUCCESS', 'FAILED')",
            name=op.f("ck_collection_run_valid_status"),
        ),
        sa.CheckConstraint(
            "trigger_type IN ('MANUAL', 'SCHEDULED')",
            name=op.f("ck_collection_run_valid_trigger_type"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_collection_run")),
        sa.UniqueConstraint("batch_no", name=op.f("uq_collection_run_batch_no")),
        sa.UniqueConstraint(
            "prefect_flow_run_id",
            name=op.f("uq_collection_run_prefect_flow_run_id"),
        ),
    )
    op.create_index(
        "ix_collection_run_stat_date_status",
        "collection_run",
        ["stat_date", "status"],
        unique=False,
    )
    op.create_index(
        "ix_collection_run_status_started_at",
        "collection_run",
        ["status", "started_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_collection_run_status_started_at",
        table_name="collection_run",
    )
    op.drop_index(
        "ix_collection_run_stat_date_status",
        table_name="collection_run",
    )
    op.drop_table("collection_run")
