"""Persist monitor target identities.

Revision ID: 20260719_0003
Revises: 20260718_0002
"""

from alembic import op


revision = "20260719_0003"
down_revision = "20260718_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE monitor_runs ADD COLUMN target_kind VARCHAR(16) NULL AFTER task_name")
    op.execute("ALTER TABLE monitor_runs ADD COLUMN target_id VARCHAR(48) NULL AFTER target_kind")
    op.execute("""
        UPDATE monitor_runs
        SET target_kind = 'legacy', target_id = CONCAT('legacy-', id)
        WHERE target_kind IS NULL OR target_id IS NULL
    """)
    op.execute("ALTER TABLE monitor_runs MODIFY target_kind VARCHAR(16) NOT NULL")
    op.execute("ALTER TABLE monitor_runs MODIFY target_id VARCHAR(48) NOT NULL")
    op.execute("CREATE INDEX ix_monitor_runs_target_status ON monitor_runs (target_kind, status, scheduled_at)")


def downgrade() -> None:
    op.execute("DROP INDEX ix_monitor_runs_target_status ON monitor_runs")
    op.execute("ALTER TABLE monitor_runs DROP COLUMN target_id")
    op.execute("ALTER TABLE monitor_runs DROP COLUMN target_kind")
