"""create custom indicator component

Revision ID: 20260626_0018
Revises: 20260624_0017
Create Date: 2026-06-26
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


revision: str = "20260626_0018"
down_revision: Union[str, Sequence[str], None] = "20260624_0017"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "custom_indicator_component",
        sa.Column(
            "id",
            mysql.BIGINT(unsigned=True),
            autoincrement=True,
            nullable=False,
        ),
        sa.Column(
            "custom_indicator_id",
            mysql.BIGINT(unsigned=True),
            nullable=False,
        ),
        sa.Column(
            "source_indicator_id",
            mysql.BIGINT(unsigned=True),
            nullable=False,
        ),
        sa.Column(
            "coefficient",
            mysql.DECIMAL(20, 4),
            server_default=sa.text("1"),
            nullable=False,
        ),
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
        sa.ForeignKeyConstraint(
            ["custom_indicator_id"],
            ["indicator.id"],
            name=op.f("fk_custom_indicator_component_custom_indicator_id_indicator"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["source_indicator_id"],
            ["indicator.id"],
            name=op.f("fk_custom_indicator_component_source_indicator_id_indicator"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_custom_indicator_component")),
        sa.UniqueConstraint(
            "custom_indicator_id",
            "source_indicator_id",
            name="uq_custom_indicator_component_pair",
        ),
    )
    op.create_index(
        "ix_custom_indicator_component_source",
        "custom_indicator_component",
        ["source_indicator_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_custom_indicator_component_source",
        table_name="custom_indicator_component",
    )
    op.drop_table("custom_indicator_component")
