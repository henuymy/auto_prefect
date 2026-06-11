"""Daily and monthly cumulative dashboard collection pipeline."""

from __future__ import annotations

import copy
import json
from calendar import monthrange
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from services.dashboard_batch_runner import dashboard_batch
from services.dashboard_collection_orchestrator import (
    metric_rows,
    naive_shanghai_now,
)
from services.dashboard_metric_store import write_acc_metric_batch
from services.dashboard_pipeline import load_collection_report
from services.dashboard_trigger import (
    generate_batch_no,
    now_shanghai,
    resolve_project_path,
)


def load_acc_collection_report(
    config_path: str | Path,
    report_name: str = "日累计",
) -> dict[str, Any]:
    resolved = resolve_project_path(config_path)
    config = json.loads(resolved.read_text(encoding="utf-8"))
    reports = [
        item
        for item in config.get("downloads") or []
        if item.get("enabled", True)
        and item.get("stage") == "city_ops"
        and item.get("name") == report_name
    ]
    if len(reports) != 1:
        raise RuntimeError(
            f"累计配置必须且只能匹配一个启用的 {report_name!r} city_ops 请求: "
            f"{resolved}, count={len(reports)}"
        )
    report = copy.deepcopy(reports[0])
    report["response_mode"] = "json_to_excel"
    return report


def month_end(value: date) -> date:
    return value.replace(day=monthrange(value.year, value.month)[1])


def execute_dashboard_acc_pipeline(
    config_path: str | Path = "config/dashboard/session.json",
    trigger_type: str = "SCHEDULED",
    force_refresh: bool = False,
    batch_no: str | None = None,
    stat_date: date | None = None,
    event_logger: Any = None,
    period_type: str = "DAY_ACC",
) -> dict[str, Any]:
    normalized_period = str(period_type or "").strip().upper()
    if normalized_period not in {"DAY_ACC", "MONTH"}:
        raise ValueError(f"period_type 只支持 DAY_ACC/MONTH: {period_type!r}")
    run_type = "DAILY" if normalized_period == "DAY_ACC" else "MONTHLY"
    batch_prefix = (
        "dashboard-daily-" if normalized_period == "DAY_ACC" else "dashboard-monthly-"
    )
    batch_no = batch_no or generate_batch_no().replace(
        "dashboard-",
        batch_prefix,
        1,
    )
    if stat_date is not None:
        query_date = month_end(stat_date) if normalized_period == "MONTH" else stat_date
    elif normalized_period == "MONTH":
        current_month = now_shanghai().date().replace(day=1)
        query_date = current_month - timedelta(days=1)
    else:
        query_date = now_shanghai().date() - timedelta(days=1)
    with dashboard_batch(
        config_path=config_path,
        trigger_type=trigger_type,
        force_refresh=force_refresh,
        batch_no=batch_no,
        query_date=query_date,
        run_type=run_type,
        indicator_scope="累计",
        failure_phase="ACC_PIPELINE",
        event_logger=event_logger,
    ) as batch:
        dashboard_config = batch.config
        daily_report = load_acc_collection_report(
            dashboard_config.get(
                "daily_report_config_path",
                "config/reports/爱家亲情网营业厅每日盯控.json",
            ),
            str(dashboard_config.get("daily_report_name") or "日累计"),
        )
        daily_report["data"] = copy.deepcopy(daily_report.get("data") or {})
        daily_report["data"]["indType"] = (
            "DD" if normalized_period == "DAY_ACC" else "M"
        )
        realtime_report = load_collection_report(
            dashboard_config.get(
                "collection_report_config_path",
                "config/reports/家客和存量通报.json",
            )
        )
        fetch_daily = batch.build_fetcher(
            daily_report,
            query_date_formatter=(
                (lambda value: value.strftime("%Y%m"))
                if normalized_period == "MONTH"
                else None
            ),
        )
        fetch_structure = batch.build_fetcher(
            realtime_report,
            query_date=now_shanghai().date(),
        )
        orchestrated = batch.collect_validate(
            fetch_metrics=fetch_daily,
            fetch_structure=fetch_structure,
        )
        collection_result = orchestrated["collection"]
        validation_result = orchestrated["validation"]
        collected_at = naive_shanghai_now()
        write_result = write_acc_metric_batch(
            batch.engine,
            batch_no,
            batch.indicator_code,
            metric_rows(
                validation_result["matched_rows"],
                batch.indicator_code,
            ),
            query_date,
            collected_at,
            period_type=normalized_period,
        )
        return {
            **write_result,
            "trigger_type": batch.session_result["trigger_type"],
            "session_status": batch.session_result.get("session_status"),
            "request_count": collection_result["request_count"],
            "area_count": validation_result["matched_area_count"],
            "row_count": validation_result["matched_row_count"],
            "query_date": query_date.isoformat(),
            "period_type": normalized_period,
            "structure_sync": orchestrated["structure_sync"],
            "lock": batch.lock_result,
        }


def execute_dashboard_monthly_pipeline(
    config_path: str | Path = "config/dashboard/session.json",
    trigger_type: str = "SCHEDULED",
    force_refresh: bool = False,
    batch_no: str | None = None,
    stat_date: date | None = None,
    event_logger: Any = None,
) -> dict[str, Any]:
    return execute_dashboard_acc_pipeline(
        config_path=config_path,
        trigger_type=trigger_type,
        force_refresh=force_refresh,
        batch_no=batch_no,
        stat_date=stat_date,
        event_logger=event_logger,
        period_type="MONTH",
    )


def execute_dashboard_daily_pipeline(
    config_path: str | Path = "config/dashboard/session.json",
    trigger_type: str = "SCHEDULED",
    force_refresh: bool = False,
    batch_no: str | None = None,
    stat_date: date | None = None,
    event_logger: Any = None,
) -> dict[str, Any]:
    return execute_dashboard_acc_pipeline(
        config_path=config_path,
        trigger_type=trigger_type,
        force_refresh=force_refresh,
        batch_no=batch_no,
        stat_date=stat_date,
        event_logger=event_logger,
        period_type="DAY_ACC",
    )
