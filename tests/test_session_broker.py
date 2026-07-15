import json
import threading
import time
from pathlib import Path

from services.session_broker import StageSessionBroker


def test_broker_persists_requested_stage_and_marks_other_stages_unknown(tmp_path):
    cookie_dump_path = tmp_path / "cookie_dump.json"
    cookie_dump_path.write_text(json.dumps({"stages": [{"stage": "legacy", "cookies": []}]}), encoding="utf-8")
    stage_dir = tmp_path / "stages"
    stage_dir.mkdir()
    (stage_dir / "report_analysis.json").write_text(
        json.dumps({"stage": "report_analysis", "data": {"stage": "report_analysis", "cookies": [{"name": "report"}]}}),
        encoding="utf-8",
    )
    (tmp_path / "stage_health.json").write_text(
        json.dumps({"report_analysis": {"status": "healthy"}}),
        encoding="utf-8",
    )

    def preparer(config, **_kwargs):
        Path(config["cookie_dump_path"]).write_text(
            json.dumps(
                {
                    "stages": [
                        {"stage": "report_analysis", "cookies": [{"name": "report"}]},
                        {"stage": "city_ops", "cookies": [{"name": "city"}]},
                    ]
                }
            ),
            encoding="utf-8",
        )
        return {
            "status": "refreshed",
            "cookie_dump_path": config["cookie_dump_path"],
            "login_attempt_count": 1,
            "validation": {"valid": True},
            "lock": {"lock_path": "login.lock"},
        }

    broker = StageSessionBroker(
        preparer,
        base_dir=tmp_path,
    )

    result = broker.ensure(
        {
            "cookie_dump_path": str(cookie_dump_path),
            "stage_session_dir": str(stage_dir),
            "stage_health_path": str(tmp_path / "stage_health.json"),
            "required_stages": ["city_ops"],
        }
    )

    assert result["status"] == "refreshed"
    assert (tmp_path / "stages" / "city_ops.json").is_file()
    assert (tmp_path / "stages" / "report_analysis.json").is_file()
    health = json.loads((tmp_path / "stage_health.json").read_text(encoding="utf-8"))
    assert health["city_ops"]["status"] == "healthy"
    assert health["report_analysis"]["status"] == "unknown"
    assert result["stage_data"]["city_ops"]["cookies"] == [{"name": "city"}]
    assert result["validation"] == {"valid": True}
    assert result["lock"] == {"lock_path": "login.lock"}
    assert result["login_attempt_count"] == 1
    assert result["cookie_dump_path"] is None
    assert not cookie_dump_path.exists()


def test_broker_supplies_temporary_snapshot_from_healthy_stage_files(tmp_path):
    stage_dir = tmp_path / "stages"
    stage_dir.mkdir()
    (stage_dir / "city_ops.json").write_text(
        json.dumps({"stage": "city_ops", "data": {"stage": "city_ops", "cookies": [{"name": "city"}]}}),
        encoding="utf-8",
    )
    (stage_dir / "report_analysis.json").write_text(
        json.dumps({"stage": "report_analysis", "data": {"stage": "report_analysis", "cookies": [{"name": "report"}]}}),
        encoding="utf-8",
    )
    health_path = tmp_path / "stage_health.json"
    health_path.write_text(
        json.dumps({"city_ops": {"status": "healthy"}, "report_analysis": {"status": "unknown"}}),
        encoding="utf-8",
    )
    cookie_dump_path = tmp_path / "cookie_dump.json"

    def preparer(config, **_kwargs):
        compatibility = json.loads(Path(config["cookie_dump_path"]).read_text(encoding="utf-8"))
        assert [item["stage"] for item in compatibility["stages"]] == ["city_ops"]
        return {"status": "reused", "cookie_dump_path": config["cookie_dump_path"]}

    StageSessionBroker(preparer, base_dir=tmp_path).ensure(
        {
            "cookie_dump_path": str(cookie_dump_path),
            "stage_session_dir": str(stage_dir),
            "stage_health_path": str(health_path),
            "required_stages": ["city_ops"],
        }
    )

    assert not cookie_dump_path.exists()


def test_broker_serializes_stage_state_updates_across_concurrent_callers(tmp_path):
    active_preparers = 0
    peak_active_preparers = 0
    state_lock = threading.Lock()
    start = threading.Barrier(3)
    errors = []

    def preparer(config, **_kwargs):
        nonlocal active_preparers, peak_active_preparers
        with state_lock:
            active_preparers += 1
            peak_active_preparers = max(peak_active_preparers, active_preparers)
        try:
            time.sleep(0.05)
            Path(config["cookie_dump_path"]).write_text(
                json.dumps({"stages": [{"stage": "city_ops", "cookies": [{"name": "city"}]}]}),
                encoding="utf-8",
            )
            return {"status": "reused", "cookie_dump_path": config["cookie_dump_path"]}
        finally:
            with state_lock:
                active_preparers -= 1

    def run_broker():
        try:
            start.wait()
            StageSessionBroker(preparer, base_dir=tmp_path).ensure(
                {"required_stages": ["city_ops"]}
            )
        except Exception as exc:  # pragma: no cover - surfaced by the assertion below
            errors.append(exc)

    threads = [threading.Thread(target=run_broker) for _ in range(2)]
    for thread in threads:
        thread.start()
    start.wait()
    for thread in threads:
        thread.join()

    assert not errors
    assert peak_active_preparers == 1
