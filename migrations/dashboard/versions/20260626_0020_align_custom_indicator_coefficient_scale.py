"""align custom indicator coefficient scale

Revision ID: 20260626_0020
Revises: 20260626_0019
Create Date: 2026-06-26
"""

from typing import Sequence, Union

from alembic import op
from sqlalchemy.dialects import mysql


revision: str = "20260626_0020"
down_revision: Union[str, Sequence[str], None] = "20260626_0019"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "custom_indicator_component",
        "coefficient",
        existing_type=mysql.DECIMAL(20, 6),
        type_=mysql.DECIMAL(20, 4),
        existing_nullable=False,
        existing_server_default="1",
    )


def downgrade() -> None:
    op.alter_column(
        "custom_indicator_component",
        "coefficient",
        existing_type=mysql.DECIMAL(20, 4),
        type_=mysql.DECIMAL(20, 6),
        existing_nullable=False,
        existing_server_default="1",
    )
