import json

from services.session_broker import StageSessionBroker


def test_broker_persists_requested_stage_and_marks_other_stages_unknown(tmp_path):
    cookie_dump_path = tmp_path / "cookie_dump.json"
    cookie_dump_path.write_text(
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

    broker = StageSessionBroker(
        lambda _config, **_kwargs: {
            "status": "refreshed",
            "cookie_dump_path": str(cookie_dump_path),
            "login_attempt_count": 1,
        },
        base_dir=tmp_path,
    )

    result = broker.ensure({"required_stages": ["city_ops"]})

    assert result["status"] == "refreshed"
    assert (tmp_path / "stages" / "city_ops.json").is_file()
    assert not (tmp_path / "stages" / "report_analysis.json").exists()
    health = json.loads((tmp_path / "stage_health.json").read_text(encoding="utf-8"))
    assert health["city_ops"]["status"] == "healthy"
    assert health["report_analysis"]["status"] == "unknown"
