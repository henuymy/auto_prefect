from __future__ import annotations

from contextlib import contextmanager
from datetime import date

import pytest

from services import dashboard_batch_runner


class FakeEngine:
    def __init__(self):
        self.disposed = False

    def dispose(self):
        self.disposed = True


class FakeRunStore:
    def __init__(self):
        self.status = "SUCCESS"
        self.updates = []
        self.closed = False

    def update(self, batch_no, **fields):
        self.status = fields.get("status", self.status)
        self.updates.append((batch_no, fields))
        return fields

    def get(self, batch_no):
        return {"batch_no": batch_no, "status": self.status}

    def close(self):
        self.closed = True


def install_batch_dependencies(monkeypatch):
    engine = FakeEngine()
    run_store = FakeRunStore()

    @contextmanager
    def fake_lock(*args, **kwargs):
        yield {"owned_by": "test"}

    monkeypatch.setattr(
        dashboard_batch_runner,
        "load_dashboard_config",
        lambda path: ({}, path),
    )
    monkeypatch.setattr(dashboard_batch_runner, "file_lock", fake_lock)
    monkeypatch.setattr(
        dashboard_batch_runner,
        "execute_session_phase",
        lambda **kwargs: {
            "session_status": "reused",
            "cookie_dump_path": "cookie.json",
        },
    )
    monkeypatch.setattr(
        dashboard_batch_runner,
        "create_dashboard_engine",
        lambda: engine,
    )
    monkeypatch.setattr(
        dashboard_batch_runner,
        "build_run_store",
        lambda config: run_store,
    )
    monkeypatch.setattr(
        dashboard_batch_runner,
        "load_collection_targets",
        lambda current_engine: ["target"],
    )
    monkeypatch.setattr(
        dashboard_batch_runner,
        "load_enabled_indicator_codes",
        lambda current_engine: ["sgs_ajvwdz"],
    )
    monkeypatch.setattr(
        dashboard_batch_runner,
        "load_json",
        lambda path: {"stages": []},
    )
    monkeypatch.setattr(
        dashboard_batch_runner,
        "find_stage",
        lambda payload, stage_name: {"name": stage_name},
    )
    return engine, run_store


def test_dashboard_batch_prepares_context_and_releases_resources(monkeypatch):
    engine, run_store = install_batch_dependencies(monkeypatch)

    with dashboard_batch_runner.dashboard_batch(
        config_path="session.json",
        trigger_type="MANUAL",
        force_refresh=False,
        batch_no="batch-1",
        query_date=date(2026, 6, 11),
        run_type="REALTIME",
        indicator_scope="完整",
        failure_phase="PIPELINE",
    ) as batch:
        assert batch.indicator_code == "sgs_ajvwdz"
        assert batch.stage == {"name": "city_ops"}
        assert batch.lock_result == {"owned_by": "test"}

    assert run_store.closed is True
    assert engine.disposed is True
    assert run_store.updates[0][1]["phase"] == "QUERY_DATE_CHECKED"


def test_dashboard_batch_records_unhandled_failure(monkeypatch):
    engine, run_store = install_batch_dependencies(monkeypatch)

    with pytest.raises(ValueError, match="bad payload"):
        with dashboard_batch_runner.dashboard_batch(
            config_path="session.json",
            trigger_type="SCHEDULED",
            force_refresh=False,
            batch_no="batch-2",
            query_date=date(2026, 6, 10),
            run_type="DAILY",
            indicator_scope="累计",
            failure_phase="ACC_PIPELINE",
        ):
            raise ValueError("bad payload")

    failure = run_store.updates[-1][1]
    assert failure["status"] == "FAILED"
    assert failure["phase"] == "ACC_PIPELINE"
    assert failure["error_type"] == "ValueError"
    assert run_store.closed is True
    assert engine.disposed is True


def test_dashboard_batch_disposes_engine_when_run_store_init_fails(monkeypatch):
    engine, _ = install_batch_dependencies(monkeypatch)
    monkeypatch.setattr(
        dashboard_batch_runner,
        "build_run_store",
        lambda config: (_ for _ in ()).throw(RuntimeError("store unavailable")),
    )

    with pytest.raises(RuntimeError, match="store unavailable"):
        with dashboard_batch_runner.dashboard_batch(
            config_path="session.json",
            trigger_type="MANUAL",
            force_refresh=False,
            batch_no="batch-3",
            query_date=date(2026, 6, 11),
            run_type="REALTIME",
            indicator_scope="完整",
            failure_phase="PIPELINE",
        ):
            pass

    assert engine.disposed is True


def test_batch_context_centralizes_collection_settings(monkeypatch):
    captured = {}

    def fake_build_fetcher(*args, **kwargs):
        captured["fetcher"] = {"args": args, "kwargs": kwargs}
        return "fetcher"

    def fake_collect_validate(**kwargs):
        captured["collection"] = kwargs
        return {"collection": {}, "validation": {}}

    monkeypatch.setattr(
        dashboard_batch_runner,
        "build_platform_fetcher",
        fake_build_fetcher,
    )
    monkeypatch.setattr(
        dashboard_batch_runner,
        "collect_validate_metric_rows",
        fake_collect_validate,
    )
    context = dashboard_batch_runner.DashboardBatchContext(
        config={
            "collection_timeout_seconds": 45,
            "collection_request_retries": 3,
            "collection_retry_delay_seconds": 0.25,
            "collection_max_workers": 20,
            "collection_hard_max_workers": 28,
            "collection_max_channel_fallback_requests": 40,
            "area_anomaly_directory": "runtime/test-anomalies",
        },
        batch_no="batch-4",
        query_date=date(2026, 6, 11),
        engine=FakeEngine(),
        run_store=FakeRunStore(),
        session_result={"trigger_type": "MANUAL"},
        targets=["target"],
        indicator_codes=["sgs_ajvwdz"],
        indicator_code="sgs_ajvwdz",
        stage={"name": "city_ops"},
        lock_result={},
    )

    assert context.build_fetcher({"name": "report"}) == "fetcher"
    context.collect_validate("metric-fetcher", "structure-fetcher")

    fetcher_kwargs = captured["fetcher"]["kwargs"]
    assert fetcher_kwargs["timeout_seconds"] == 45
    assert fetcher_kwargs["request_retries"] == 3
    assert fetcher_kwargs["retry_delay_seconds"] == 0.25
    collection = captured["collection"]
    assert collection["max_workers"] == 20
    assert collection["hard_limit"] == 28
    assert collection["max_fallback_requests"] == 40
