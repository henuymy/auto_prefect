from __future__ import annotations

import threading
import time

import pytest

from services.dashboard_collection_service import (
    CollectionTarget,
    DashboardCollectionError,
    collect_metric_rows,
    extract_target_metric_rows,
    extract_structure_observations,
    normalize_max_workers,
)


def test_extracts_grid_manager_structure_observations():
    grid = target(1, "G001", "GRID")
    payload = {
        "reCode": "0000",
        "result": {
            "tableData": [
                {"areaCode": "G001", "areaName": "网格1"},
                {"areaCode": "M001", "areaName": "经理1"},
            ]
        },
    }

    assert extract_structure_observations(grid, payload) == [
        {
            "parent_type": "GRID",
            "parent_code": "G001",
            "child_type": "CHANNEL_MANAGER",
            "child_code": "M001",
            "child_name": "经理1",
        }
    ]


def target(
    target_id: int,
    code: str,
    target_type: str,
) -> CollectionTarget:
    return CollectionTarget(
        id=target_id,
        target_code=code,
        target_name=code,
        target_type=target_type,
        area_id=target_id if target_type != "CHANNEL_MANAGER" else None,
        parent_target_id=None,
        sort_order=target_id,
    )


def payload(*rows):
    return {"reCode": "0000", "result": {"tableData": list(rows)}}


def test_extracts_formal_self_row_and_manager_channels_without_duplicates():
    branch = extract_target_metric_rows(
        target(1, "AQ", "BRANCH"),
        payload(
            {"areaCode": "A", "areaName": "郑州市", "metric": 100},
            {"areaCode": "AQ", "areaName": "中原区", "metric": 20},
            {"areaCode": "AQ01", "areaName": "网格1", "metric": 5},
        ),
        ["metric"],
    )
    manager = extract_target_metric_rows(
        target(2, "M001", "CHANNEL_MANAGER"),
        payload(
            {"areaCode": "M001", "areaName": "经理1", "metric": 10},
            {"areaCode": "C001", "areaName": "渠道1", "metric": 3},
            {"areaCode": "C002", "areaName": "", "metric": 4},
        ),
        ["metric"],
    )

    assert branch == [
        {
            "level_type": "BRANCH",
            "area_code": "AQ",
            "area_name": "中原区",
            "parent_request_code": "AQ",
            "metric": 20,
        }
    ]
    assert manager == [
        {
            "level_type": "CHANNEL",
            "area_code": "C001",
            "area_name": "渠道1",
            "parent_request_code": "M001",
            "metric": 3,
        }
    ]


def test_normalize_workers_uses_production_default_and_hard_cap():
    assert normalize_max_workers(None) == 24
    assert normalize_max_workers(16) == 16
    assert normalize_max_workers(64) == 32


def test_collection_respects_concurrency_and_preserves_target_order():
    targets = [target(index, f"C{index}", "CITY") for index in range(1, 9)]
    active = 0
    peak = 0
    lock = threading.Lock()

    def fetch(item):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        time.sleep(0.01)
        with lock:
            active -= 1
        return payload(
            {
                "areaCode": item.target_code,
                "areaName": item.target_name,
                "metric": item.id,
            }
        )

    result = collect_metric_rows(
        targets,
        ["metric"],
        fetch,
        max_workers=3,
    )

    assert peak == 3
    assert result["request_count"] == 8
    assert [row["metric"] for row in result["rows"]] == list(range(1, 9))


def test_any_target_failure_rejects_the_whole_batch():
    targets = [
        target(1, "A", "CITY"),
        target(2, "AQ", "BRANCH"),
    ]

    def fetch(item):
        if item.target_code == "AQ":
            raise TimeoutError("request timed out")
        return payload(
            {"areaCode": item.target_code, "areaName": item.target_name, "metric": 1}
        )

    with pytest.raises(DashboardCollectionError) as error:
        collect_metric_rows(targets, ["metric"], fetch, max_workers=2)

    assert len(error.value.errors) == 1
    assert error.value.errors[0]["target_code"] == "AQ"


def test_business_failure_code_rejects_target():
    with pytest.raises(DashboardCollectionError, match="reCode=1101"):
        extract_target_metric_rows(
            target(1, "A", "CITY"),
            {"reCode": "1101", "reMsg": "登录失效"},
            ["metric"],
        )


def test_recoverable_manager_failure_can_return_partial_rows():
    targets = [
        target(1, "A", "CITY"),
        target(2, "M001", "CHANNEL_MANAGER"),
    ]

    def fetch(item):
        if item.target_type == "CHANNEL_MANAGER":
            return {"reCode": "1104", "reMsg": "runtime exception"}
        return payload(
            {"areaCode": item.target_code, "areaName": item.target_name, "metric": 1}
        )

    result = collect_metric_rows(
        targets,
        ["metric"],
        fetch,
        max_workers=2,
        allow_recoverable_manager_failures=True,
    )

    assert result["row_count"] == 1
    assert result["recoverable_errors"][0]["target_code"] == "M001"


def test_retryable_business_code_is_retried(monkeypatch):
    from datetime import date

    from services import dashboard_collection_service as service

    responses = [
        {"reCode": "1104", "reMsg": "runtime exception"},
        {"reCode": "0000", "result": {"tableData": []}},
    ]

    class Response:
        status_code = 200

    monkeypatch.setattr(service, "request_report", lambda *args, **kwargs: Response())
    monkeypatch.setattr(
        service,
        "response_json_with_context",
        lambda response: responses.pop(0),
    )
    monkeypatch.setattr(service, "raise_for_status_with_context", lambda response: None)
    monkeypatch.setattr(service.time, "sleep", lambda seconds: None)

    fetch = service.build_platform_fetcher(
        {
            "method": "POST",
            "url": "https://example.test",
            "body_type": "json",
            "data": {},
        },
        {"cookies": []},
        ["metric"],
        date(2026, 6, 11),
        request_retries=1,
    )

    assert fetch(target(1, "A", "CITY"))["reCode"] == "0000"
    assert responses == []


def test_platform_fetcher_supports_month_query_date(monkeypatch):
    from datetime import date

    from services import dashboard_collection_service as service

    captured = {}

    class Response:
        status_code = 200

    def request(session, report, *args, **kwargs):
        captured.update(report["data"])
        return Response()

    monkeypatch.setattr(service, "request_report", request)
    monkeypatch.setattr(
        service,
        "response_json_with_context",
        lambda response: {"reCode": "0000", "result": {"tableData": []}},
    )
    monkeypatch.setattr(service, "raise_for_status_with_context", lambda response: None)

    fetch = service.build_platform_fetcher(
        {
            "method": "POST",
            "url": "https://example.test",
            "body_type": "json",
            "data": {},
        },
        {"cookies": []},
        ["metric"],
        date(2026, 5, 1),
        query_date_formatter=lambda value: value.strftime("%Y%m"),
    )
    fetch(target(1, "A", "CITY"))

    assert captured["queryDate"] == "202605"
