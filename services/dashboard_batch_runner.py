"""Shared lifecycle for realtime and cumulative dashboard batches."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Callable, Iterator

from sqlalchemy.engine import Engine

from infrastructure.dashboard_run_store import CollectionRunStore
from infrastructure.dashboard_mysql import create_dashboard_engine
from services.dashboard_collection_orchestrator import (
    collect_validate_metric_rows,
    naive_shanghai_now,
)
from services.dashboard_collection_service import (
    CollectionTarget,
    build_platform_fetcher,
    load_collection_targets,
    load_enabled_indicator_codes,
)
from services.dashboard_trigger import (
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
    lock_result: dict[str, Any]

    def build_fetcher(
        self,
        report: dict[str, Any],
        query_date: date | None = None,
        query_date_formatter: Callable[[date], str] | None = None,
    ) -> Callable[[CollectionTarget], dict[str, Any]]:
        return build_platform_fetcher(
            report,
            self.stage,
            self.indicator_codes,
            query_date or self.query_date,
            timeout_seconds=int(
                self.config.get("collection_timeout_seconds", 30) or 30
            ),
            request_retries=int(self.config.get("collection_request_retries", 2) or 0),
            retry_delay_seconds=float(
                self.config.get("collection_retry_delay_seconds", 0.5) or 0
            ),
            query_date_formatter=query_date_formatter,
        )

    def collect_validate(
        self,
        fetch_metrics: Callable[[CollectionTarget], dict[str, Any]],
        fetch_structure: Callable[[CollectionTarget], dict[str, Any]],
    ) -> dict[str, Any]:
        return collect_validate_metric_rows(
            engine=self.engine,
            run_store=self.run_store,
            batch_no=self.batch_no,
            targets=self.targets,
            indicator_codes=self.indicator_codes,
            fetch_metrics=fetch_metrics,
            fetch_structure=fetch_structure,
            max_workers=int(self.config.get("collection_max_workers", 24) or 24),
            hard_limit=int(self.config.get("collection_hard_max_workers", 32) or 32),
            max_fallback_requests=int(
                self.config.get(
                    "collection_max_channel_fallback_requests",
                    50,
                )
                or 50
            ),
            anomaly_directory=resolve_project_path(
                self.config.get(
                    "area_anomaly_directory",
                    "runtime/dashboard/area_anomalies",
                )
            ),
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
        session_result = execute_session_phase(
            config_path=config_path,
            trigger_type=trigger_type,
            force_refresh=force_refresh,
            batch_no=batch_no,
            event_logger=event_logger,
            acquire_collection_lock=False,
            run_type=run_type,
        )
        engine = create_dashboard_engine()
        run_store: CollectionRunStore | None = None
        try:
            run_store = build_run_store(dashboard_config)
            run_store.update(
                batch_no,
                status="RUNNING",
                phase="QUERY_DATE_CHECKED",
                stat_date=query_date,
            )
            targets = load_collection_targets(engine)
            indicator_codes = load_enabled_indicator_codes(engine)
            if len(indicator_codes) != 1:
                raise RuntimeError(
                    f"首版{indicator_scope}批次要求恰好启用一个指标，"
                    f"当前启用数量={len(indicator_codes)}"
                )

            cookie_dump_path = session_result.get("cookie_dump_path")
            if not cookie_dump_path:
                raise RuntimeError("会话阶段未返回 cookie_dump_path")
            stage = find_stage(
                load_json(resolve_project_path(cookie_dump_path)),
                str(dashboard_config.get("required_stage") or "city_ops"),
            )
            yield DashboardBatchContext(
                config=dashboard_config,
                batch_no=batch_no,
                query_date=query_date,
                engine=engine,
                run_store=run_store,
                session_result=session_result,
                targets=targets,
                indicator_codes=indicator_codes,
                indicator_code=indicator_codes[0],
                stage=stage,
                lock_result=lock_result,
            )
        except Exception as exc:
            if run_store is not None:
                try:
                    current = run_store.get(batch_no)
                    if current["status"] != "FAILED":
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
                            error_message=sanitize_error(exc),
                        )
                except Exception:
                    pass
            raise
        finally:
            if run_store is not None:
                run_store.close()
            engine.dispose()
