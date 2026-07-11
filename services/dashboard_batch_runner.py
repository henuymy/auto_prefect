"""Shared lifecycle for realtime and cumulative dashboard batches."""

from __future__ import annotations

import json
import logging
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from time import perf_counter
from typing import Any, Callable, Iterator

from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from infrastructure.dashboard_run_store import CollectionRunStore
from infrastructure.dashboard_mysql import (
    DashboardMySQLLockLease,
    create_dashboard_engine,
    dashboard_mysql_lock,
)
from services.dashboard_collection_orchestrator import (
    collect_validate_metric_rows_simple,
    naive_shanghai_now,
)
from services.dashboard_collection_service import (
    CollectionTarget,
    load_collection_targets,
    load_enabled_indicator_codes,
)
from services.dashboard_failure_report import (
    DEFAULT_FAILURE_DIRECTORY,
    write_dashboard_failure_report,
)
from services.dashboard_retention_service import recover_stale_collection_runs
from services.dashboard_v2_trigger import (
    build_run_store,
    execute_session_phase,
    load_dashboard_config,
    resolve_project_path,
    sanitize_error,
)
from services.method_service import find_stage, load_json
from services.session_manager import file_lock


@dataclass(frozen=True)
class DashboardBatchContext:
    config: dict[str, Any]
    batch_no: str
    query_date: date
    engine: Engine
    run_store: CollectionRunStore
    session_result: dict[str, Any]
    targets: list[CollectionTarget]
    indicator_codes: list[str]
    indicator_code: str
    stage: dict[str, Any]
    database_lock: DashboardMySQLLockLease
    lock_result: dict[str, Any]

    def collect_validate_simple(
        self,
        fetch_metrics: Callable[[CollectionTarget], dict[str, Any]],
        max_workers: int = 24,
        hard_limit: int = 32,
        retry_strategy: str = "affected_grid",
    ) -> dict[str, Any]:
        """简化版采集验证：不需要 fetch_structure，单次采集"""
        return collect_validate_metric_rows_simple(
            engine=self.engine,
            run_store=self.run_store,
            batch_no=self.batch_no,
            targets=self.targets,
            indicator_codes=self.indicator_codes,
            fetch_metrics=fetch_metrics,
            max_workers=max_workers,
            hard_limit=hard_limit,
            anomaly_directory=resolve_project_path(
                self.config.get(
                    "area_anomaly_directory",
                    "runtime/dashboard/area_anomalies",
                )
            ),
            failure_directory=resolve_project_path(
                self.config.get(
                    "failure_report_directory",
                    DEFAULT_FAILURE_DIRECTORY,
                )
            ),
            event_logger=self.config.get("event_logger"),
            retry_strategy=retry_strategy,
        )


