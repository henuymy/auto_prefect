"""Add channel-indicator exclusion rules and effective metric overrides.

Revision ID: 20260805_0006
Revises: 20260729_0005
"""

from __future__ import annotations

from alembic import op


revision = "20260805_0006"
down_revision = "20260729_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE channel_indicator_exclusion (
            id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
            channel_node_id BIGINT UNSIGNED NOT NULL,
            indicator_id BIGINT UNSIGNED NOT NULL,
            effective_from DATE NOT NULL,
            effective_to DATE NULL,
            status VARCHAR(16) NOT NULL DEFAULT 'ACTIVE',
            reason VARCHAR(500) NULL,
            created_by VARCHAR(100) NULL,
            created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
            updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3)
                ON UPDATE CURRENT_TIMESTAMP(3),
            CONSTRAINT pk_channel_indicator_exclusion PRIMARY KEY (id),
            CONSTRAINT uq_channel_indicator_exclusion_channel_indicator_from
                UNIQUE (channel_node_id, indicator_id, effective_from),
            CONSTRAINT ck_channel_indicator_exclusion_valid_status
                CHECK (status IN ('ACTIVE','CANCELLED')),
            CONSTRAINT ck_channel_indicator_exclusion_valid_effective_dates
                CHECK (effective_to IS NULL OR effective_to >= effective_from),
            CONSTRAINT fk_channel_indicator_exclusion_channel_node_id_hierarchy_node
                FOREIGN KEY (channel_node_id) REFERENCES hierarchy_node (id)
                ON DELETE RESTRICT,
            CONSTRAINT fk_channel_indicator_exclusion_indicator_id_indicator
                FOREIGN KEY (indicator_id) REFERENCES indicator (id)
                ON DELETE RESTRICT,
            INDEX ix_channel_indicator_exclusion_channel_validity
                (channel_node_id, effective_from, effective_to),
            INDEX ix_channel_indicator_exclusion_indicator_validity
                (indicator_id, effective_from, effective_to)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """)
    op.execute("""
        CREATE TABLE metric_caliber_override (
            id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
            collection_run_id BIGINT UNSIGNED NULL,
            node_id BIGINT UNSIGNED NOT NULL,
            indicator_id BIGINT UNSIGNED NOT NULL,
            metric_value DECIMAL(20,4) NULL,
            value_state VARCHAR(16) NOT NULL DEFAULT 'VALUE',
            calculation_type VARCHAR(32) NOT NULL DEFAULT 'EXCLUSION_ROLLUP',
            rule_fingerprint VARCHAR(64) NULL,
            created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
            CONSTRAINT pk_metric_caliber_override PRIMARY KEY (id),
            CONSTRAINT uq_metric_caliber_override_run_node_indicator
                UNIQUE (collection_run_id, node_id, indicator_id),
            CONSTRAINT ck_metric_caliber_override_valid_value_state
                CHECK (value_state IN ('VALUE','EXCLUDED')),
            CONSTRAINT ck_metric_caliber_override_valid_calculation_type
                CHECK (calculation_type IN ('EXCLUSION_ROLLUP')),
            CONSTRAINT ck_metric_caliber_override_valid_value_state_metric_value
                CHECK (
                    (value_state = 'VALUE' AND metric_value IS NOT NULL)
                    OR (value_state = 'EXCLUDED' AND metric_value IS NULL)
                ),
            CONSTRAINT fk_metric_caliber_override_collection_run_id_collection_run
                FOREIGN KEY (collection_run_id) REFERENCES collection_run (id)
                ON DELETE SET NULL,
            CONSTRAINT fk_metric_caliber_override_node_id_hierarchy_node
                FOREIGN KEY (node_id) REFERENCES hierarchy_node (id)
                ON DELETE RESTRICT,
            CONSTRAINT fk_metric_caliber_override_indicator_id_indicator
                FOREIGN KEY (indicator_id) REFERENCES indicator (id)
                ON DELETE RESTRICT,
            INDEX ix_metric_caliber_override_node_indicator_run
                (node_id, indicator_id, collection_run_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS metric_caliber_override")
    op.execute("DROP TABLE IF EXISTS channel_indicator_exclusion")
