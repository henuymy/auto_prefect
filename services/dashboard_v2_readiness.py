"""Dashboard V2 migration and critical-schema readiness checks."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import SQLAlchemyError

from infrastructure.dashboard_mysql import create_dashboard_engine


PROJECT_ROOT = Path(__file__).resolve().parents[1]
_SHANGHAI = ZoneInfo("Asia/Shanghai")


def load_expected_revision() -> str:
    config = Config(str(PROJECT_ROOT / "alembic_dashboard_v2.ini"))
    config.set_main_option(
        "script_location", str(PROJECT_ROOT / "migrations" / "dashboard_v2")
    )
    heads = ScriptDirectory.from_config(config).get_heads()
    if len(heads) != 1:
        raise RuntimeError(f"驾驶舱 V2 迁移链必须只有一个 head: {heads}")
    return heads[0]


EXPECTED_REVISION = load_expected_revision()
EXPECTED_TABLES = {
    "collection_run",
    "hierarchy_node",
    "hierarchy_parent_history",
    "indicator",
    "indicator_formula_component",
    "channel_indicator_exclusion",
    "target_plan",
    "metric_target_value",
    "metric_current",
    "metric_snapshot",
    "metric_acc",
    "metric_caliber_override",
    "monitor_runs",
    "monitor_steps",
    "monitor_events",
}
EXPECTED_COLUMNS = {
    "collection_run": {
        "id", "batch_no", "run_type", "trigger_type", "status", "phase",
        "stat_date", "started_at", "finished_at", "created_at", "updated_at",
    },
    "hierarchy_node": {
        "id", "node_type", "node_code", "node_name", "parent_id", "level_no",
        "request_enabled", "metric_enabled", "enabled", "sort_order",
        "last_seen_at", "missing_count", "created_at", "updated_at",
    },
    "hierarchy_parent_history": {
        "id", "child_node_id", "parent_node_id", "valid_from", "valid_to",
        "collection_run_id", "change_type", "active_child_node_id", "created_at",
    },
    "indicator": {
        "id", "code", "name", "indicator_type", "storage_mode", "enabled",
        "source_active", "sort_order", "removed_at", "created_at", "updated_at",
    },
    "indicator_formula_component": {
        "id", "custom_indicator_id", "source_indicator_id", "coefficient",
        "sort_order", "created_at", "updated_at",
    },
    "channel_indicator_exclusion": {
        "id", "channel_node_id", "indicator_id", "effective_from",
        "effective_to", "status", "reason", "created_by", "created_at",
        "updated_at",
    },
    "target_plan": {
        "id", "plan_name", "scenario", "period_type", "effective_from",
        "effective_to", "priority", "version_no", "status",
        "is_realtime", "supersedes_plan_id", "activated_at", "retired_at",
        "created_at", "updated_at",
    },
    "metric_target_value": {
        "id", "plan_id", "node_id", "indicator_id", "target_value",
        "created_at", "updated_at",
    },
    "metric_current": {
        "id", "node_id", "indicator_id", "collection_run_id", "metric_value",
        "stat_date", "collected_at", "updated_at",
    },
    "metric_snapshot": {
        "id", "collected_at", "collection_run_id", "node_id", "indicator_id",
        "metric_value",
    },
    "metric_acc": {
        "id", "period_type", "stat_date", "node_id", "indicator_id",
        "collection_run_id", "metric_value", "collected_at", "updated_at",
    },
    "metric_caliber_override": {
        "id", "collection_run_id", "node_id", "indicator_id", "metric_value",
        "value_state", "calculation_type", "rule_fingerprint", "created_at",
    },
    "monitor_runs": {
        "id", "source", "external_run_id", "task_name", "target_kind",
        "target_id", "trigger", "status", "scheduled_at", "started_at",
        "finished_at", "state_occurred_at", "current_step",
        "business_error_summary", "technical_error_summary", "created_at",
        "updated_at",
    },
    "monitor_steps": {
        "id", "run_id", "name", "status", "message", "started_at",
        "finished_at",
    },
    "monitor_events": {
        "id", "source_event_id", "stream_sequence", "run_id", "at", "level",
        "message", "details",
    },
}
EXPECTED_GENERATED_COLUMNS = {
    ("hierarchy_parent_history", "active_child_node_id"),
}
EXPECTED_UNIQUE_CONSTRAINTS = {
    ("collection_run", "uq_collection_run_batch_no", ("batch_no",)),
    (
        "collection_run",
        "uq_collection_run_prefect_flow_run_id",
        ("prefect_flow_run_id",),
    ),
    ("hierarchy_node", "uq_hierarchy_node_type_code", ("node_type", "node_code")),
    (
        "hierarchy_parent_history",
        "uq_hierarchy_history_active_child",
        ("active_child_node_id",),
    ),
    ("indicator", "uq_indicator_code", ("code",)),
    (
        "indicator_formula_component",
        "uq_indicator_formula_component_pair",
        ("custom_indicator_id", "source_indicator_id"),
    ),
    (
        "channel_indicator_exclusion",
        "uq_channel_indicator_exclusion_channel_indicator_from",
        ("channel_node_id", "indicator_id", "effective_from"),
    ),
    (
        "target_plan",
        "uq_target_plan_status_version",
        ("scenario", "period_type", "plan_name", "status", "version_no"),
    ),
    (
        "metric_target_value",
        "uq_metric_target_value_plan_node_indicator",
        ("plan_id", "node_id", "indicator_id"),
    ),
    (
        "metric_current",
        "uq_metric_current_node_indicator",
        ("node_id", "indicator_id"),
    ),
    (
        "metric_snapshot",
        "uq_metric_snapshot_run_node_indicator_collected",
        ("collection_run_id", "node_id", "indicator_id", "collected_at"),
    ),
    (
        "metric_acc",
        "uq_metric_acc_period_date_node_indicator",
        ("period_type", "stat_date", "node_id", "indicator_id"),
    ),
    (
        "metric_caliber_override",
        "uq_metric_caliber_override_run_node_indicator",
        ("collection_run_id", "node_id", "indicator_id"),
    ),
    (
        "monitor_runs",
        "uq_monitor_runs_source_external",
        ("source", "external_run_id"),
    ),
    ("monitor_steps", "uq_monitor_steps_run_name", ("run_id", "name")),
    (
        "monitor_events",
        "uq_monitor_events_source_event_id",
        ("source_event_id",),
    ),
    (
        "monitor_events",
        "uq_monitor_events_stream_sequence",
        ("stream_sequence",),
    ),
}
EXPECTED_FOREIGN_KEYS = {
    ("hierarchy_node", ("parent_id",), "hierarchy_node", ("id",), "RESTRICT"),
    ("target_plan", ("supersedes_plan_id",), "target_plan", ("id",), "RESTRICT"),
    (
        "hierarchy_parent_history", ("child_node_id",),
        "hierarchy_node", ("id",), "RESTRICT",
    ),
    (
        "hierarchy_parent_history", ("parent_node_id",),
        "hierarchy_node", ("id",), "RESTRICT",
    ),
    (
        "hierarchy_parent_history", ("collection_run_id",),
        "collection_run", ("id",), "SET NULL",
    ),
    (
        "indicator_formula_component", ("custom_indicator_id",),
        "indicator", ("id",), "CASCADE",
    ),
    (
        "indicator_formula_component", ("source_indicator_id",),
        "indicator", ("id",), "RESTRICT",
    ),
    (
        "channel_indicator_exclusion", ("channel_node_id",),
        "hierarchy_node", ("id",), "RESTRICT",
    ),
    (
        "channel_indicator_exclusion", ("indicator_id",),
        "indicator", ("id",), "RESTRICT",
    ),
    ("metric_target_value", ("plan_id",), "target_plan", ("id",), "CASCADE"),
    (
        "metric_target_value", ("node_id",),
        "hierarchy_node", ("id",), "RESTRICT",
    ),
    (
        "metric_target_value", ("indicator_id",),
        "indicator", ("id",), "RESTRICT",
    ),
    ("metric_current", ("node_id",), "hierarchy_node", ("id",), "RESTRICT"),
    ("metric_current", ("indicator_id",), "indicator", ("id",), "RESTRICT"),
    (
        "metric_current", ("collection_run_id",),
        "collection_run", ("id",), "SET NULL",
    ),
    ("metric_acc", ("node_id",), "hierarchy_node", ("id",), "RESTRICT"),
    ("metric_acc", ("indicator_id",), "indicator", ("id",), "RESTRICT"),
    (
        "metric_acc", ("collection_run_id",),
        "collection_run", ("id",), "SET NULL",
    ),
    (
        "metric_caliber_override", ("collection_run_id",),
        "collection_run", ("id",), "SET NULL",
    ),
    (
        "metric_caliber_override", ("node_id",),
        "hierarchy_node", ("id",), "RESTRICT",
    ),
    (
        "metric_caliber_override", ("indicator_id",),
        "indicator", ("id",), "RESTRICT",
    ),
    ("monitor_steps", ("run_id",), "monitor_runs", ("id",), "CASCADE"),
    ("monitor_events", ("run_id",), "monitor_runs", ("id",), "CASCADE"),
}
EXPECTED_CHECK_CONSTRAINTS = {
    ("collection_run", "ck_collection_run_valid_run_type"),
    ("collection_run", "ck_collection_run_valid_trigger_type"),
    ("collection_run", "ck_collection_run_valid_status"),
    ("hierarchy_node", "ck_hierarchy_node_valid_node_type"),
    ("hierarchy_node", "ck_hierarchy_node_valid_level_no"),
    ("indicator", "ck_indicator_valid_indicator_type"),
    ("indicator", "ck_indicator_valid_storage_mode"),
    ("channel_indicator_exclusion", "ck_channel_indicator_exclusion_valid_status"),
    (
        "channel_indicator_exclusion",
        "ck_channel_indicator_exclusion_valid_effective_dates",
    ),
    ("target_plan", "ck_target_plan_valid_scenario"),
    ("target_plan", "ck_target_plan_valid_period_type"),
    ("target_plan", "ck_target_plan_valid_status"),
    ("target_plan", "ck_target_plan_valid_effective_dates"),
    (
        "hierarchy_parent_history",
        "ck_hierarchy_parent_history_valid_change_type",
    ),
    (
        "hierarchy_parent_history",
        "ck_hierarchy_parent_history_valid_history_dates",
    ),
    ("metric_acc", "ck_metric_acc_valid_period_type"),
    ("metric_caliber_override", "ck_metric_caliber_override_valid_value_state"),
    (
        "metric_caliber_override",
        "ck_metric_caliber_override_valid_calculation_type",
    ),
    (
        "metric_caliber_override",
        "ck_metric_caliber_override_valid_value_state_metric_value",
    ),
}
EXPECTED_INDEXES = {
    ("collection_run", "ix_collection_run_status_started", ("status", "started_at")),
    (
        "collection_run", "ix_collection_run_stat_date_status",
        ("stat_date", "status"),
    ),
    (
        "hierarchy_node", "ix_hierarchy_node_parent_enabled_order",
        ("parent_id", "enabled", "sort_order"),
    ),
    (
        "hierarchy_node", "ix_hierarchy_node_type_enabled_order",
        ("node_type", "enabled", "sort_order"),
    ),
    (
        "hierarchy_node", "ix_hierarchy_node_request_enabled",
        ("request_enabled", "enabled"),
    ),
    (
        "hierarchy_node", "ix_hierarchy_node_metric_enabled",
        ("metric_enabled", "enabled"),
    ),
    (
        "hierarchy_parent_history", "ix_hierarchy_history_child_validity",
        ("child_node_id", "valid_from", "valid_to"),
    ),
    (
        "hierarchy_parent_history", "ix_hierarchy_history_parent_validity",
        ("parent_node_id", "valid_from", "valid_to"),
    ),
    (
        "target_plan", "ix_target_plan_lookup",
        (
            "scenario", "period_type", "status", "effective_from",
            "effective_to", "priority",
        ),
    ),
    (
        "target_plan", "ix_target_plan_realtime_lookup",
        (
            "scenario", "period_type", "status", "is_realtime",
            "effective_from", "effective_to", "priority",
        ),
    ),
    (
        "indicator", "ix_indicator_enabled_sort_order",
        ("enabled", "sort_order"),
    ),
    (
        "indicator", "ix_indicator_source_active_sort_order",
        ("source_active", "sort_order"),
    ),
    (
        "indicator_formula_component", "ix_indicator_formula_component_source",
        ("source_indicator_id",),
    ),
    (
        "channel_indicator_exclusion",
        "ix_channel_indicator_exclusion_channel_validity",
        ("channel_node_id", "effective_from", "effective_to"),
    ),
    (
        "channel_indicator_exclusion",
        "ix_channel_indicator_exclusion_indicator_validity",
        ("indicator_id", "effective_from", "effective_to"),
    ),
    (
        "metric_target_value", "ix_metric_target_value_node_indicator",
        ("node_id", "indicator_id"),
    ),
    (
        "metric_current", "ix_metric_current_indicator_value",
        ("indicator_id", "metric_value"),
    ),
    (
        "metric_current", "ix_metric_current_stat_date_indicator",
        ("stat_date", "indicator_id"),
    ),
    (
        "metric_snapshot", "ix_metric_snapshot_node_indicator_collected",
        ("node_id", "indicator_id", "collected_at"),
    ),
    (
        "metric_snapshot", "ix_metric_snapshot_collected_at",
        ("collected_at",),
    ),
    (
        "metric_acc", "ix_metric_acc_node_indicator_period_date",
        ("node_id", "indicator_id", "period_type", "stat_date"),
    ),
    (
        "metric_acc", "ix_metric_acc_period_date_indicator",
        ("period_type", "stat_date", "indicator_id", "metric_value"),
    ),
    ("metric_acc", "ix_metric_acc_stat_date", ("stat_date",)),
    (
        "metric_caliber_override",
        "ix_metric_caliber_override_node_indicator_run",
        ("node_id", "indicator_id", "collection_run_id"),
    ),
    (
        "monitor_runs",
        "ix_monitor_runs_status_scheduled",
        ("status", "scheduled_at"),
    ),
    (
        "monitor_runs",
        "ix_monitor_runs_target_status",
        ("target_kind", "status", "scheduled_at"),
    ),
    (
        "monitor_runs",
        "ix_monitor_runs_task_started",
        ("task_name", "started_at"),
    ),
    (
        "monitor_events",
        "ix_monitor_events_run_sequence",
        ("run_id", "stream_sequence"),
    ),
}


@dataclass
class DashboardV2SchemaSnapshot:
    actual_revision: str | None = None
    tables: set[str] = field(default_factory=set)
    columns: dict[str, set[str]] = field(default_factory=dict)
    generated_columns: set[tuple[str, str]] = field(default_factory=set)
    unique_constraints: set[tuple[str, str, tuple[str, ...]]] = field(
        default_factory=set
    )
    foreign_keys: set[
        tuple[str, tuple[str, ...], str, tuple[str, ...], str]
    ] = field(default_factory=set)
    check_constraints: set[tuple[str, str]] = field(default_factory=set)
    indexes: set[tuple[str, str, tuple[str, ...]]] = field(default_factory=set)
    primary_keys: dict[str, tuple[str, ...]] = field(default_factory=dict)
    partitions: dict[str, str] = field(default_factory=dict)


def _normalize_partition_description(value: object) -> str:
    result = str(value or "").strip()
    if len(result) >= 2 and result[0] == result[-1] == "'":
        result = result[1:-1]
    return result.upper() if result.upper() == "MAXVALUE" else result


def collect_dashboard_v2_schema(connection: Connection) -> DashboardV2SchemaSnapshot:
    inspector = inspect(connection)
    tables = set(inspector.get_table_names())
    snapshot = DashboardV2SchemaSnapshot(tables=tables)
    if "alembic_version" in tables:
        snapshot.actual_revision = connection.scalar(
            text("SELECT version_num FROM alembic_version")
        )
    for table in sorted(EXPECTED_TABLES & tables):
        column_rows = inspector.get_columns(table)
        snapshot.columns[table] = {str(row["name"]) for row in column_rows}
        for row in column_rows:
            computed = row.get("computed")
            if computed and computed.get("persisted") is not False:
                snapshot.generated_columns.add((table, str(row["name"])))
        for row in inspector.get_unique_constraints(table):
            if row.get("name"):
                snapshot.unique_constraints.add(
                    (table, str(row["name"]), tuple(row.get("column_names") or ()))
                )
        for row in inspector.get_foreign_keys(table):
            options = row.get("options") or {}
            ondelete = str(options.get("ondelete") or "RESTRICT").upper()
            snapshot.foreign_keys.add(
                (
                    table,
                    tuple(row.get("constrained_columns") or ()),
                    str(row.get("referred_table") or ""),
                    tuple(row.get("referred_columns") or ()),
                    ondelete,
                )
            )
        for row in inspector.get_indexes(table):
            if row.get("name"):
                snapshot.indexes.add(
                    (table, str(row["name"]), tuple(row.get("column_names") or ()))
                )
        snapshot.primary_keys[table] = tuple(
            inspector.get_pk_constraint(table).get("constrained_columns") or ()
        )

    check_rows = connection.execute(text("""
        SELECT TABLE_NAME, CONSTRAINT_NAME
        FROM information_schema.TABLE_CONSTRAINTS
        WHERE CONSTRAINT_SCHEMA = DATABASE()
          AND CONSTRAINT_TYPE = 'CHECK'
    """)).mappings()
    snapshot.check_constraints = {
        (str(row["TABLE_NAME"]), str(row["CONSTRAINT_NAME"]))
        for row in check_rows
    }
    generated_rows = connection.execute(text("""
        SELECT TABLE_NAME, COLUMN_NAME, EXTRA, GENERATION_EXPRESSION
        FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME = 'hierarchy_parent_history'
          AND COLUMN_NAME = 'active_child_node_id'
    """)).mappings()
    for row in generated_rows:
        if (
            "STORED GENERATED" in str(row["EXTRA"] or "").upper()
            and str(row["GENERATION_EXPRESSION"] or "").strip()
        ):
            snapshot.generated_columns.add(
                (str(row["TABLE_NAME"]), str(row["COLUMN_NAME"]))
            )
    partition_rows = connection.execute(text("""
        SELECT PARTITION_NAME, PARTITION_DESCRIPTION
        FROM information_schema.PARTITIONS
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME = 'metric_snapshot'
          AND PARTITION_NAME IS NOT NULL
    """)).mappings()
    snapshot.partitions = {
        str(row["PARTITION_NAME"]): _normalize_partition_description(
            row["PARTITION_DESCRIPTION"]
        )
        for row in partition_rows
    }
    return snapshot


def _format_named(items: set[tuple[Any, ...]]) -> list[str]:
    return sorted(f"{item[0]}.{item[1]}" for item in items)


def evaluate_dashboard_v2_schema(
    snapshot: DashboardV2SchemaSnapshot,
    *,
    expected_revision: str,
    now: datetime,
) -> dict[str, Any]:
    missing_tables = sorted(EXPECTED_TABLES - snapshot.tables)
    missing_columns = {
        table: sorted(columns - snapshot.columns.get(table, set()))
        for table, columns in EXPECTED_COLUMNS.items()
        if columns - snapshot.columns.get(table, set())
    }
    missing_generated = EXPECTED_GENERATED_COLUMNS - snapshot.generated_columns
    missing_unique = EXPECTED_UNIQUE_CONSTRAINTS - snapshot.unique_constraints
    missing_foreign = EXPECTED_FOREIGN_KEYS - snapshot.foreign_keys
    missing_checks = EXPECTED_CHECK_CONSTRAINTS - snapshot.check_constraints
    missing_indexes = EXPECTED_INDEXES - snapshot.indexes
    snapshot_primary_ok = snapshot.primary_keys.get("metric_snapshot") == (
        "id",
        "collected_at",
    )
    snapshot_has_no_foreign_keys = not any(
        item[0] == "metric_snapshot" for item in snapshot.foreign_keys
    )

    business_day = (
        now.astimezone(_SHANGHAI).date() if now.tzinfo is not None else now.date()
    )
    expected_partitions = {
        f"p{day:%Y%m%d}": (day + timedelta(days=1)).isoformat()
        for offset in range(31)
        if (day := business_day + timedelta(days=offset))
    }
    missing_partitions = sorted(set(expected_partitions) - set(snapshot.partitions))
    invalid_partitions = sorted(
        name
        for name, boundary in expected_partitions.items()
        if name in snapshot.partitions and snapshot.partitions[name] != boundary
    )
    if snapshot.partitions.get("p_future") != "MAXVALUE":
        invalid_partitions.append("p_future")
    partition_ok = not missing_partitions and not invalid_partitions
    current_unique = next(
        (
            item
            for item in EXPECTED_UNIQUE_CONSTRAINTS
            if item[0] == "metric_current"
        ),
        None,
    )
    ok = all(
        (
            not missing_tables,
            snapshot.actual_revision == expected_revision,
            not missing_columns,
            not missing_generated,
            not missing_unique,
            not missing_foreign,
            not missing_checks,
            not missing_indexes,
            snapshot_primary_ok,
            snapshot_has_no_foreign_keys,
            partition_ok,
        )
    )
    return {
        "ok": ok,
        "message": "驾驶舱 V2 结构就绪" if ok else "驾驶舱 V2 结构未就绪",
        "expected_revision": expected_revision,
        "actual_revision": snapshot.actual_revision,
        "missing_tables": missing_tables,
        "missing_columns": missing_columns,
        "missing_hierarchy_node_columns": missing_columns.get("hierarchy_node", []),
        "missing_unique_constraints": _format_named(missing_unique),
        "missing_foreign_keys": sorted(
            f"{item[0]}.{','.join(item[1])}->{item[2]}({','.join(item[3])})"
            f"[{item[4]}]"
            for item in missing_foreign
        ),
        "missing_check_constraints": _format_named(missing_checks),
        "missing_indexes": _format_named(missing_indexes),
        "generated_columns_ok": not missing_generated,
        "metric_current_unique_ok": (
            current_unique is not None
            and current_unique in snapshot.unique_constraints
        ),
        "metric_snapshot_primary_key_ok": snapshot_primary_ok,
        "metric_snapshot_has_no_foreign_keys": snapshot_has_no_foreign_keys,
        "missing_snapshot_partitions": missing_partitions,
        "invalid_snapshot_partitions": sorted(set(invalid_partitions)),
        "metric_snapshot_partition_ok": partition_ok,
        "partition_count": len(snapshot.partitions),
    }


def check_dashboard_v2_schema(*, now: datetime | None = None) -> dict[str, Any]:
    engine = create_dashboard_engine()
    try:
        with engine.connect() as connection:
            snapshot = collect_dashboard_v2_schema(connection)
        return evaluate_dashboard_v2_schema(
            snapshot,
            expected_revision=EXPECTED_REVISION,
            now=now or datetime.now(_SHANGHAI),
        )
    except SQLAlchemyError as exc:
        return {
            "ok": False,
            "message": f"驾驶舱 V2 结构检查失败: {type(exc).__name__}",
            "expected_revision": EXPECTED_REVISION,
        }
    finally:
        engine.dispose()