@contextmanager
def dashboard_batch(
    *,
    config_path: str | Path,
    trigger_type: str,
    force_refresh: bool,
    batch_no: str,
    query_date: date,
    run_type: str,
    indicator_scope: str,
    failure_phase: str,
    event_logger: Any = None,
) -> Iterator[DashboardBatchContext]:
    """Hold the batch lock and manage common run/session/database resources."""
    dashboard_config, _ = load_dashboard_config(config_path)
    lock_path = resolve_project_path(
        dashboard_config.get(
            "collection_lock_path",
            "runtime/locks/dashboard_collection.lock",
        )
    )

    with file_lock(
        lock_path,
        wait_seconds=float(
            dashboard_config.get("collection_lock_wait_seconds", 5) or 5
        ),
        poll_seconds=float(
            dashboard_config.get("collection_lock_poll_seconds", 1) or 1
        ),
        stale_seconds=float(
            dashboard_config.get("collection_lock_stale_seconds", 1800) or 1800
        ),
        lock_label="驾驶舱完整采集锁",
    ) as lock_result:
        logger = event_logger or logging.getLogger(__name__)
        session_started = perf_counter()
        session_result = execute_session_phase(
            config_path=config_path,
            trigger_type=trigger_type,
            force_refresh=force_refresh,
            batch_no=batch_no,
            event_logger=event_logger,
            acquire_collection_lock=False,
            run_type=run_type,
        )
        session_seconds = perf_counter() - session_started
        logger.info(
            "驾驶舱批次前置耗时 batch_no=%s session=%.3fs session_status=%s",
            batch_no,
            session_seconds,
            session_result.get("session_status"),
        )
        engine_started = perf_counter()
        engine = create_dashboard_engine()
        engine_seconds = perf_counter() - engine_started
        run_store: CollectionRunStore | None = None
        try:
            run_store = build_run_store(dashboard_config)
            run_store.update(
                batch_no,
                status="RUNNING",
                phase="QUERY_DATE_CHECKED",
                stat_date=query_date,
            )
            with dashboard_mysql_lock(
                engine,
                lock_name=str(
                    dashboard_config.get("collection_database_lock_name")
                    or "auto_notify_dashboard_collection"
                ),
                wait_seconds=int(
                    dashboard_config.get("collection_database_lock_wait_seconds", 5)
                    or 5
                ),
                heartbeat_seconds=int(
                    dashboard_config.get(
                        "collection_database_lock_heartbeat_seconds", 30
                    ) or 0
                ),
                idle_timeout_seconds=int(
                    dashboard_config.get(
                        "collection_database_lock_idle_timeout_seconds", 300
                    ) or 0
                ),
            ) as database_lock:
                with Session(engine) as recovery_session:
                    recovered_runs = recover_stale_collection_runs(
                        recovery_session,
                        active_run_timeout_minutes=int(
                            (dashboard_config.get("retention") or {}).get(
                                "active_run_timeout_minutes", 120
                            )
                            or 120
                        ),
                    )
                if recovered_runs:
                    logger.warning(
                        "驾驶舱已自动收口超时批次 count=%s",
                        recovered_runs,
                    )
                targets_started = perf_counter()
                targets = load_collection_targets(engine)
                targets_seconds = perf_counter() - targets_started
                indicators_started = perf_counter()
                indicator_codes = load_enabled_indicator_codes(engine)
                indicators_seconds = perf_counter() - indicators_started
                logger.info(
                    "驾驶舱批次预加载耗时 batch_no=%s engine=%.3fs targets=%.3fs indicators=%.3fs target_count=%s indicator_count=%s",
                    batch_no,
                    engine_seconds,
                    targets_seconds,
                    indicators_seconds,
                    len(targets),
                    len(indicator_codes),
                )
                if not indicator_codes:
                    raise RuntimeError(f"{indicator_scope}批次没有启用的指标")

                cookie_dump_path = session_result.get("cookie_dump_path")
                if not cookie_dump_path:
                    raise RuntimeError("会话阶段未返回 cookie_dump_path")
                stage = find_stage(
                    load_json(resolve_project_path(cookie_dump_path)),
                    str(dashboard_config.get("required_stage") or "city_ops"),
                )
                yield DashboardBatchContext(
                    config={**dashboard_config, "event_logger": event_logger},
                    batch_no=batch_no,
                    query_date=query_date,
                    engine=engine,
                    run_store=run_store,
                    session_result=session_result,
                    targets=targets,
                    indicator_codes=indicator_codes,
                    indicator_code=indicator_codes[0],
                    stage=stage,
                    database_lock=database_lock,
                    lock_result={
                        **lock_result,
                        "database_lock": database_lock.as_dict(),
                    },
                )
        except Exception as exc:
            if run_store is not None:
                try:
                    current = run_store.get(batch_no)
                    if current["status"] != "FAILED":
                        failure_directory = resolve_project_path(
                            dashboard_config.get(
                                "failure_report_directory",
                                DEFAULT_FAILURE_DIRECTORY,
                            )
                        )
                        report_path = write_dashboard_failure_report(
                            failure_directory,
                            batch_no,
                            phase=str(getattr(exc, "phase", failure_phase)),
                            error_type=str(getattr(exc, "error_type", type(exc).__name__)),
                            message=sanitize_error(exc),
                            details={
                                "run_type": run_type,
                                "query_date": query_date.isoformat(),
                            },
                            now_provider=naive_shanghai_now,
                        )
                        store_error = json.dumps(
                            {
                                "message": sanitize_error(exc),
                                "report_path": str(report_path),
                            },
                            ensure_ascii=False,
                        )
                        run_store.update(
                            batch_no,
                            status="FAILED",
                            phase=getattr(exc, "phase", failure_phase),
                            finished_at=naive_shanghai_now(),
                            error_type=getattr(
                                exc,
                                "error_type",
                                type(exc).__name__,
                            ),
                            error_message=store_error,
                        )
                except Exception:
                    pass
            raise
        finally:
            if run_store is not None:
                run_store.close()
            engine.dispose()
