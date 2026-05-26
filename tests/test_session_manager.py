import json
import shutil
from pathlib import Path
from uuid import uuid4

from services import session_manager
from services.session_manager import (
    format_probe_validation_error,
    lock_is_stale,
    prepare_session,
    validate_cookie_dump,
    validate_stage_probes,
)


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text=None, url="https://example/probe"):
        self.status_code = status_code
        self._payload = payload
        self.text = json.dumps(payload, ensure_ascii=False) if text is None and payload is not None else (text or "")
        self.content = self.text.encode("utf-8")
        self.url = url

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


class FakeSession:
    responses = []

    def __init__(self):
        self.cookies = {}
        self.headers = {}
        self.trust_env = False

    def request(self, method, url, **kwargs):
        response = self.responses.pop(0)
        response.request_kwargs = kwargs
        response.request_method = method
        return response


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


def test_stage_probe_report_analysis_accepts_return_code_zero(monkeypatch):
    monkeypatch.setattr(session_manager.requests, "Session", FakeSession)
    FakeSession.responses = [FakeResponse(payload={"returnCode": "0", "loginName": "371_yumingyang"})]
    cookie_dump = {
        "stages": [
            {
                "stage": "report_analysis",
                "cookies": [{"name": "ssr-token", "value": "token", "domain": "example.com"}],
            }
        ]
    }

    result = validate_stage_probes(
        cookie_dump,
        ["report_analysis"],
        {
            "report_analysis": {
                "method": "POST",
                "url": "https://example/getLoginName",
                "headers_from_cookies": {"Ssr-Token": "ssr-token"},
                "body_type": "form",
                "data": {},
                "success_json_path": "returnCode",
                "success_value": "0",
            }
        },
    )

    assert result["valid"] is True
    assert result["results"][0]["ok"] is True


def test_stage_probe_city_ops_accepts_recode_0000(monkeypatch):
    monkeypatch.setattr(session_manager.requests, "Session", FakeSession)
    FakeSession.responses = [FakeResponse(payload={"reCode": "0000", "reMsg": "success"})]
    cookie_dump = {
        "stages": [
            {
                "stage": "city_ops",
                "cookies": [{"name": "JSESSIONID", "value": "sid", "domain": "example.com"}],
                "session_storage": {"uapToken": "dynamic-token"},
            }
        ]
    }

    result = validate_stage_probes(
        cookie_dump,
        ["city_ops"],
        {
            "city_ops": {
                "method": "POST",
                "url": "https://example/getUserInfo",
                "headers_from_session_storage": {"uapToken": "uapToken"},
                "body_type": "json",
                "data": {},
                "success_json_path": "reCode",
                "success_value": "0000",
            }
        },
    )

    assert result["valid"] is True
    assert result["results"][0]["ok"] is True


def test_stage_probe_smart_ops_accepts_empty_200(monkeypatch):
    monkeypatch.setattr(session_manager.requests, "Session", FakeSession)
    FakeSession.responses = [FakeResponse(status_code=200, text="")]
    cookie_dump = {
        "stages": [
            {
                "stage": "smart_ops",
                "cookies": [{"name": "JSESSIONID", "value": "sid", "domain": "example.com"}],
                "session_storage": {"zhyyptInfo": json.dumps({"accessToken": "user-info"})},
            }
        ]
    }

    result = validate_stage_probes(
        cookie_dump,
        ["smart_ops"],
        {
            "smart_ops": {
                "method": "GET",
                "url": "https://example/refresh",
                "headers_from_session_storage": {"user-info": "zhyyptInfo.accessToken"},
                "success_status_codes": [200],
                "allow_empty_body": True,
            }
        },
    )

    assert result["valid"] is True
    assert result["results"][0]["ok"] is True


