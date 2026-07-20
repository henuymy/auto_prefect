"""Persist Prefect monitor event identities and state ordering.

Revision ID: 20260719_0004
Revises: 20260719_0003
"""

from alembic import op


revision = "20260719_0004"
down_revision = "20260719_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE monitor_runs ADD COLUMN state_occurred_at DATETIME(3) NULL AFTER finished_at")
    op.execute("""
        UPDATE monitor_runs
        SET state_occurred_at = COALESCE(finished_at, started_at, scheduled_at, updated_at)
        WHERE state_occurred_at IS NULL
    """)
    op.execute("ALTER TABLE monitor_events ADD COLUMN source_event_id VARCHAR(64) NULL AFTER id")
    op.execute("CREATE UNIQUE INDEX uq_monitor_events_source_event_id ON monitor_events (source_event_id)")


def downgrade() -> None:
    op.execute("DROP INDEX uq_monitor_events_source_event_id ON monitor_events")
    op.execute("ALTER TABLE monitor_events DROP COLUMN source_event_id")
    op.execute("ALTER TABLE monitor_runs DROP COLUMN state_occurred_at")
