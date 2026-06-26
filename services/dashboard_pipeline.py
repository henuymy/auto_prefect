"""Dashboard collection pipeline - simplified without report config.

所有采集入口，支持实时、日累计、月累计。
"""

from __future__ import annotations

import logging
import json
from calendar import monthrange
from datetime import date, timedelta
from pathlib import Path
from time import perf_counter
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from services.dashboard_batch_runner import dashboard_batch
from services.dashboard_collection_orchestrator import (
    attach_area_ids_in_session,
    metric_rows,
    naive_shanghai_now,
    sync_structure_in_session,
)
from services.dashboard_custom_indicator_service import (
    compose_store_metric_rows,
    load_metric_indicator_plan,
)
from services.dashboard_metric_store import (
    finalize_metric_run_in_session,
    normalize_metric_rows,
    write_acc_metric_batch_in_session,
    write_metric_batch_in_session,
)
from models.dashboard_collection_run import CollectionRun
from services.dashboard_simple_collection import create_simple_fetcher
from services.dashboard_trigger import (
    generate_batch_no,
    now_shanghai,
)


def month_end(value: date) -> date:
    """获取月末日期"""
    return value.replace(day=monthrange(value.year, value.month)[1])


def execute_dashboard_pipeline(
    config_path: str | Path = "config/dashboard/session.json",
    trigger_type: str = "SCHEDULED",
    force_refresh: bool = False,
    batch_no: str | None = None,
    stat_date: date | None = None,
    event_logger: Any = None,
    period_type: str = "REALTIME",
) -> dict[str, Any]:
    """通用数据驾驶舱采集管道

    参数：
        period_type: REALTIME(实时), DAY_ACC(日累计), MONTH(月累计)

    流程：
        1. 创建采集器（不需要报表配置）
        2. 采集数据
        3. 同步到 area 表 + request_target 表
        4. 验证（简化）
        5. 写入 metric_snapshot + metric_current
        6. 清理过期数据
    """
    normalized_period = str(period_type or "").strip().upper()
    if normalized_period not in {"REALTIME", "DAY_ACC", "MONTH"}:
        raise ValueError(f"period_type 只支持 REALTIME/DAY_ACC/MONTH: {period_type!r}")

    if normalized_period == "REALTIME":
        run_type = "REALTIME"
        batch_prefix = "dashboard-"
        indicator_scope = "完整"
    elif normalized_period == "DAY_ACC":
        run_type = "DAILY"
        batch_prefix = "dashboard-daily-"
        indicator_scope = "累计"
    else:
        run_type = "MONTHLY"
        batch_prefix = "dashboard-monthly-"
        indicator_scope = "累计"

    batch_no = batch_no or generate_batch_no().replace("dashboard-", batch_prefix, 1)

    if stat_date is not None:
        query_date = month_end(stat_date) if normalized_period == "MONTH" else stat_date
    elif normalized_period == "MONTH":
        current_month = now_shanghai().date().replace(day=1)
        query_date = current_month - timedelta(days=1)
    elif normalized_period == "REALTIME":
        query_date = now_shanghai().date()
    else:
        query_date = now_shanghai().date() - timedelta(days=1)

    pipeline_started = perf_counter()

    with dashboard_batch(
        config_path=config_path,
        trigger_type=trigger_type,
        force_refresh=force_refresh,
        batch_no=batch_no,
        query_date=query_date,
        run_type=run_type,
        indicator_scope=indicator_scope,
        failure_phase="PIPELINE",
        event_logger=event_logger,
    ) as batch:
        dashboard_config = batch.config
        indicator_plan = load_metric_indicator_plan(batch.engine)
        request_indicator_codes = indicator_plan["request_codes"]
        store_indicator_codes = indicator_plan["store_codes"]

        fetch_started = perf_counter()
        fetch_metrics = create_simple_fetcher(
            stage=batch.stage,
            indicator_codes=request_indicator_codes,
            query_date=query_date,
            period_type=normalized_period,
            timeout_seconds=int(dashboard_config.get("collection_timeout_seconds", 30) or 30),
            request_retries=int(dashboard_config.get("collection_request_retries", 2) or 0),
            retry_delay_seconds=float(dashboard_config.get("collection_retry_delay_seconds", 0.5) or 0),
        )
        fetcher_setup_seconds = perf_counter() - fetch_started

        orchestrate_started = perf_counter()
        orchestrated = batch.collect_validate_simple(
            fetch_metrics=fetch_metrics,
            max_workers=int(dashboard_config.get("collection_max_workers", 24) or 24),
            hard_limit=int(dashboard_config.get("collection_hard_max_workers", 32) or 32),
            retry_strategy=str(
                dashboard_config.get(
                    "collection_retry_strategy",
                    "affected_grid",
                )
                or "affected_grid"
            ),
        )
        orchestrate_seconds = perf_counter() - orchestrate_started

        collection_result = orchestrated["collection"]
        validation_result = orchestrated["validation"]
        structure_changed = orchestrated.get("structure_changed", False)
        structure_observations = orchestrated.get("structure_observations", [])
        change_plan = orchestrated.get("change_plan") or {}
        structure_change_summary = orchestrated.get("structure_change_summary") or {}
        relation_missing_disable_threshold = int(
            dashboard_config.get("relation_missing_disable_threshold", 2) or 2
        )
        validated_rows = orchestrated.get("validated_rows", validation_result.get("matched_rows", []))
        collected_at = naive_shanghai_now()

        write_started = perf_counter()
        write_timings: dict[str, float] = {}
        session_factory = sessionmaker(bind=batch.engine, expire_on_commit=False, class_=Session)
        with session_factory() as session:
            transaction = session.begin()
            try:
                if structure_changed:
                    stage_started = perf_counter()
                    sync_result = sync_structure_in_session(
                        session,
                        collection_result["rows"],
                        structure_observations,
                        collected_at,
                        change_plan=change_plan,
                        relation_missing_disable_threshold=relation_missing_disable_threshold,
                    )
                    write_timings["structure_sync_seconds"] = perf_counter() - stage_started
                    sync_result["structure_changed"] = True
                    sync_result["structure_observations_count"] = len(structure_observations)
                    sync_result["structure_change_summary"] = structure_change_summary

                    stage_started = perf_counter()
                    rows_for_metrics = attach_area_ids_in_session(
                        session,
                        collection_result["rows"],
                    )
                    write_timings["area_id_attach_seconds"] = perf_counter() - stage_started
                else:
                    sync_result = {
                        "structure_changed": False,
                        "structure_observations_count": len(structure_observations),
                        "structure_change_summary": structure_change_summary,
                    }
                    rows_for_metrics = validated_rows
                    write_timings["structure_sync_seconds"] = 0.0
                    write_timings["area_id_attach_seconds"] = 0.0

                stage_started = perf_counter()
                run_record = session.scalar(
                    select(CollectionRun).where(CollectionRun.batch_no == batch_no)
                )
                if run_record is not None:
                    run_record.structure_change_summary = json.dumps(
                        structure_change_summary,
                        ensure_ascii=False,
                    )
                write_timings["run_record_seconds"] = perf_counter() - stage_started

                stage_started = perf_counter()
                rows_for_metrics = compose_store_metric_rows(
                    rows_for_metrics,
                    indicator_plan["custom_components"],
                )
                normalized_rows_by_indicator = {
                    indicator_code: normalize_metric_rows(
                        metric_rows(rows_for_metrics, indicator_code)
                    )
                    for indicator_code in store_indicator_codes
                }
                write_timings["normalize_seconds"] = perf_counter() - stage_started

                stage_started = perf_counter()
                indicator_results: list[dict[str, Any]] = []
                total_written = 0
                expected_run_type = (
                    "REALTIME"
                    if normalized_period == "REALTIME"
                    else "DAILY"
                    if normalized_period == "DAY_ACC"
                    else "MONTHLY"
                )
                for indicator_code, normalized_metric_rows in normalized_rows_by_indicator.items():
                    if normalized_period == "REALTIME":
                        indicator_result = write_metric_batch_in_session(
                            session,
                            dialect_name=batch.engine.dialect.name,
                            batch_no=batch_no,
                            indicator_code=indicator_code,
                            normalized_rows=normalized_metric_rows,
                            stat_date=query_date,
                            collected_at=collected_at,
                            finalize_run=False,
                        )
                        total_written += indicator_result["snapshot_insert_count"]
                    else:
                        indicator_result = write_acc_metric_batch_in_session(
                            session,
                            dialect_name=batch.engine.dialect.name,
                            batch_no=batch_no,
                            indicator_code=indicator_code,
                            normalized_rows=normalized_metric_rows,
                            stat_date=query_date,
                            collected_at=collected_at,
                            period_type=normalized_period,
                            expected_run_type=expected_run_type,
                            finalize_run=False,
                        )
                        total_written += indicator_result["acc_upsert_count"]
                    indicator_results.append(indicator_result)

                finalize_metric_run_in_session(
                    session,
                    batch_no=batch_no,
                    expected_run_type=expected_run_type,
                    stat_date=query_date,
                    collected_at=collected_at,
                    current_upsert_count=(
                        total_written if normalized_period == "REALTIME" else 0
                    ),
                    snapshot_insert_count=(
                        total_written if normalized_period == "REALTIME" else 0
                    ),
                    acc_upsert_count=(
                        total_written if normalized_period != "REALTIME" else 0
                    ),
                )
                write_result = {
                    "batch_no": batch_no,
                    "indicator_codes": list(store_indicator_codes),
                    "indicator_count": len(store_indicator_codes),
                    "request_indicator_codes": list(request_indicator_codes),
                    "indicator_results": indicator_results,
                    "status": "SUCCESS",
                    "phase": "COMPLETED",
                }
                if normalized_period == "REALTIME":
                    write_result["current_upsert_count"] = total_written
                    write_result["snapshot_insert_count"] = total_written
                else:
                    write_result["acc_upsert_count"] = total_written
                write_timings["metric_write_seconds"] = perf_counter() - stage_started

                stage_started = perf_counter()
                transaction.commit()
                write_timings["commit_seconds"] = perf_counter() - stage_started
            except Exception:
                transaction.rollback()
                raise
        write_seconds = perf_counter() - write_started

        try:
            sync_result["attempt_timings"] = orchestrated.get("attempt_timings", [])
        except Exception as e:
            logging.getLogger(__name__).warning(
                f"结构同步结果整理失败（不影响本次采集结果）: {e}", exc_info=True,
            )

        retention_result = None
        try:
            from sqlalchemy.orm import Session as _Session
            from services.dashboard_retention_service import cleanup_expired_data
            retention_config = dashboard_config.get("retention") or {}
            with _Session(batch.engine) as _ret_session:
                retention_result = cleanup_expired_data(
                    _ret_session,
                    snapshot_retention_days=retention_config.get("snapshot_retention_days", 7),
                    run_retention_days=retention_config.get("run_retention_days", 7),
                    acc_retention_days=retention_config.get("acc_retention_days", 90),
                )
        except Exception:
            logging.getLogger(__name__).warning(
                "数据清理失败（不影响本次采集结果）", exc_info=True,
            )

        logger = event_logger or logging.getLogger(__name__)
        total_seconds = perf_counter() - pipeline_started
        timing = {
            "fetcher_setup_seconds": round(fetcher_setup_seconds, 3),
            "orchestrate_seconds": round(orchestrate_seconds, 3),
            "write_seconds": round(write_seconds, 3),
            "write_timings": {
                key: round(value, 3)
                for key, value in write_timings.items()
            },
            "total_seconds": round(total_seconds, 3),
            "attempts": orchestrated.get("attempts", 1),
            "attempt_timings": orchestrated.get("attempt_timings", []),
        }
        logger.info(
            "驾驶舱批次总耗时 batch_no=%s period=%s total=%.3fs fetcher=%.3fs orchestrate=%.3fs write=%.3fs attempts=%s",
            batch_no,
            normalized_period,
            total_seconds,
            fetcher_setup_seconds,
            orchestrate_seconds,
            write_seconds,
            orchestrated.get("attempts", 1),
        )
        logger.info(
            "驾驶舱写入阶段耗时 batch_no=%s timings=%s",
            batch_no,
            timing["write_timings"],
        )
        if structure_change_summary.get("changed"):
            logger.info(
                "驾驶舱结构变化摘要 batch_no=%s summary=%s",
                batch_no,
                structure_change_summary,
            )

        return {
            **write_result,
            "retention": retention_result,
            "sync": sync_result,
            "timing": timing,
            "trigger_type": batch.session_result["trigger_type"],
            "session_status": batch.session_result.get("session_status"),
            "request_count": collection_result["request_count"],
            "area_count": validation_result["matched_area_count"],
            "row_count": validation_result["matched_row_count"],
            "query_date": query_date.isoformat(),
            "period_type": normalized_period,
            "structure_sync": orchestrated.get("structure_sync", []),
            "lock": batch.lock_result,
        }


