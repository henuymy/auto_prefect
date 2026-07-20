"""Add MySQL-owned monitor center tables.

Revision ID: 20260718_0002
Revises: 20260630_0001
"""

from alembic import op


revision = "20260718_0002"
down_revision = "20260630_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE monitor_runs (
            id VARCHAR(48) NOT NULL,
            source VARCHAR(16) NOT NULL,
            external_run_id VARCHAR(64) NOT NULL,
            task_name VARCHAR(255) NOT NULL,
            `trigger` VARCHAR(32) NOT NULL,
            status VARCHAR(16) NOT NULL,
            scheduled_at DATETIME(3) NULL,
            started_at DATETIME(3) NULL,
            finished_at DATETIME(3) NULL,
            current_step VARCHAR(128) NOT NULL DEFAULT '尚未开始',
            business_error_summary TEXT NULL,
            technical_error_summary TEXT NULL,
            created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
            updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
            PRIMARY KEY (id),
            UNIQUE KEY uq_monitor_runs_source_external (source, external_run_id),
            KEY ix_monitor_runs_status_scheduled (status, scheduled_at),
            KEY ix_monitor_runs_task_started (task_name, started_at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """)
    op.execute("""
        CREATE TABLE monitor_steps (
            id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
            run_id VARCHAR(48) NOT NULL,
            name VARCHAR(128) NOT NULL,
            status VARCHAR(16) NOT NULL,
            message TEXT NOT NULL,
            started_at DATETIME(3) NULL,
            finished_at DATETIME(3) NULL,
            PRIMARY KEY (id),
            UNIQUE KEY uq_monitor_steps_run_name (run_id, name),
            CONSTRAINT fk_monitor_steps_run FOREIGN KEY (run_id) REFERENCES monitor_runs(id) ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """)
    op.execute("""
        CREATE TABLE monitor_events (
            id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
            stream_sequence BIGINT UNSIGNED NOT NULL,
            run_id VARCHAR(48) NOT NULL,
            at DATETIME(3) NOT NULL,
            level VARCHAR(8) NOT NULL,
            message TEXT NOT NULL,
            details TEXT NOT NULL,
            PRIMARY KEY (id),
            UNIQUE KEY uq_monitor_events_stream_sequence (stream_sequence),
            KEY ix_monitor_events_run_sequence (run_id, stream_sequence),
            CONSTRAINT fk_monitor_events_run FOREIGN KEY (run_id) REFERENCES monitor_runs(id) ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS monitor_events")
    op.execute("DROP TABLE IF EXISTS monitor_steps")
    op.execute("DROP TABLE IF EXISTS monitor_runs")
