"""End-to-end dashboard collection pipeline."""

from __future__ import annotations

import copy
import json
from datetime import date
from pathlib import Path
from typing import Any

from services.dashboard_batch_runner import dashboard_batch
from services.dashboard_collection_orchestrator import (
    metric_rows as _metric_rows,
    naive_shanghai_now as _naive_shanghai_now,
)
from services.dashboard_metric_store import write_metric_batch
from services.dashboard_trigger import (
    generate_batch_no,
    now_shanghai,
    resolve_project_path,
)


def load_collection_report(config_path: str | Path) -> dict[str, Any]:
    resolved = resolve_project_path(config_path)
    config = json.loads(resolved.read_text(encoding="utf-8"))
    reports = [
        item
        for item in config.get("downloads") or []
        if item.get("enabled", True) and item.get("stage") == "city_ops"
    ]
    if len(reports) != 1:
        raise RuntimeError(
            "驾驶舱采集配置必须且只能包含一个启用的 city_ops 请求: "
            f"{resolved}, count={len(reports)}"
        )
    return copy.deepcopy(reports[0])


def execute_dashboard_pipeline(
    config_path: str | Path = "config/dashboard/session.json",
    trigger_type: str = "SCHEDULED",
    force_refresh: bool = False,
    batch_no: str | None = None,
    stat_date: date | None = None,
    event_logger: Any = None,
) -> dict[str, Any]:
    batch_no = batch_no or generate_batch_no()
    query_date = stat_date or now_shanghai().date()
    with dashboard_batch(
        config_path=config_path,
        trigger_type=trigger_type,
        force_refresh=force_refresh,
        batch_no=batch_no,
        query_date=query_date,
        run_type="REALTIME",
        indicator_scope="完整",
        failure_phase="PIPELINE",
        event_logger=event_logger,
    ) as batch:
        dashboard_config = batch.config
        report = load_collection_report(
            dashboard_config.get(
                "collection_report_config_path",
                "config/reports/家客和存量通报.json",
            )
        )
        fetch_payload = batch.build_fetcher(report)
        orchestrated = batch.collect_validate(
            fetch_metrics=fetch_payload,
            fetch_structure=fetch_payload,
        )
        collection_result = orchestrated["collection"]
        validation_result = orchestrated["validation"]

        collected_at = _naive_shanghai_now()
        write_result = write_metric_batch(
            batch.engine,
            batch_no,
            batch.indicator_code,
            _metric_rows(
                validation_result["matched_rows"],
                batch.indicator_code,
            ),
            query_date,
            collected_at,
        )
        return {
            **write_result,
            "trigger_type": batch.session_result["trigger_type"],
            "session_status": batch.session_result.get("session_status"),
            "request_count": collection_result["request_count"],
            "area_count": validation_result["matched_area_count"],
            "row_count": validation_result["matched_row_count"],
            "max_workers": collection_result["max_workers"],
            "fallback_request_count": collection_result["fallback_request_count"],
            "query_date": query_date.isoformat(),
            "query_date_is_today": query_date == now_shanghai().date(),
            "structure_sync": orchestrated["structure_sync"],
            "lock": batch.lock_result,
        }
