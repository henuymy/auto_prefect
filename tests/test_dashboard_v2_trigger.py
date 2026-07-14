from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from services import dashboard_v2_trigger


def test_resolve_project_path_rebases_logical_runtime_paths(monkeypatch, tmp_path):
    runtime_root = tmp_path / "shared-runtime"
    monkeypatch.setenv("AUTO_NOTIFY_RUNTIME_ROOT", str(runtime_root))

    assert dashboard_v2_trigger.resolve_project_path(
        "runtime/session/locks/dashboard_collection.lock",
        base_dir=tmp_path / "project",
    ) == (runtime_root / "session" / "locks" / "dashboard_collection.lock").resolve()


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False),
        encoding="utf-8",
    )


def make_configs(tmp_path: Path) -> tuple[Path, Path, Path]:
    autologin_path = tmp_path / "autologin.json"
    run_store_dir = tmp_path / "runs"
    lock_path = tmp_path / "dashboard.lock"
    write_json(
        autologin_path,
        {
            "cookie_dump_path": str(tmp_path / "cookie_dump.json"),
            "required_stages": ["report_analysis", "city_ops"],
            "stage_probes": {
                "report_analysis": {"enabled": False},
                "city_ops": {
                    "method": "POST",
                    "url": "https://example/getUserInfo",
                    "body_type": "json",
                },
            },
        },
    )
    dashboard_config_path = tmp_path / "session.json"
    write_json(
        dashboard_config_path,
        {
            "schema_version": 2,
            "autologin_config_path": str(autologin_path),
            "required_stage": "city_ops",
            "run_store_type": "json",
            "collection_lock_path": str(lock_path),
            "collection_lock_wait_seconds": 0.05,
            "collection_lock_poll_seconds": 0.01,
            "collection_lock_stale_seconds": 60,
            "run_store_dir": str(run_store_dir),
        },
    )
    return dashboard_config_path, run_store_dir, lock_path


class MemoryRunStore:
    def __init__(self):
        self.records: dict[str, dict] = {}

    def create(self, batch_no: str, trigger_type: str, run_type: str = "REALTIME") -> dict:
        record = {"batch_no": batch_no, "trigger_type": trigger_type, "run_type": run_type}
        self.records[batch_no] = record
        return record

    def update(self, batch_no: str, **changes) -> dict:
        self.records[batch_no].update(changes)
        return self.records[batch_no]

    def close(self) -> None:
        return None


def test_generate_batch_no_contains_shanghai_timestamp(monkeypatch):
    fixed = datetime(2026, 6, 10, 9, 30, 5, tzinfo=ZoneInfo("Asia/Shanghai"))
    monkeypatch.setattr(dashboard_v2_trigger, "uuid4", lambda: type("U", (), {"hex": "abcdef123456"})())

    assert dashboard_v2_trigger.generate_batch_no(fixed) == "dashboard-20260610-093005-abcdef12"


def test_build_city_ops_login_config_only_keeps_required_probe(tmp_path):
    config_path, _, _ = make_configs(tmp_path)
    dashboard_config, resolved = dashboard_v2_trigger.load_dashboard_config(config_path)

    result = dashboard_v2_trigger.build_city_ops_login_config(
        dashboard_config,
        resolved.parent,
    )

    assert result["required_stages"] == ["city_ops"]
    assert list(result["stage_probes"]) == ["city_ops"]


def test_execute_session_phase_records_success(monkeypatch, tmp_path):
    config_path, _, _ = make_configs(tmp_path)
    run_store = MemoryRunStore()
    monkeypatch.setattr(dashboard_v2_trigger, "build_run_store", lambda _: run_store)
    def prepare_session(config, **_kwargs):
        cookie_path = Path(config["cookie_dump_path"])
        cookie_path.write_text(
            json.dumps({"stages": [{"stage": "city_ops", "cookies": [{"name": "city"}]}]}),
            encoding="utf-8",
        )
        return {"status": "reused", "cookie_dump_path": str(cookie_path)}

    monkeypatch.setattr(dashboard_v2_trigger, "prepare_session", prepare_session)

    result = dashboard_v2_trigger.execute_session_phase(
        config_path=config_path,
        trigger_type="MANUAL",
        batch_no="dashboard-test-success",
    )

    record = run_store.records["dashboard-test-success"]
    assert result["phase"] == "SESSION_READY"
    assert result["session_status"] == "reused"
    assert record["status"] == "SUCCESS"
    assert record["phase"] == "SESSION_READY"


def test_execute_session_phase_records_sanitized_failure(monkeypatch, tmp_path):
    config_path, _, _ = make_configs(tmp_path)
    run_store = MemoryRunStore()
    monkeypatch.setattr(dashboard_v2_trigger, "build_run_store", lambda _: run_store)

    def fail(*args, **kwargs):
        raise RuntimeError("uapToken=secret-value session probe failed")

    monkeypatch.setattr(dashboard_v2_trigger, "prepare_session", fail)

    with pytest.raises(RuntimeError, match="session probe failed"):
        dashboard_v2_trigger.execute_session_phase(
            config_path=config_path,
            trigger_type="SCHEDULED",
            batch_no="dashboard-test-failed",
        )

    record = run_store.records["dashboard-test-failed"]
    assert record["status"] == "FAILED"
    assert record["error_type"] == "RuntimeError"
    assert "secret-value" not in record["error_message"]
    assert "uapToken=***" in record["error_message"]


def test_load_dashboard_config_rejects_non_v2_schema(tmp_path):
    config_path = tmp_path / "session.json"
    write_json(config_path, {"schema_version": 1})

    with pytest.raises(ValueError, match="只支持.*2"):
        dashboard_v2_trigger.load_dashboard_config(config_path)
