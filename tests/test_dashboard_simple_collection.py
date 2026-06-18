from __future__ import annotations

from datetime import date

import pytest

from services.dashboard_simple_collection import (
    ACC_URL,
    REALTIME_URL,
    build_request_params,
    create_simple_fetcher,
)


def test_simple_fetcher_requires_uaptoken(monkeypatch):
    monkeypatch.setattr(
        "services.dashboard_simple_collection.build_headers",
        lambda report, stage: (_ for _ in ()).throw(RuntimeError("missing uapToken")),
    )

    with pytest.raises(RuntimeError, match="missing uapToken"):
        create_simple_fetcher(
            stage={"cookies": [], "session_storage": {}},
            indicator_codes=["metric"],
            query_date=date(2026, 6, 15),
        )


def test_build_request_params_uses_realtime_shape():
    payload = build_request_params("AQ", ["sgs_ajvwdz"], date(2026, 6, 15), "REALTIME")

    assert payload == {
        "areaId": "AQ",
        "indCodes": "sgs_ajvwdz",
        "diyCodes": "sgs_ajvwdz",
        "areaType": None,
        "queryDate": "20260615",
    }


def test_build_request_params_uses_daily_acc_shape():
    payload = build_request_params("AQ", ["sgs_ajvwdz"], date(2026, 6, 14), "DAY_ACC")

    assert payload == {
        "areaId": "AQ",
        "indCodes": "sgs_ajvwdz",
        "diyCodes": "sgs_ajvwdz",
        "areaType": None,
        "queryDate": "20260614",
        "indType": "DD",
    }


def test_build_request_params_uses_month_acc_shape():
    payload = build_request_params("AQ", ["sgs_ajvwdz"], date(2026, 5, 31), "MONTH")

    assert payload == {
        "areaId": "AQ",
        "indCodes": "sgs_ajvwdz",
        "diyCodes": "sgs_ajvwdz",
        "areaType": None,
        "queryDate": "202605",
        "indType": "M",
    }


def test_simple_fetcher_uses_correct_url_by_period(monkeypatch):
    captured = []

    class Response:
        status_code = 200

    monkeypatch.setattr("services.dashboard_simple_collection.raise_for_status_with_context", lambda response: None)
    monkeypatch.setattr("services.dashboard_simple_collection.response_json_with_context", lambda response: {"reCode": "0000", "result": {"tableData": []}})
    monkeypatch.setattr(
        "services.dashboard_simple_collection.request_report",
        lambda session, report, *args, **kwargs: captured.append(report["url"]) or Response(),
    )

    stage = {"cookies": [], "session_storage": {"uapToken": "token"}}
    realtime_fetch = create_simple_fetcher(stage, ["metric"], date(2026, 6, 15), period_type="REALTIME")
    daily_fetch = create_simple_fetcher(stage, ["metric"], date(2026, 6, 14), period_type="DAY_ACC")
    monthly_fetch = create_simple_fetcher(stage, ["metric"], date(2026, 5, 31), period_type="MONTH")

    target = type("T", (), {"target_code": "AQ"})()
    realtime_fetch(target)
    daily_fetch(target)
    monthly_fetch(target)

    assert captured == [REALTIME_URL, ACC_URL, ACC_URL]