def test_prepare_session_reuses_after_lock_when_probe_recovers(monkeypatch):
    work_dir = make_work_dir()
    try:
        cookie_dump_path = work_dir / "cookie_dump.json"
        write_json(
            cookie_dump_path,
            {
                "stages": [
                    {
                        "stage": "city_ops",
                        "cookies": [{"name": "JSESSIONID", "value": "sid", "domain": "example.com"}],
                        "session_storage": {"uapToken": "dynamic-token"},
                    }
                ]
            },
        )
        monkeypatch.setattr(session_manager.requests, "Session", FakeSession)
        FakeSession.responses = [
            FakeResponse(payload={"reCode": "1101", "reMsg": "单点登录超时，请登录后重新跳转"}),
            FakeResponse(payload={"reCode": "0000", "reMsg": "success"}),
        ]
        login_calls = []
        monkeypatch.setattr(session_manager, "run_login_command", lambda *args, **kwargs: login_calls.append(args[0]) or {"returncode": 0})

        result = prepare_session(
            {
                "cookie_dump_path": str(cookie_dump_path),
                "required_stages": ["city_ops"],
                "login_command": "fake-login",
                "stage_probes": {
                    "city_ops": {
                        "method": "POST",
                        "url": "https://example/getUserInfo",
                        "headers_from_session_storage": {"uapToken": "uapToken"},
                        "body_type": "json",
                        "data": {},
                        "success_json_path": "reCode",
                        "success_value": "0000",
                    }
                },
            }
        )

        assert result["status"] == "reused_after_lock"
        assert login_calls == []
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def test_prepare_session_force_refresh_rechecks_under_lock(monkeypatch):
    work_dir = make_work_dir()
    try:
        cookie_dump_path = work_dir / "cookie_dump.json"
        write_json(
            cookie_dump_path,
            {
                "stages": [
                    {
                        "stage": "city_ops",
                        "cookies": [{"name": "JSESSIONID", "value": "sid", "domain": "example.com"}],
                        "session_storage": {"uapToken": "dynamic-token"},
                    }
                ]
            },
        )
        monkeypatch.setattr(session_manager.requests, "Session", FakeSession)
        FakeSession.responses = [FakeResponse(payload={"reCode": "0000", "reMsg": "success"})]
        login_calls = []
        close_calls = []
        monkeypatch.setattr(session_manager, "run_login_command", lambda *args, **kwargs: login_calls.append(args[0]) or {"returncode": 0})
        monkeypatch.setattr(session_manager, "close_browser_session", lambda *args, **kwargs: close_calls.append(args) or {"stopped_pids": []})

        result = prepare_session(
            {
                "cookie_dump_path": str(cookie_dump_path),
                "required_stages": ["city_ops"],
                "login_command": "fake-login",
                "stage_probes": {
                    "city_ops": {
                        "method": "POST",
                        "url": "https://example/getUserInfo",
                        "headers_from_session_storage": {"uapToken": "uapToken"},
                        "body_type": "json",
                        "data": {},
                        "success_json_path": "reCode",
                        "success_value": "0000",
                    }
                },
            },
            force_refresh=True,
        )

        assert result["status"] == "reused_after_lock"
        assert login_calls == []
        assert close_calls == []
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def test_format_probe_validation_error_is_human_readable():
    message = format_probe_validation_error(
        {
            "valid": False,
            "results": [
                {
                    "stage": "city_ops",
                    "ok": False,
                    "reason": "session_expired",
                    "status_code": 401,
                    "url": "https://example/getUserInfo",
                }
            ],
        }
    )

    assert "city_ops 探活失败" in message
    assert "原因=session_expired" in message
    assert "HTTP=401" in message


def test_lock_is_stale_when_recorded_pid_is_gone(monkeypatch):
    work_dir = make_work_dir()
    try:
        lock_path = work_dir / "login.lock"
        write_json(lock_path, {"pid": 999999})
        monkeypatch.setattr(session_manager, "process_is_running", lambda pid: False)

        assert lock_is_stale(lock_path, stale_seconds=600) is True
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)