def execute_dashboard_realtime_pipeline(
    config_path: str | Path = "config/dashboard/session.json",
    trigger_type: str = "SCHEDULED",
    force_refresh: bool = False,
    batch_no: str | None = None,
    stat_date: date | None = None,
    event_logger: Any = None,
) -> dict[str, Any]:
    """实时采集管道（当天数据）"""
    return execute_dashboard_pipeline(
        config_path=config_path,
        trigger_type=trigger_type,
        force_refresh=force_refresh,
        batch_no=batch_no,
        stat_date=stat_date,
        event_logger=event_logger,
        period_type="REALTIME",
    )


def execute_dashboard_daily_pipeline(
    config_path: str | Path = "config/dashboard/session.json",
    trigger_type: str = "SCHEDULED",
    force_refresh: bool = False,
    batch_no: str | None = None,
    stat_date: date | None = None,
    event_logger: Any = None,
) -> dict[str, Any]:
    """日累计采集管道（昨天的累计数据）"""
    return execute_dashboard_pipeline(
        config_path=config_path,
        trigger_type=trigger_type,
        force_refresh=force_refresh,
        batch_no=batch_no,
        stat_date=stat_date,
        event_logger=event_logger,
        period_type="DAY_ACC",
    )


def execute_dashboard_monthly_pipeline(
    config_path: str | Path = "config/dashboard/session.json",
    trigger_type: str = "SCHEDULED",
    force_refresh: bool = False,
    batch_no: str | None = None,
    stat_date: date | None = None,
    event_logger: Any = None,
) -> dict[str, Any]:
    """月累计采集管道（上月末的累计数据）"""
    return execute_dashboard_pipeline(
        config_path=config_path,
        trigger_type=trigger_type,
        force_refresh=force_refresh,
        batch_no=batch_no,
        stat_date=stat_date,
        event_logger=event_logger,
        period_type="MONTH",
    )
