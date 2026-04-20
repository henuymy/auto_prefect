import json
import shutil
from pathlib import Path
from uuid import uuid4

from services.session_manager import prepare_session, validate_cookie_dump


def write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def make_work_dir():
    work_dir = Path("runtime/test_work") / uuid4().hex
    work_dir.mkdir(parents=True, exist_ok=True)
    return work_dir


def test_validate_cookie_dump_requires_stage():
    cookie_dump = {
        "stages": [
            {
                "stage": "report_analysis",
                "cookies": [{"name": "JSESSIONID", "value": "abc"}],
            }
        ]
    }

    validation = validate_cookie_dump(cookie_dump, ["report_analysis", "data_market"])

    assert validation["valid"] is False
    assert validation["missing_stages"] == ["data_market"]


def test_prepare_session_reuses_existing_cookie_dump():
    work_dir = make_work_dir()
    try:
        cookie_dump_path = work_dir / "cookie_dump.json"
        write_json(
            cookie_dump_path,
            {
                "stages": [
                    {
                        "stage": "report_analysis",
                        "cookies": [{"name": "JSESSIONID", "value": "abc"}],
                    }
                ]
            },
        )

        result = prepare_session(
            {
                "cookie_dump_path": str(cookie_dump_path),
                "required_stages": ["report_analysis"],
                "allow_login": False,
            }
        )

        assert result["status"] == "reused"
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def test_prepare_session_syncs_legacy_cookie_dump():
    work_dir = make_work_dir()
    try:
        legacy_cookie_dump_path = work_dir / "legacy" / "cookie_dump.json"
        cookie_dump_path = work_dir / "runtime" / "cookie_dump.json"
        write_json(
            legacy_cookie_dump_path,
            {
                "stages": [
                    {
                        "stage": "report_analysis",
                        "cookies": [{"name": "JSESSIONID", "value": "abc"}],
                    }
                ]
            },
        )

        result = prepare_session(
            {
                "cookie_dump_path": str(cookie_dump_path),
                "legacy_cookie_dump_path": str(legacy_cookie_dump_path),
                "required_stages": ["report_analysis"],
                "allow_login": False,
            }
        )

        assert result["status"] == "reused"
        assert cookie_dump_path.exists()
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)
