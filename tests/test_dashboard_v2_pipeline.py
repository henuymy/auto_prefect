from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest
from sqlalchemy.exc import OperationalError

from services import dashboard_v2_pipeline as pipeline


def operational_error(code: int) -> OperationalError:
    return OperationalError("statement", {}, Exception(code, "mysql error"))


def test_v2_query_date_normalization(monkeypatch):
    monkeypatch.setattr(
        pipeline,
        "now_shanghai",
        lambda: SimpleNamespace(date=lambda: date(2026, 7, 1)),
    )

    assert pipeline.resolve_v2_query_date("REALTIME", None) == date(2026, 7, 1)
    assert pipeline.resolve_v2_query_date("DAY_ACC", None) == date(2026, 6, 30)
    assert pipeline.resolve_v2_query_date("MONTH", None) == date(2026, 6, 30)
    assert pipeline.resolve_v2_query_date("MONTH", date(2026, 2, 1)) == date(
        2026, 2, 28
    )


def test_v2_transaction_retries_deadlock_with_fresh_attempt(monkeypatch):
    attempts: list[int] = []

    def write(**kwargs):
        attempts.append(kwargs["transaction_attempt"])
        if len(attempts) < 3:
            raise operational_error(1213)
        return {"status": "SUCCESS", "transaction_attempt": len(attempts)}

    monkeypatch.setattr(pipeline, "_write_v2_transaction", write)
    monkeypatch.setattr(pipeline.random, "uniform", lambda start, end: 0)
    monkeypatch.setattr(pipeline.time, "sleep", lambda delay: None)
    result = pipeline._write_v2_transaction_with_retry(
        batch=SimpleNamespace(batch_no="v2-test"),
        orchestrated={},
        period_type="REALTIME",
        query_date=date(2026, 6, 30),
        max_attempts=3,
        logger=SimpleNamespace(warning=lambda *args: None),
    )

    assert attempts == [1, 2, 3]
    assert result["status"] == "SUCCESS"


def test_v2_transaction_does_not_retry_non_lock_error(monkeypatch):
    attempts = 0

    def write(**kwargs):
        nonlocal attempts
        attempts += 1
        raise operational_error(1045)

    monkeypatch.setattr(pipeline, "_write_v2_transaction", write)
    with pytest.raises(OperationalError):
        pipeline._write_v2_transaction_with_retry(
            batch=SimpleNamespace(batch_no="v2-test"),
            orchestrated={},
            period_type="REALTIME",
            query_date=date(2026, 6, 30),
            max_attempts=3,
            logger=SimpleNamespace(warning=lambda *args: None),
        )

    assert attempts == 1


def test_v2_transaction_retry_count_is_bounded():
    with pytest.raises(ValueError, match="1-5"):
        pipeline._write_v2_transaction_with_retry(
            batch=SimpleNamespace(batch_no="v2-test"),
            orchestrated={},
            period_type="REALTIME",
            query_date=date(2026, 6, 30),
            max_attempts=6,
            logger=SimpleNamespace(warning=lambda *args: None),
        )
