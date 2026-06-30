"""Create the greenfield Dashboard V2 schema.

Revision ID: 20260630_0001
Revises:
Create Date: 2026-06-30
"""

from __future__ import annotations

from alembic import op


revision = "20260630_0001"
down_revision = None
branch_labels = ("dashboard_v2",)
depends_on = None


CREATE_STATEMENTS = (
    """
    CREATE TABLE collection_run (
        id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
        batch_no VARCHAR(64) NOT NULL,
        run_type VARCHAR(20) NOT NULL,
        trigger_type VARCHAR(16) NOT NULL,
        prefect_flow_run_id VARCHAR(36) NULL,
        status VARCHAR(16) NOT NULL,
        phase VARCHAR(32) NOT NULL,
        session_status VARCHAR(32) NULL,
        stat_date DATE NULL,
        started_at DATETIME(3) NOT NULL,
        finished_at DATETIME(3) NULL,
        request_count INT UNSIGNED NOT NULL DEFAULT 0,
        node_count INT UNSIGNED NOT NULL DEFAULT 0,
        row_count INT UNSIGNED NOT NULL DEFAULT 0,
        current_upsert_count INT UNSIGNED NOT NULL DEFAULT 0,
        snapshot_insert_count INT UNSIGNED NOT NULL DEFAULT 0,
        acc_upsert_count INT UNSIGNED NOT NULL DEFAULT 0,
        structure_change_summary JSON NULL,
        error_type VARCHAR(64) NULL,
        error_message JSON NULL,
        created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
        updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3)
            ON UPDATE CURRENT_TIMESTAMP(3),
        CONSTRAINT pk_collection_run PRIMARY KEY (id),
        CONSTRAINT uq_collection_run_batch_no UNIQUE (batch_no),
        CONSTRAINT uq_collection_run_prefect_flow_run_id
            UNIQUE (prefect_flow_run_id),
        CONSTRAINT ck_collection_run_valid_run_type
            CHECK (run_type IN ('REALTIME','DAY_ACC','MONTH','INDICATOR_SYNC')),
        CONSTRAINT ck_collection_run_valid_trigger_type
            CHECK (trigger_type IN ('SCHEDULED','MANUAL')),
        CONSTRAINT ck_collection_run_valid_status
            CHECK (status IN ('PENDING','RUNNING','SUCCESS','FAILED')),
        INDEX ix_collection_run_status_started (status, started_at),
        INDEX ix_collection_run_stat_date_status (stat_date, status)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE hierarchy_node (
        id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
        node_type VARCHAR(32) NOT NULL,
        node_code VARCHAR(100) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
        node_name VARCHAR(200) NOT NULL,
        parent_id BIGINT UNSIGNED NULL,
        level_no TINYINT UNSIGNED NOT NULL,
        request_enabled BOOLEAN NOT NULL DEFAULT 0,
        metric_enabled BOOLEAN NOT NULL DEFAULT 1,
        enabled BOOLEAN NOT NULL DEFAULT 1,
        sort_order INT UNSIGNED NOT NULL DEFAULT 0,
        last_seen_at DATETIME(3) NULL,
        missing_count INT UNSIGNED NOT NULL DEFAULT 0,
        created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
        updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3)
            ON UPDATE CURRENT_TIMESTAMP(3),
        CONSTRAINT pk_hierarchy_node PRIMARY KEY (id),
        CONSTRAINT uq_hierarchy_node_type_code UNIQUE (node_type, node_code),
        CONSTRAINT ck_hierarchy_node_valid_node_type
            CHECK (node_type IN
                ('CITY','BRANCH','GRID','CHANNEL_MANAGER','CHANNEL')),
        CONSTRAINT ck_hierarchy_node_valid_level_no
            CHECK (level_no BETWEEN 1 AND 5),
        CONSTRAINT fk_hierarchy_node_parent_id_hierarchy_node
            FOREIGN KEY (parent_id) REFERENCES hierarchy_node (id)
            ON DELETE RESTRICT,
        INDEX ix_hierarchy_node_parent_enabled_order
            (parent_id, enabled, sort_order),
        INDEX ix_hierarchy_node_type_enabled_order
            (node_type, enabled, sort_order),
        INDEX ix_hierarchy_node_request_enabled (request_enabled, enabled),
        INDEX ix_hierarchy_node_metric_enabled (metric_enabled, enabled)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE indicator (
        id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
        code VARCHAR(100) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
        name VARCHAR(200) NOT NULL,
        indicator_type VARCHAR(16) NOT NULL DEFAULT 'SOURCE',
        storage_mode VARCHAR(16) NOT NULL DEFAULT 'STORE',
        enabled BOOLEAN NOT NULL DEFAULT 1,
        source_active BOOLEAN NOT NULL DEFAULT 1,
        sort_order INT UNSIGNED NOT NULL DEFAULT 0,
        removed_at DATETIME(3) NULL,
        created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
        updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3)
            ON UPDATE CURRENT_TIMESTAMP(3),
        CONSTRAINT pk_indicator PRIMARY KEY (id),
        CONSTRAINT uq_indicator_code UNIQUE (code),
        CONSTRAINT ck_indicator_valid_indicator_type
            CHECK (indicator_type IN ('SOURCE','CUSTOM')),
        CONSTRAINT ck_indicator_valid_storage_mode
            CHECK (storage_mode IN ('STORE','COMPONENT')),
        INDEX ix_indicator_enabled_sort_order (enabled, sort_order),
        INDEX ix_indicator_source_active_sort_order (source_active, sort_order)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE target_plan (
        id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
        plan_name VARCHAR(200) NOT NULL,
        scenario VARCHAR(16) NOT NULL,
        period_type VARCHAR(16) NOT NULL,
        effective_from DATE NOT NULL,
        effective_to DATE NULL,
        priority INT NOT NULL DEFAULT 0,
        version_no INT UNSIGNED NOT NULL DEFAULT 1,
        status VARCHAR(16) NOT NULL DEFAULT 'DRAFT',
        supersedes_plan_id BIGINT UNSIGNED NULL,
        activated_at DATETIME(3) NULL,
        retired_at DATETIME(3) NULL,
        created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
        updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3)
            ON UPDATE CURRENT_TIMESTAMP(3),
        CONSTRAINT pk_target_plan PRIMARY KEY (id),
        CONSTRAINT uq_target_plan_business_version
            UNIQUE (scenario, period_type, plan_name, version_no),
        CONSTRAINT ck_target_plan_valid_scenario
            CHECK (scenario IN ('NORMAL','PK')),
        CONSTRAINT ck_target_plan_valid_period_type
            CHECK (period_type IN ('DAY','MONTH')),
        CONSTRAINT ck_target_plan_valid_status
            CHECK (status IN ('DRAFT','ACTIVE','RETIRED')),
        CONSTRAINT ck_target_plan_valid_effective_dates
            CHECK (effective_to IS NULL OR effective_to >= effective_from),
        CONSTRAINT fk_target_plan_supersedes_plan_id_target_plan
            FOREIGN KEY (supersedes_plan_id) REFERENCES target_plan (id)
            ON DELETE RESTRICT,
        INDEX ix_target_plan_lookup
            (scenario, period_type, status, effective_from, effective_to, priority)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE hierarchy_parent_history (
        id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
        child_node_id BIGINT UNSIGNED NOT NULL,
        parent_node_id BIGINT UNSIGNED NOT NULL,
        valid_from DATETIME(3) NOT NULL,
        valid_to DATETIME(3) NULL,
        collection_run_id BIGINT UNSIGNED NULL,
        change_type VARCHAR(32) NOT NULL,
        active_child_node_id BIGINT UNSIGNED GENERATED ALWAYS AS
            (CASE WHEN valid_to IS NULL THEN child_node_id ELSE NULL END) STORED,
        created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
        CONSTRAINT pk_hierarchy_parent_history PRIMARY KEY (id),
        CONSTRAINT uq_hierarchy_history_active_child UNIQUE (active_child_node_id),
        CONSTRAINT ck_hierarchy_parent_history_valid_change_type
            CHECK (change_type IN ('CREATED','MOVED','RESTORED')),
        CONSTRAINT ck_hierarchy_parent_history_valid_history_dates
            CHECK (valid_to IS NULL OR valid_to >= valid_from),
        CONSTRAINT fk_hierarchy_parent_history_child_node_id_hierarchy_node
            FOREIGN KEY (child_node_id) REFERENCES hierarchy_node (id)
            ON DELETE RESTRICT,
        CONSTRAINT fk_hierarchy_parent_history_parent_node_id_hierarchy_node
            FOREIGN KEY (parent_node_id) REFERENCES hierarchy_node (id)
            ON DELETE RESTRICT,
        CONSTRAINT fk_hierarchy_parent_history_collection_run_id_collection_run
            FOREIGN KEY (collection_run_id) REFERENCES collection_run (id)
            ON DELETE SET NULL,
        INDEX ix_hierarchy_history_child_validity
            (child_node_id, valid_from, valid_to),
        INDEX ix_hierarchy_history_parent_validity
            (parent_node_id, valid_from, valid_to)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE indicator_formula_component (
        id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
        custom_indicator_id BIGINT UNSIGNED NOT NULL,
        source_indicator_id BIGINT UNSIGNED NOT NULL,
        coefficient DECIMAL(20,4) NOT NULL DEFAULT 1,
        sort_order INT UNSIGNED NOT NULL DEFAULT 0,
        created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
        updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3)
            ON UPDATE CURRENT_TIMESTAMP(3),
        CONSTRAINT pk_indicator_formula_component PRIMARY KEY (id),
        CONSTRAINT uq_indicator_formula_component_pair
            UNIQUE (custom_indicator_id, source_indicator_id),
        CONSTRAINT fk_indicator_formula_component_custom_indicator_id_indicator
            FOREIGN KEY (custom_indicator_id) REFERENCES indicator (id)
            ON DELETE CASCADE,
        CONSTRAINT fk_indicator_formula_component_source_indicator_id_indicator
            FOREIGN KEY (source_indicator_id) REFERENCES indicator (id)
            ON DELETE RESTRICT,
        INDEX ix_indicator_formula_component_source (source_indicator_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE metric_target_value (
        id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
        plan_id BIGINT UNSIGNED NOT NULL,
        node_id BIGINT UNSIGNED NOT NULL,
        indicator_id BIGINT UNSIGNED NOT NULL,
        target_value DECIMAL(20,4) NOT NULL,
        created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
        updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3)
            ON UPDATE CURRENT_TIMESTAMP(3),
        CONSTRAINT pk_metric_target_value PRIMARY KEY (id),
        CONSTRAINT uq_metric_target_value_plan_node_indicator
            UNIQUE (plan_id, node_id, indicator_id),
        CONSTRAINT fk_metric_target_value_plan_id_target_plan
            FOREIGN KEY (plan_id) REFERENCES target_plan (id)
            ON DELETE CASCADE,
        CONSTRAINT fk_metric_target_value_node_id_hierarchy_node
            FOREIGN KEY (node_id) REFERENCES hierarchy_node (id)
            ON DELETE RESTRICT,
        CONSTRAINT fk_metric_target_value_indicator_id_indicator
            FOREIGN KEY (indicator_id) REFERENCES indicator (id)
            ON DELETE RESTRICT,
        INDEX ix_metric_target_value_node_indicator (node_id, indicator_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE metric_current (
        id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
        node_id BIGINT UNSIGNED NOT NULL,
        indicator_id BIGINT UNSIGNED NOT NULL,
        collection_run_id BIGINT UNSIGNED NULL,
        metric_value DECIMAL(20,4) NOT NULL,
        stat_date DATE NOT NULL,
        collected_at DATETIME(3) NOT NULL,
        updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3)
            ON UPDATE CURRENT_TIMESTAMP(3),
        CONSTRAINT pk_metric_current PRIMARY KEY (id),
        CONSTRAINT uq_metric_current_node_indicator UNIQUE (node_id, indicator_id),
        CONSTRAINT fk_metric_current_node_id_hierarchy_node
            FOREIGN KEY (node_id) REFERENCES hierarchy_node (id)
            ON DELETE RESTRICT,
        CONSTRAINT fk_metric_current_indicator_id_indicator
            FOREIGN KEY (indicator_id) REFERENCES indicator (id)
            ON DELETE RESTRICT,
        CONSTRAINT fk_metric_current_collection_run_id_collection_run
            FOREIGN KEY (collection_run_id) REFERENCES collection_run (id)
            ON DELETE SET NULL,
        INDEX ix_metric_current_indicator_value (indicator_id, metric_value),
        INDEX ix_metric_current_stat_date_indicator (stat_date, indicator_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE metric_snapshot (
        id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
        collected_at DATETIME(3) NOT NULL,
        collection_run_id BIGINT UNSIGNED NOT NULL,
        node_id BIGINT UNSIGNED NOT NULL,
        indicator_id BIGINT UNSIGNED NOT NULL,
        metric_value DECIMAL(20,4) NOT NULL,
        CONSTRAINT pk_metric_snapshot PRIMARY KEY (id, collected_at),
        CONSTRAINT uq_metric_snapshot_run_node_indicator_collected
            UNIQUE (collection_run_id, node_id, indicator_id, collected_at),
        INDEX ix_metric_snapshot_node_indicator_collected
            (node_id, indicator_id, collected_at),
        INDEX ix_metric_snapshot_collected_at (collected_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    PARTITION BY RANGE COLUMNS (collected_at) (
        PARTITION p_future VALUES LESS THAN (MAXVALUE)
    )
    """,
    """
    CREATE TABLE metric_acc (
        id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
        period_type VARCHAR(16) NOT NULL,
        stat_date DATE NOT NULL,
        node_id BIGINT UNSIGNED NOT NULL,
        indicator_id BIGINT UNSIGNED NOT NULL,
        collection_run_id BIGINT UNSIGNED NULL,
        metric_value DECIMAL(20,4) NOT NULL,
        collected_at DATETIME(3) NOT NULL,
        updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3)
            ON UPDATE CURRENT_TIMESTAMP(3),
        CONSTRAINT pk_metric_acc PRIMARY KEY (id),
        CONSTRAINT uq_metric_acc_period_date_node_indicator
            UNIQUE (period_type, stat_date, node_id, indicator_id),
        CONSTRAINT ck_metric_acc_valid_period_type
            CHECK (period_type IN ('DAY_ACC','MONTH')),
        CONSTRAINT fk_metric_acc_node_id_hierarchy_node
            FOREIGN KEY (node_id) REFERENCES hierarchy_node (id)
            ON DELETE RESTRICT,
        CONSTRAINT fk_metric_acc_indicator_id_indicator
            FOREIGN KEY (indicator_id) REFERENCES indicator (id)
            ON DELETE RESTRICT,
        CONSTRAINT fk_metric_acc_collection_run_id_collection_run
            FOREIGN KEY (collection_run_id) REFERENCES collection_run (id)
            ON DELETE SET NULL,
        INDEX ix_metric_acc_node_indicator_period_date
            (node_id, indicator_id, period_type, stat_date),
        INDEX ix_metric_acc_period_date_indicator
            (period_type, stat_date, indicator_id, metric_value),
        INDEX ix_metric_acc_stat_date (stat_date)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
)


DROP_ORDER = (
    "metric_acc",
    "metric_snapshot",
    "metric_current",
    "metric_target_value",
    "indicator_formula_component",
    "hierarchy_parent_history",
    "target_plan",
    "indicator",
    "hierarchy_node",
    "collection_run",
)


def upgrade() -> None:
    for statement in CREATE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    for table_name in DROP_ORDER:
        op.execute(f"DROP TABLE IF EXISTS `{table_name}`")
