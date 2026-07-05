"""Shared lifecycle and locking for Dashboard V2 collection batches."""

from __future__ import annotations

import logging
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from time import perf_counter
from typing import Any, Callable, Iterator

from sqlalchemy import select
from sqlalchemy.orm import Session

from infrastructure.dashboard_mysql import create_dashboard_engine, dashboard_mysql_lock
from infrastructure.dashboard_run_store import CollectionRunStore
from infrastructure.dashboard_v2_run_store import MySQLV2CollectionRunStore
from models.dashboard_v2 import CollectionRunV2
from services.dashboard_collection_service import CollectionTarget
from services.dashboard_trigger import (
    build_city_ops_login_config,
    load_dashboard_config,
    normalize_trigger_type,
    resolve_project_path,
    sanitize_error,
)
from services.dashboard_v2_hierarchy import load_v2_collection_targets
from services.dashboard_v2_indicator_service import load_v2_metric_indicator_plan
from services.dashboard_v2_orchestrator import collect_validate_metric_rows_v2
from services.method_service import find_stage, load_json
from services.session_manager import file_lock, prepare_session
from services.dashboard_trigger import now_shanghai


@dataclass(frozen=True)
class DashboardV2BatchContext:
    config: dict[str, Any]
    batch_no: str
    query_date: date
    engine: Any
    run_store: CollectionRunStore
    session_result: dict[str, Any]
    targets: list[CollectionTarget]
    indicator_plan: dict[str, Any]
    stage: dict[str, Any]
    lock_result: dict[str, Any]

    def collect_validate(
        self,
        fetch_metrics: Callable[[CollectionTarget], dict[str, Any]],
        *,
        max_workers: int = 24,
        hard_limit: int = 32,
        retry_strategy: str = "affected_grid",
    ) -> dict[str, Any]:
        return collect_validate_metric_rows_v2(
            engine=self.engine,
            run_store=self.run_store,
            batch_no=self.batch_no,
            targets=self.targets,
            indicator_codes=self.indicator_plan["request_codes"],
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
                    "runtime/dashboard/failures",
                )
            ),
            event_logger=self.config.get("event_logger"),
            retry_strategy=retry_strategy,
        )


