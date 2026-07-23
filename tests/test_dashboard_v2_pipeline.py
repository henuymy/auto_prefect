from __future__ import annotations

from contextlib import contextmanager
from datetime import date
from types import SimpleNamespace
from unittest.mock import Mock

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


def test_v2_transaction_retries_connection_loss_once_with_a_new_pool(monkeypatch):
    attempts: list[int] = []
    engine = SimpleNamespace(dispose=Mock())
    database_lock = SimpleNamespace(assert_held=Mock())

    def write(**kwargs):
        attempts.append(kwargs["transaction_attempt"])
        if len(attempts) == 1:
            raise operational_error(2013)
        return {"status": "SUCCESS", "transaction_attempt": len(attempts)}

    monkeypatch.setattr(pipeline, "_write_v2_transaction", write)
    monkeypatch.setattr(
        pipeline,
        "_completed_write_result",
        lambda **kwargs: None,
        raising=False,
    )
    monkeypatch.setattr(pipeline.random, "uniform", lambda start, end: 0)
    monkeypatch.setattr(pipeline.time, "sleep", lambda delay: None)

    result = pipeline._write_v2_transaction_with_retry(
        batch=SimpleNamespace(
            batch_no="v2-test",
            engine=engine,
            database_lock=database_lock,
        ),
        orchestrated={},
        period_type="REALTIME",
        query_date=date(2026, 6, 30),
        max_attempts=3,
        logger=SimpleNamespace(warning=lambda *args: None),
    )

    assert attempts == [1, 2]
    assert result["status"] == "SUCCESS"
    database_lock.assert_held.assert_called_once_with()
    engine.dispose.assert_called_once_with()


def test_v2_transaction_recovers_when_connection_loss_followed_a_commit(monkeypatch):
    engine = SimpleNamespace(dispose=Mock())
    database_lock = SimpleNamespace(assert_held=Mock())
    completed = {
        "batch_no": "v2-test",
        "status": "SUCCESS",
        "phase": "COMPLETED",
        "transaction_attempt": 1,
        "current_upsert_count": 42,
        "snapshot_insert_count": 9,
        "acc_upsert_count": 0,
        "write_stats": {"recovered_after_connection_loss": True},
    }
    write = Mock(side_effect=operational_error(2013))

    monkeypatch.setattr(pipeline, "_write_v2_transaction", write)
    monkeypatch.setattr(
        pipeline,
        "_completed_write_result",
        lambda **kwargs: completed,
        raising=False,
    )

    result = pipeline._write_v2_transaction_with_retry(
        batch=SimpleNamespace(
            batch_no="v2-test",
            engine=engine,
            database_lock=database_lock,
        ),
        orchestrated={},
        period_type="REALTIME",
        query_date=date(2026, 6, 30),
        max_attempts=3,
        logger=SimpleNamespace(warning=lambda *args: None),
    )

    assert result == completed
    write.assert_called_once()
    database_lock.assert_held.assert_called_once_with()
    engine.dispose.assert_called_once_with()


def test_v2_transaction_retries_connection_loss_only_once(monkeypatch):
    attempts: list[int] = []
    engine = SimpleNamespace(dispose=Mock())
    database_lock = SimpleNamespace(assert_held=Mock())

    def write(**kwargs):
        attempts.append(kwargs["transaction_attempt"])
        raise operational_error(2013)

    monkeypatch.setattr(pipeline, "_write_v2_transaction", write)
    monkeypatch.setattr(
        pipeline,
        "_completed_write_result",
        lambda **kwargs: None,
        raising=False,
    )
    monkeypatch.setattr(pipeline.random, "uniform", lambda start, end: 0)
    monkeypatch.setattr(pipeline.time, "sleep", lambda delay: None)

    with pytest.raises(OperationalError):
        pipeline._write_v2_transaction_with_retry(
            batch=SimpleNamespace(
                batch_no="v2-test",
                engine=engine,
                database_lock=database_lock,
            ),
            orchestrated={},
            period_type="REALTIME",
            query_date=date(2026, 6, 30),
            max_attempts=3,
            logger=SimpleNamespace(warning=lambda *args: None),
        )

    assert attempts == [1, 2]
    assert engine.dispose.call_count == 1


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


def test_v2_pipeline_restores_detailed_prefect_stage_logs(monkeypatch):
    messages: list[str] = []

    class Logger:
        def info(self, message, *args):
            messages.append(message % args)

        def warning(self, message, *args):
            messages.append(message % args)

    orchestrated = {
        "collection": {"request_count": 13, "row_count": 42},
        "validated_rows": [{"node_id": 1}],
        "validation": {"matched_node_count": 42},
        "attempts": 1,
        "attempt_timings": [{"attempt": 1, "collect_seconds": 0.2}],
        "structure_changed": False,
        "structure_change_summary": {"changed": False},
    }
    batch = SimpleNamespace(
        config={},
        indicator_plan={"request_codes": ["metric"], "store_codes": ["metric"]},
        stage={},
        lock_result={"acquired": True},
        collect_validate=lambda *args, **kwargs: orchestrated,
    )

    @contextmanager
    def fake_batch(**kwargs):
        yield batch

    monkeypatch.setattr(pipeline, "dashboard_v2_batch", fake_batch)
    monkeypatch.setattr(pipeline, "create_simple_fetcher", lambda **kwargs: object())
    monkeypatch.setattr(
        pipeline,
        "_write_v2_transaction_with_retry",
        lambda **kwargs: {
            "batch_no": "dashboard-v2-test",
            "status": "SUCCESS",
            "phase": "COMPLETED",
            "transaction_attempt": 1,
            "current_upsert_count": 42,
            "snapshot_insert_count": 9,
            "write_stats": {"chunk_size": 1000},
        },
    )

    result = pipeline.execute_dashboard_v2_pipeline(
        batch_no="dashboard-v2-test",
        stat_date=date(2026, 7, 5),
        event_logger=Logger(),
    )

    assert result["row_count"] == 42
    assert result["timing"]["attempts"] == 1
    assert any("V2" in message and "total=" in message for message in messages)
    assert any("V2" in message and "stats=" in message for message in messages)
