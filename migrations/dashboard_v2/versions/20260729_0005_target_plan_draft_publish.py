"""Allow editable target drafts and published versions to share a version number.

Revision ID: 20260729_0005
Revises: 20260719_0004
"""

from __future__ import annotations

from alembic import op


revision = "20260729_0005"
down_revision = "20260719_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE target_plan "
        "ADD COLUMN is_realtime TINYINT(1) NOT NULL DEFAULT 0 AFTER status"
    )
    op.drop_constraint("uq_target_plan_business_version", "target_plan", type_="unique")
    op.create_unique_constraint(
        "uq_target_plan_status_version",
        "target_plan",
        ["scenario", "period_type", "plan_name", "status", "version_no"],
    )
    op.create_index(
        "ix_target_plan_realtime_lookup",
        "target_plan",
        [
            "scenario",
            "period_type",
            "status",
            "is_realtime",
            "effective_from",
            "effective_to",
            "priority",
        ],
    )


def downgrade() -> None:
    op.drop_index("ix_target_plan_realtime_lookup", table_name="target_plan")
    op.drop_constraint("uq_target_plan_status_version", "target_plan", type_="unique")
    op.create_unique_constraint(
        "uq_target_plan_business_version",
        "target_plan",
        ["scenario", "period_type", "plan_name", "version_no"],
    )
    op.execute("ALTER TABLE target_plan DROP COLUMN is_realtime")