@contextmanager
def dashboard_v2_batch(
    *,
    config_path: str | Path,
    trigger_type: str,
    force_refresh: bool,
    batch_no: str,
    query_date: date,
    run_type: str,
    event_logger: Any = None,
) -> Iterator[DashboardV2BatchContext]:
    """Hold local/database locks and expose one V2 batch context."""
    dashboard_config, resolved_config_path = load_dashboard_config(config_path)
    normalized_trigger = normalize_trigger_type(trigger_type)
    logger = event_logger or logging.getLogger(__name__)
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
        lock_label="驾驶舱 V2 完整采集锁",
    ) as local_lock:
        engine_started = perf_counter()
        engine = create_dashboard_engine()
        engine_seconds = perf_counter() - engine_started
        run_store = MySQLV2CollectionRunStore(engine)
        record_created = False
        try:
            run_store.create(batch_no, normalized_trigger, run_type=run_type)
            record_created = True
            run_store.update(
                batch_no,
                status="RUNNING",
                phase="SESSION",
                stat_date=query_date,
            )
            login_config = build_city_ops_login_config(
                dashboard_config,
                resolved_config_path.parent,
            )
            session_started = perf_counter()
            session_result = prepare_session(
                login_config,
                base_dir=resolve_project_path("."),
                force_refresh=force_refresh,
                event_logger=event_logger,
            )
            session_seconds = perf_counter() - session_started
            logger.info(
                "驾驶舱 V2 批次前置耗时 batch_no=%s session=%.3fs "
                "session_status=%s",
                batch_no,
                session_seconds,
                session_result.get("status"),
            )
            if session_result.get("status") == "invalid":
                raise RuntimeError(
                    f"V2 city_ops 会话不可用: {session_result.get('reason')}"
                )
            run_store.update(
                batch_no,
                status="RUNNING",
                phase="SESSION_READY",
                session_status=session_result.get("status"),
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
            ) as database_lock:
                recovered = recover_stale_v2_runs(
                    engine,
                    timeout_minutes=int(
                        (dashboard_config.get("retention") or {}).get(
                            "active_run_timeout_minutes", 120
                        )
                        or 120
                    ),
                    exclude_batch_no=batch_no,
                )
                if recovered:
                    logger.warning("V2 已自动收口超时批次 count=%s", recovered)
                targets_started = perf_counter()
                targets = load_v2_collection_targets(engine)
                targets_seconds = perf_counter() - targets_started
                indicators_started = perf_counter()
                indicator_plan = load_v2_metric_indicator_plan(engine)
                indicators_seconds = perf_counter() - indicators_started
                logger.info(
                    "驾驶舱 V2 批次预加载耗时 batch_no=%s engine=%.3fs "
                    "targets=%.3fs indicators=%.3fs target_count=%s "
                    "request_indicator_count=%s store_indicator_count=%s",
                    batch_no,
                    engine_seconds,
                    targets_seconds,
                    indicators_seconds,
                    len(targets),
                    len(indicator_plan.get("request_codes", [])),
                    len(indicator_plan.get("store_codes", [])),
                )
                if not targets:
                    raise RuntimeError("V2 没有启用的请求节点")
                if not indicator_plan["request_codes"]:
                    raise RuntimeError("V2 没有可请求的源指标")
                cookie_dump_path = session_result.get("cookie_dump_path")
                if not cookie_dump_path:
                    raise RuntimeError("V2 会话阶段未返回 cookie_dump_path")
                stage = find_stage(
                    load_json(resolve_project_path(cookie_dump_path)),
                    str(dashboard_config.get("required_stage") or "city_ops"),
                )
                yield DashboardV2BatchContext(
                    config={**dashboard_config, "event_logger": event_logger},
                    batch_no=batch_no,
                    query_date=query_date,
                    engine=engine,
                    run_store=run_store,
                    session_result=session_result,
                    targets=targets,
                    indicator_plan=indicator_plan,
                    stage=stage,
                    lock_result={
                        **local_lock,
                        "database_lock": database_lock,
                    },
                )
        except Exception as exc:
            if record_created:
                try:
                    current = run_store.get(batch_no)
                    if current["status"] != "FAILED":
                        run_store.update(
                            batch_no,
                            status="FAILED",
                            phase=str(getattr(exc, "phase", "PIPELINE")),
                            finished_at=now_shanghai().replace(tzinfo=None),
                            error_type=str(
                                getattr(exc, "error_type", type(exc).__name__)
                            ),
                            error_message={"message": sanitize_error(exc)},
                        )
                except Exception:
                    logger.exception("V2 批次失败状态回写失败")
            raise
        finally:
            run_store.close()
            engine.dispose()


def recover_stale_v2_runs(
    engine: Any,
    *,
    timeout_minutes: int,
    exclude_batch_no: str | None = None,
) -> int:
    """Close stale runs only after the caller owns the global MySQL lock."""
    if timeout_minutes <= 0:
        raise ValueError("timeout_minutes 必须大于 0")
    cutoff = now_shanghai().replace(tzinfo=None) - timedelta(minutes=timeout_minutes)
    with Session(engine) as session, session.begin():
        query = (
            select(CollectionRunV2)
            .where(
                CollectionRunV2.status.in_(["PENDING", "RUNNING"]),
                CollectionRunV2.started_at < cutoff,
            )
            .with_for_update()
        )
        if exclude_batch_no:
            query = query.where(CollectionRunV2.batch_no != exclude_batch_no)
        rows = list(session.scalars(query))
        finished_at = now_shanghai().replace(tzinfo=None)
        for row in rows:
            row.status = "FAILED"
            row.phase = "RECOVER_TIMEOUT"
            row.finished_at = finished_at
            row.error_type = "STALE_RUN_TIMEOUT"
            row.error_message = {
                "message": f"批次超过 {timeout_minutes} 分钟未完成"
            }
        return len(rows)
