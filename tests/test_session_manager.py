import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path
from uuid import uuid4

import pytest

from services import session_manager
from services.session_manager import (
    SessionInfrastructureError,
    classify_probe_validation,
    expand_login_command,
    format_cookie_validation,
    format_probe_validation_error,
    lock_is_stale,
    prepare_session,
    prepare_session_from_config,
    validate_cookie_dump,
    validate_stage_probes,
)


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text=None, url="https://example/probe", headers=None):
        self.status_code = status_code
        self._payload = payload
        self.text = json.dumps(payload, ensure_ascii=False) if text is None and payload is not None else (text or "")
        self.content = self.text.encode("utf-8")
        self.url = url
        self.headers = headers or {}

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


class FakeSession:
    responses = []
    request_calls = []

    def __init__(self):
        self.cookies = {}
        self.headers = {}
        self.trust_env = False

    def request(self, method, url, **kwargs):
        self.request_calls.append(
            {"method": method, "url": url, "kwargs": kwargs}
        )
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        response.request_kwargs = kwargs
        response.request_method = method
        return response


def write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def make_work_dir():
    return Path(tempfile.mkdtemp(prefix="auto_notify_session_test_"))


def valid_city_ops_cookie_dump():
    return {
        "stages": [
            {
                "stage": "city_ops",
                "cookies": [{"name": "JSESSIONID", "value": "sid", "domain": "example.com"}],
                "session_storage": {"uapToken": "token"},
            }
        ]
    }


def session_config(cookie_dump_path, **overrides):
    config = {
        "cookie_dump_path": str(cookie_dump_path),
        "required_stages": ["city_ops"],
        "login_command": "fake-login",
        "login_max_attempts": 2,
        "login_retry_delay_seconds": 0,
        "stage_probes": {"city_ops": {"method": "POST", "url": "https://example/getUserInfo"}},
    }
    config.update(overrides)
    return config


def test_expand_login_command_uses_current_python_executable(monkeypatch):
    monkeypatch.setattr(session_manager.sys, "executable", r"C:\Program Files\Python\python.exe")

    command = expand_login_command('{python_executable} -c "print(1)"')

    assert "{python_executable}" not in command
    assert '"C:\\Program Files\\Python\\python.exe"' in command


def test_format_cookie_validation_excludes_available_stages():
    message = format_cookie_validation(
        {
            "valid": False,
            "available_stages": ["ngboss_main", "city_ops"],
            "missing_stages": ["report_analysis"],
            "expired_or_empty_stages": ["city_ops"],
            "too_old": True,
        }
    )

    assert message == (
        "missing_stages=['report_analysis']，"
        "expired_or_empty_stages=['city_ops']，too_old=True"
    )
    assert "available_stages" not in message



def test_cookie_snapshot_payload_and_hash_come_from_one_read(monkeypatch, tmp_path):
    cookie_path = tmp_path / "cookie.json"
    original = valid_city_ops_cookie_dump()
    original["generated_at"] = "original"
    replacement = valid_city_ops_cookie_dump()
    replacement["generated_at"] = "replacement"
    write_json(cookie_path, original)
    original_bytes = cookie_path.read_bytes()
    original_read_bytes = Path.read_bytes

    def replace_after_read(path):
        content = original_read_bytes(path)
        if path.resolve() == cookie_path.resolve():
            write_json(cookie_path, replacement)
        return content

    monkeypatch.setattr(Path, "read_bytes", replace_after_read)

    payload, cookie_hash = session_manager.load_cookie_snapshot_if_exists(cookie_path)

    assert payload["generated_at"] == "original"
    assert cookie_hash == hashlib.sha256(original_bytes).hexdigest()
    assert json.loads(cookie_path.read_text(encoding="utf-8"))["generated_at"] == "replacement"


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


def test_merge_cookie_dump_stages_keeps_unrefreshed_stages():
    existing = {
        "generated_at": "old",
        "stages": [
            {"stage": "report_analysis", "cookies": [{"value": "report-old"}]},
            {"stage": "city_ops", "cookies": [{"value": "city-old"}]},
        ],
    }
    refreshed = {
        "generated_at": "new",
        "stages": [
            {"stage": "city_ops", "cookies": [{"value": "city-new"}]},
        ],
    }

    merged = session_manager.merge_cookie_dump_stages(existing, refreshed)

    assert merged["generated_at"] == "new"
    assert [stage["stage"] for stage in merged["stages"]] == [
        "report_analysis",
        "city_ops",
    ]
    assert merged["stages"][1]["cookies"][0]["value"] == "city-new"


def test_scoped_login_refresh_preserves_other_shared_stages(monkeypatch, tmp_path):
    cookie_path = tmp_path / "cookie.json"
    existing = valid_city_ops_cookie_dump()
    existing["stages"].insert(
        0,
        {
            "stage": "report_analysis",
            "cookies": [{"name": "JSESSIONID", "value": "report-old"}],
        },
    )
    write_json(cookie_path, existing)

    refreshed = valid_city_ops_cookie_dump()
    refreshed["stages"][0]["cookies"][0]["value"] = "city-new"
    probe_results = iter(
        [
            {"valid": False, "results": [{"stage": "city_ops", "ok": False, "status_code": 401}]},
            {"valid": False, "results": [{"stage": "city_ops", "ok": False, "status_code": 401}]},
            {"valid": True, "results": [{"stage": "city_ops", "ok": True}]},
        ]
    )
    monkeypatch.setattr(
        session_manager,
        "validate_stage_probes",
        lambda *_args, **_kwargs: next(probe_results),
    )
    monkeypatch.setattr(
        session_manager,
        "close_browser_session",
        lambda *_args, **_kwargs: {"stopped_pids": []},
    )
    monkeypatch.setattr(
        session_manager,
        "run_login_command",
        lambda _command, **kwargs: write_json(
            Path(kwargs["env"]["AUTO_NOTIFY_COOKIE_DUMP_PATH"]), refreshed
        )
        or {"returncode": 0},
    )

    assert prepare_session(session_config(cookie_path))["status"] == "refreshed"

    published = json.loads(cookie_path.read_text(encoding="utf-8"))
    assert [stage["stage"] for stage in published["stages"]] == [
        "report_analysis",
        "city_ops",
    ]
    assert published["stages"][0]["cookies"][0]["value"] == "report-old"
    assert published["stages"][1]["cookies"][0]["value"] == "city-new"


def test_login_redirect_response_is_session_expired():
    response = FakeResponse(
        status_code=302,
        headers={"Location": "/uac/web3/jsp/login/login.jsp"},
    )

    assert session_manager.response_has_session_expired(response) is True


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


def test_prepare_session_from_config_uses_runtime_root_for_session_state(monkeypatch):
    work_dir = make_work_dir()
    try:
        config_path = work_dir / "config" / "modules" / "autologin.json"
        cookie_dump_path = work_dir / "shared" / "session" / "cookie_dump.json"
        write_json(
            config_path,
            {
                "cookie_dump_path": "session/cookie_dump.json",
                "stage_session_dir": "session/stages",
                "stage_health_path": "session/stage_health.json",
                "required_stages": ["report_analysis"],
                "allow_login": False,
            },
        )
        write_json(
            work_dir / "shared" / "session" / "stages" / "report_analysis.json",
            {"stage": "report_analysis", "data": {"stage": "report_analysis", "cookies": [{"name": "sid", "value": "x"}]}},
        )
        write_json(work_dir / "shared" / "session" / "stage_health.json", {"report_analysis": {"status": "healthy"}})
        monkeypatch.setenv("AUTO_NOTIFY_RUNTIME_ROOT", str(work_dir / "shared"))

        result = prepare_session_from_config(
            "config/modules/autologin.json",
            base_dir=work_dir,
        )

        assert result["status"] == "reused"
        assert result["cookie_dump_path"] is None
        assert not cookie_dump_path.exists()
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def test_prepare_session_from_config_rebases_runtime_state(monkeypatch, tmp_path):
    config_path = tmp_path / "config" / "modules" / "autologin.json"
    shared_cookie_path = tmp_path / "shared" / "session" / "cookie_dump.json"
    write_json(
        config_path,
        {
                "cookie_dump_path": "session/cookie_dump.json",
                "stage_session_dir": "session/stages",
                "stage_health_path": "session/stage_health.json",
            "required_stages": ["report_analysis"],
            "allow_login": False,
        },
    )
    write_json(
        tmp_path / "shared" / "session" / "stages" / "report_analysis.json",
        {"stage": "report_analysis", "data": {"stage": "report_analysis", "cookies": [{"name": "sid", "value": "x"}]}},
    )
    write_json(tmp_path / "shared" / "session" / "stage_health.json", {"report_analysis": {"status": "healthy"}})
    monkeypatch.setenv("AUTO_NOTIFY_RUNTIME_ROOT", str(tmp_path / "shared"))

    result = prepare_session_from_config(
        "config/modules/autologin.json", base_dir=tmp_path
    )

    assert result["status"] == "reused"
    assert result["cookie_dump_path"] is None
    assert not shared_cookie_path.exists()


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


def test_stage_probe_resolves_dynamic_date_in_request_data(monkeypatch):
    monkeypatch.setattr(session_manager.requests, "Session", FakeSession)
    FakeSession.request_calls = []
    FakeSession.responses = [FakeResponse(payload={"reCode": "0000"})]

    result = validate_stage_probes(
        valid_city_ops_cookie_dump(),
        ["city_ops"],
        {
            "city_ops": {
                "method": "POST",
                "url": "https://example/getIndexByReal",
                "headers_from_session_storage": {"Uaptoken": "uapToken"},
                "body_type": "json",
                "data": {
                    "indCode": "sgs_ajvwdz",
                    "areaId": "AQ",
                    "queryDate": "${today_yyyymmdd}",
                },
                "success_json_path": "reCode",
                "success_value": "0000",
            }
        },
    )

    assert result["valid"] is True
    assert FakeSession.request_calls[0]["kwargs"]["json"]["queryDate"] != "${today_yyyymmdd}"
    assert FakeSession.request_calls[0]["kwargs"]["json"]["queryDate"].isdigit()
    assert len(FakeSession.request_calls[0]["kwargs"]["json"]["queryDate"]) == 8


def test_stage_probe_retries_one_transient_request_failure(monkeypatch):
    monkeypatch.setattr(session_manager.requests, "Session", FakeSession)
    FakeSession.request_calls = []
    FakeSession.responses = [
        session_manager.requests.exceptions.ReadTimeout("secret detail"),
        FakeResponse(payload={"reCode": "0000", "reMsg": "success"}),
    ]
    sleeps = []
    monkeypatch.setattr(session_manager.time, "sleep", sleeps.append)

    result = validate_stage_probes(
        valid_city_ops_cookie_dump(),
        ["city_ops"],
        {
            "city_ops": {
                "method": "POST",
                "url": "https://example/getUserInfo",
                "connect_timeout_seconds": 2,
                "read_timeout_seconds": 5,
                "headers_from_session_storage": {"uapToken": "uapToken"},
                "body_type": "json",
                "data": {},
                "success_json_path": "reCode",
                "success_value": "0000",
            }
        },
    )

    assert result["valid"] is True
    assert len(FakeSession.request_calls) == 2
    assert sleeps == [0.5]
    assert FakeSession.request_calls[0]["kwargs"]["timeout"] == (2, 5)


def test_stage_probe_reports_safe_error_after_one_retry(monkeypatch):
    monkeypatch.setattr(session_manager.requests, "Session", FakeSession)
    FakeSession.request_calls = []
    FakeSession.responses = [
        session_manager.requests.exceptions.ConnectTimeout("token=secret"),
        session_manager.requests.exceptions.ConnectTimeout("token=secret"),
    ]
    monkeypatch.setattr(session_manager.time, "sleep", lambda _seconds: None)

    result = validate_stage_probes(
        valid_city_ops_cookie_dump(),
        ["city_ops"],
        {
            "city_ops": {
                "method": "POST",
                "url": "https://example/getUserInfo",
                "connect_timeout_seconds": 2,
                "read_timeout_seconds": 5,
            }
        },
    )

    assert result["valid"] is False
    assert len(FakeSession.request_calls) == 2
    assert result["results"][0]["error"] == "ConnectTimeout"
    assert "secret" not in repr(result)


def test_stage_probe_uses_ordered_fallbacks_after_primary_request_failures(monkeypatch):
    monkeypatch.setattr(session_manager.requests, "Session", FakeSession)
    FakeSession.request_calls = []
    FakeSession.responses = [
        session_manager.requests.exceptions.ReadTimeout("secret detail"),
        session_manager.requests.exceptions.ReadTimeout("secret detail"),
        FakeResponse(status_code=503, payload={"reCode": "503"}),
        FakeResponse(payload={"reCode": "0000"}),
    ]
    sleeps = []
    monkeypatch.setattr(session_manager.time, "sleep", sleeps.append)

    result = validate_stage_probes(
        valid_city_ops_cookie_dump(),
        ["city_ops"],
        {
            "city_ops": {
                "method": "POST",
                "url": "https://example/getUserInfo",
                "headers_from_session_storage": {"Uaptoken": "uapToken"},
                "body_type": "json",
                "data": {},
                "success_json_path": "reCode",
                "success_value": "0000",
                "fallback_probes": [
                    {
                        "method": "POST",
                        "url": "https://example/getLevelAreaList",
                        "headers_from_session_storage": {"Uaptoken": "uapToken"},
                        "body_type": "json",
                        "data": {"areaLevel": "3", "areaId": "AQ"},
                        "success_json_path": "reCode",
                        "success_value": "0000",
                    },
                    {
                        "method": "POST",
                        "url": "https://example/getSecondaryAreaList",
                        "headers_from_session_storage": {"Uaptoken": "uapToken"},
                        "body_type": "json",
                        "data": {"areaLevel": "2", "areaId": "A"},
                        "success_json_path": "reCode",
                        "success_value": "0000",
                    }
                ],
            }
        },
    )

    assert result["valid"] is True
    assert result["results"] == [{"stage": "city_ops", "enabled": True, "url": "https://example/probe", "status_code": 200, "ok": True, "fallback_used": True}]
    assert [call["url"] for call in FakeSession.request_calls] == [
        "https://example/getUserInfo",
        "https://example/getUserInfo",
        "https://example/getLevelAreaList",
        "https://example/getSecondaryAreaList",
    ]
    assert sleeps == [0.5]
    assert "secret" not in repr(result)


def test_stage_probe_does_not_retry_authentication_response(monkeypatch):
    monkeypatch.setattr(session_manager.requests, "Session", FakeSession)
    FakeSession.request_calls = []
    FakeSession.responses = [FakeResponse(status_code=401, payload={"reCode": "401"})]

    result = validate_stage_probes(
        valid_city_ops_cookie_dump(),
        ["city_ops"],
        {
            "city_ops": {
                "method": "POST",
                "url": "https://example/getUserInfo",
                "connect_timeout_seconds": 2,
                "read_timeout_seconds": 5,
            }
        },
    )

    assert result["results"][0]["reason"] == "session_expired"
    assert len(FakeSession.request_calls) == 1


def test_stage_probe_city_ops_requires_all_configured_probes(monkeypatch):
    monkeypatch.setattr(session_manager.requests, "Session", FakeSession)
    FakeSession.responses = [
        FakeResponse(payload={"reCode": "0000"}),
        FakeResponse(payload={"reCode": "0000"}),
    ]
    cookie_dump = {
        "stages": [
            {
                "stage": "city_ops",
                "cookies": [{"name": "JSESSIONID", "value": "sid", "domain": "example.com"}],
                "session_storage": {"uapToken": "dynamic-token"},
            }
        ]
    }
    probe = {
        "method": "POST",
        "headers_from_session_storage": {"uapToken": "uapToken"},
        "body_type": "json",
        "data": {},
        "success_json_path": "reCode",
        "success_value": "0000",
    }

    result = validate_stage_probes(
        cookie_dump,
        ["city_ops"],
        {
            "city_ops": {
                "probes": [
                    {**probe, "url": "https://example/getUserInfo"},
                    {**probe, "url": "https://example/getAreaList"},
                ]
            }
        },
    )

    assert result["valid"] is True
    assert [item["ok"] for item in result["results"]] == [True, True]


def test_stage_probe_smart_ops_accepts_region_lookup_response(monkeypatch):
    monkeypatch.setattr(session_manager.requests, "Session", FakeSession)
    FakeSession.responses = [
        FakeResponse(
            payload={
                "header": {"rspcode": "0000", "rspdesc": "请求成功"},
                "response": {"areaId": "371", "areaName": "郑州"},
            }
        )
    ]
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
                "method": "POST",
                "url": "https://example/getNameById",
                "headers": {"Content-Type": "application/json"},
                "headers_from_session_storage": {"user-info": "zhyyptInfo.accessToken"},
                "body_type": "json",
                "data": {"area": "371"},
                "success_json_path": "header.rspcode",
                "success_value": "0000",
            }
        },
    )

    assert result["valid"] is True
    assert result["results"][0]["ok"] is True
    assert FakeSession.responses == []


def test_smart_ops_probe_config_uses_region_lookup():
    config_path = Path(__file__).resolve().parents[1] / "config" / "modules" / "autologin.json"
    probe = json.loads(config_path.read_text(encoding="utf-8"))["stage_probes"]["smart_ops"]

    assert probe["method"] == "POST"
    assert probe["url"].endswith("/zhyypt/smop/dszzBranch/regions/getNameById")
    assert probe["headers"]["Content-Type"] == "application/json"
    assert probe["headers_from_session_storage"] == {
        "user-info": "zhyyptInfo.accessToken"
    }
    assert probe["body_type"] == "json"
    assert probe["data"] == {"area": "371"}
    assert probe["success_json_path"] == "header.rspcode"
    assert probe["success_value"] == "0000"


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


def test_prepare_session_does_not_login_for_infrastructure_probe_failure(monkeypatch):
    work_dir = make_work_dir()
    try:
        cookie_dump_path = work_dir / "cookie_dump.json"
        write_json(cookie_dump_path, valid_city_ops_cookie_dump())
        monkeypatch.setattr(
            session_manager,
            "validate_stage_probes",
            lambda *_args, **_kwargs: {
                "valid": False,
                "results": [
                    {
                        "stage": "city_ops",
                        "ok": False,
                        "reason": "probe_error",
                        "error": "ConnectTimeout",
                    }
                ],
            },
        )
        login_calls = []
        monkeypatch.setattr(
            session_manager,
            "run_login_command",
            lambda *args, **kwargs: login_calls.append(args) or {},
        )
        with pytest.raises(SessionInfrastructureError, match="city_ops"):
            prepare_session(session_config(cookie_dump_path))

        assert login_calls == []
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def test_prepare_session_authentication_failure_logs_in_immediately(monkeypatch):
    work_dir = make_work_dir()
    try:
        cookie_dump_path = work_dir / "cookie_dump.json"
        write_json(cookie_dump_path, valid_city_ops_cookie_dump())
        probe_results = iter(
            [
                {"valid": False, "results": [{"stage": "city_ops", "ok": False, "status_code": 401}]},
                {"valid": False, "results": [{"stage": "city_ops", "ok": False, "status_code": 401}]},
                {"valid": True, "results": [{"stage": "city_ops", "ok": True}]},
            ]
        )
        monkeypatch.setattr(session_manager, "validate_stage_probes", lambda *_args, **_kwargs: next(probe_results))
        login_calls = []
        def fake_login(*args, **kwargs):
            login_calls.append(args)
            write_json(Path(kwargs["env"]["AUTO_NOTIFY_COOKIE_DUMP_PATH"]), valid_city_ops_cookie_dump())
            return {"returncode": 0}

        monkeypatch.setattr(session_manager, "run_login_command", fake_login)
        monkeypatch.setattr(session_manager, "close_browser_session", lambda *_args, **_kwargs: {"stopped_pids": []})

        result = prepare_session(session_config(cookie_dump_path))

        assert result["status"] == "refreshed"
        assert len(login_calls) == 1
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def test_prepare_session_success_result_does_not_include_login_command(monkeypatch):
    work_dir = make_work_dir()
    try:
        cookie_dump_path = work_dir / "cookie_dump.json"
        write_json(cookie_dump_path, valid_city_ops_cookie_dump())
        probe_results = iter(
            [
                {"valid": False, "results": [{"stage": "city_ops", "ok": False, "status_code": 401}]},
                {"valid": False, "results": [{"stage": "city_ops", "ok": False, "status_code": 401}]},
                {"valid": True, "results": [{"stage": "city_ops", "ok": True}]},
            ]
        )
        monkeypatch.setattr(
            session_manager,
            "validate_stage_probes",
            lambda *_args, **_kwargs: next(probe_results),
        )

        def fake_login(*_args, **kwargs):
            write_json(
                Path(kwargs["env"]["AUTO_NOTIFY_COOKIE_DUMP_PATH"]),
                valid_city_ops_cookie_dump(),
            )
            return {
                "command": "python login.py --password raw-password",
                "returncode": 0,
            }

        monkeypatch.setattr(session_manager, "run_login_command", fake_login)
        monkeypatch.setattr(
            session_manager,
            "close_browser_session",
            lambda *_args, **_kwargs: {"stopped_pids": []},
        )

        result = prepare_session(session_config(cookie_dump_path))

        assert result["login"] == {"returncode": 0}
        assert "command" not in result["login"]
        assert "raw-password" not in repr(result)
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def test_prepare_session_does_not_login_when_lock_recheck_finds_infrastructure_failure(monkeypatch):
    work_dir = make_work_dir()
    try:
        cookie_dump_path = work_dir / "cookie_dump.json"
        write_json(cookie_dump_path, valid_city_ops_cookie_dump())
        probe_results = iter(
            [
                {"valid": False, "results": [{"stage": "city_ops", "ok": False, "status_code": 401}]},
                {
                    "valid": False,
                    "results": [
                        {
                            "stage": "city_ops",
                            "ok": False,
                            "reason": "probe_error",
                            "error": "ConnectTimeout",
                        }
                    ],
                },
            ]
        )
        monkeypatch.setattr(session_manager, "validate_stage_probes", lambda *_args, **_kwargs: next(probe_results))
        login_calls = []
        monkeypatch.setattr(
            session_manager,
            "run_login_command",
            lambda *args, **kwargs: login_calls.append(args) or {},
        )
        monkeypatch.setattr(
            session_manager,
            "run_login_with_retry",
            lambda login_attempt, **_kwargs: login_attempt(),
        )

        with pytest.raises(SessionInfrastructureError, match="city_ops"):
            prepare_session(session_config(cookie_dump_path))

        assert login_calls == []
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("login_max_attempts", 3),
        ("login_max_attempts", 0),
        ("login_retry_delay_seconds", 30),
        ("login_retry_delay_seconds", 60),
    ],
)
def test_prepare_session_rejects_non_production_retry_configuration(key, value):
    config = session_config(Path(tempfile.gettempdir()) / f"unused-cookie-{uuid4().hex}.json")
    config[key] = value
    config["allow_login"] = False

    with pytest.raises(ValueError, match=key):
        prepare_session(config)


@pytest.mark.parametrize("login_attempts", [0, 3, True, "1"])
def test_prepare_session_rejects_invalid_caller_login_attempt_override(login_attempts):
    config = session_config(Path(tempfile.gettempdir()) / f"unused-cookie-{uuid4().hex}.json")
    config["allow_login"] = False

    with pytest.raises(ValueError, match="login_attempts"):
        prepare_session(config, login_attempts=login_attempts)


def test_prepare_session_allows_business_single_login_attempt_override(monkeypatch, tmp_path):
    cookie_path = tmp_path / "cookie.json"
    retry_options = {}
    monkeypatch.setattr(
        session_manager,
        "close_browser_session",
        lambda *_args, **_kwargs: {"stopped_pids": []},
    )
    monkeypatch.setattr(
        session_manager,
        "validate_stage_probes",
        lambda *_args, **_kwargs: {"valid": True, "results": []},
    )

    def fake_login(_command, **kwargs):
        assert kwargs["env"]["AUTO_NOTIFY_REQUIRED_STAGES"] == "city_ops"
        write_json(
            Path(kwargs["env"]["AUTO_NOTIFY_COOKIE_DUMP_PATH"]),
            valid_city_ops_cookie_dump(),
        )
        return {"returncode": 0}

    def fake_retry(login_attempt, **kwargs):
        retry_options.update(kwargs)
        return login_attempt()

    monkeypatch.setattr(session_manager, "run_login_command", fake_login)
    monkeypatch.setattr(session_manager, "run_login_with_retry", fake_retry)

    result = prepare_session(
        session_config(cookie_path),
        force_refresh=True,
        login_attempts=1,
    )

    assert result["status"] == "refreshed"
    assert result["login_attempt_count"] == 1
    assert retry_options == {
        "max_attempts": 1,
        "retry_delay_seconds": 0,
        "preserve_diagnostics": False,
    }


def test_login_failure_retries_without_fixed_delay(monkeypatch):
    calls = []
    sleeps = []
    outcomes = iter([RuntimeError("first failure"), {"returncode": 0}])

    def login_attempt():
        calls.append("login")
        outcome = next(outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    result = session_manager.run_login_with_retry(
        login_attempt,
        max_attempts=2,
        retry_delay_seconds=0,
        sleeper=sleeps.append,
    )

    assert calls == ["login", "login"]
    assert sleeps == []
    assert result["attempt_count"] == 2


def test_login_stops_after_two_failed_attempts(monkeypatch):
    calls = []
    sleeps = []

    def login_attempt():
        calls.append("login")
        raise RuntimeError("login failed")

    with pytest.raises(session_manager.SessionLoginError) as exc_info:
        session_manager.run_login_with_retry(
            login_attempt,
            max_attempts=2,
            retry_delay_seconds=0,
            sleeper=sleeps.append,
        )

    assert calls == ["login", "login"]
    assert sleeps == []
    assert exc_info.value.attempt_count == 2


def test_prepare_session_rejects_fixed_login_retry_delay(monkeypatch):
    config = session_config(
        Path(tempfile.gettempdir()) / f"unused-cookie-{uuid4().hex}.json"
    )
    config["login_retry_delay_seconds"] = 60
    monkeypatch.setattr(
        session_manager,
        "file_lock",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("should reject retry delay before acquiring the lock")
        ),
    )

    with pytest.raises(ValueError, match="login_retry_delay_seconds 必须固定为 0"):
        prepare_session(config, force_refresh=True)


def test_browser_cleanup_failure_does_not_wait_for_fixed_login_retry_delay():
    calls = []
    sleeps = []

    def login_attempt():
        calls.append("login")
        raise session_manager.BrowserSessionCleanupError("browser_cleanup_failed")

    with pytest.raises(session_manager.SessionLoginError) as exc_info:
        session_manager.run_login_with_retry(
            login_attempt,
            max_attempts=2,
            retry_delay_seconds=60,
            sleeper=sleeps.append,
        )

    assert calls == ["login"]
    assert sleeps == []
    assert exc_info.value.errors == ["自动登录浏览器未能安全关闭"]


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
                    "actual_value": "sensitive-actual",
                    "expected_value": "sensitive-expected",
                    "error": "ConnectTimeout at https://example/getUserInfo",
                }
            ],
        }
    )

    assert "city_ops 探活失败" in message
    assert "原因=session_expired" in message
    assert "HTTP=401" in message
    assert "example/getUserInfo" not in message
    assert "sensitive-actual" not in message
    assert "sensitive-expected" not in message
    assert "ConnectTimeout" not in message

    diagnostic_message = format_probe_validation_error(
        {
            "valid": False,
            "results": [
                {
                    "stage": "city_ops",
                    "ok": False,
                    "reason": "probe_error",
                    "error": "ConnectTimeout: https://example/getUserInfo",
                }
            ],
        },
        include_diagnostics=True,
    )
    assert "错误类型=ConnectTimeout" in diagnostic_message
    assert "example/getUserInfo" not in diagnostic_message


def test_probe_classification_marks_redirect_as_authentication_failure():
    result = classify_probe_validation(
        {
            "valid": False,
            "results": [
                {
                    "stage": "report_analysis",
                    "ok": False,
                    "reason": "status_not_allowed",
                    "status_code": 302,
                }
            ],
        }
    )

    assert result == "authentication"


def test_probe_classification_marks_explicit_expiry_as_authentication_failure():
    result = classify_probe_validation(
        {
            "valid": False,
            "results": [
                {"stage": "city_ops", "ok": False, "reason": "session_expired"}
            ],
        }
    )

    assert result == "authentication"


def test_probe_classification_marks_json_value_mismatch_as_authentication_failure():
    result = classify_probe_validation(
        {"valid": False, "results": [{"stage": "city_ops", "ok": False, "reason": "json_value_mismatch"}]}
    )

    assert result == "authentication"


def test_prepare_session_logs_in_for_json_value_mismatch(monkeypatch):
    work_dir = make_work_dir()
    try:
        cookie_dump_path = work_dir / "cookie_dump.json"
        write_json(cookie_dump_path, valid_city_ops_cookie_dump())
        probe_results = iter([
            {"valid": False, "results": [{"stage": "city_ops", "ok": False, "reason": "json_value_mismatch"}]},
            {"valid": False, "results": [{"stage": "city_ops", "ok": False, "reason": "json_value_mismatch"}]},
            {"valid": True, "results": [{"stage": "city_ops", "ok": True}]},
        ])
        monkeypatch.setattr(session_manager, "validate_stage_probes", lambda *_args, **_kwargs: next(probe_results))
        monkeypatch.setattr(session_manager, "close_browser_session", lambda *_args, **_kwargs: {"stopped_pids": []})

        def fake_login(_command, **kwargs):
            write_json(Path(kwargs["env"]["AUTO_NOTIFY_COOKIE_DUMP_PATH"]), valid_city_ops_cookie_dump())
            return {"returncode": 0}

        monkeypatch.setattr(session_manager, "run_login_command", fake_login)
        assert prepare_session(session_config(cookie_dump_path))["status"] == "refreshed"
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def test_failed_login_attempt_never_replaces_shared_snapshot(monkeypatch):
    work_dir = make_work_dir()
    try:
        shared_path = work_dir / "cookie_dump.json"
        original = valid_city_ops_cookie_dump()
        original["generated_at"] = "original"
        write_json(shared_path, original)
        monkeypatch.setattr(session_manager, "validate_stage_probes", lambda *_args, **_kwargs: {"valid": False, "results": [{"stage": "city_ops", "ok": False, "status_code": 401}]})
        monkeypatch.setattr(session_manager, "close_browser_session", lambda *_args, **_kwargs: {"stopped_pids": []})

        def fake_login(_command, **kwargs):
            write_json(Path(kwargs["env"]["AUTO_NOTIFY_COOKIE_DUMP_PATH"]), {"stages": []})
            return {"returncode": 0}

        monkeypatch.setattr(session_manager, "run_login_command", fake_login)
        original_retry = session_manager.run_login_with_retry
        monkeypatch.setattr(
            session_manager,
            "run_login_with_retry",
            lambda login_attempt, **kwargs: original_retry(
                login_attempt,
                **kwargs,
                sleeper=lambda _seconds: None,
            ),
        )
        with pytest.raises(session_manager.SessionLoginError):
            prepare_session(session_config(shared_path))

        assert json.loads(shared_path.read_text(encoding="utf-8"))["generated_at"] == "original"
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def test_login_publishes_private_snapshot_only_after_probe_success(monkeypatch):
    work_dir = make_work_dir()
    try:
        shared_path = work_dir / "cookie_dump.json"
        original = valid_city_ops_cookie_dump()
        original["generated_at"] = "original"
        refreshed = valid_city_ops_cookie_dump()
        refreshed["generated_at"] = "refreshed"
        write_json(shared_path, original)
        probe_calls = 0

        def probes(cookie_dump, *_args, **_kwargs):
            nonlocal probe_calls
            probe_calls += 1
            if probe_calls <= 2:
                return {"valid": False, "results": [{"stage": "city_ops", "ok": False, "status_code": 401}]}
            assert json.loads(shared_path.read_text(encoding="utf-8"))["generated_at"] == "original"
            assert cookie_dump["generated_at"] == "refreshed"
            return {"valid": True, "results": [{"stage": "city_ops", "ok": True}]}

        monkeypatch.setattr(session_manager, "validate_stage_probes", probes)
        monkeypatch.setattr(session_manager, "close_browser_session", lambda *_args, **_kwargs: {"stopped_pids": []})

        def fake_login(_command, **kwargs):
            write_json(Path(kwargs["env"]["AUTO_NOTIFY_COOKIE_DUMP_PATH"]), refreshed)
            return {"returncode": 0}

        monkeypatch.setattr(session_manager, "run_login_command", fake_login)
        assert prepare_session(session_config(shared_path))["status"] == "refreshed"
        assert json.loads(shared_path.read_text(encoding="utf-8"))["generated_at"] == "refreshed"
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def test_prepare_session_second_login_attempt_publishes_only_successful_snapshot(monkeypatch):
    work_dir = make_work_dir()
    try:
        shared_path = work_dir / "cookie_dump.json"
        original = valid_city_ops_cookie_dump()
        original["generated_at"] = "original"
        refreshed = valid_city_ops_cookie_dump()
        refreshed["generated_at"] = "second-attempt"
        write_json(shared_path, original)
        probe_results = iter(
            [
                {"valid": False, "results": [{"stage": "city_ops", "ok": False, "status_code": 401}]},
                {"valid": False, "results": [{"stage": "city_ops", "ok": False, "status_code": 401}]},
                {"valid": True, "results": [{"stage": "city_ops", "ok": True}]},
            ]
        )
        monkeypatch.setattr(session_manager, "validate_stage_probes", lambda *_args, **_kwargs: next(probe_results))
        monkeypatch.setattr(session_manager, "close_browser_session", lambda *_args, **_kwargs: {"stopped_pids": []})
        attempt_paths = []

        def fake_login(_command, **kwargs):
            attempt_path = Path(kwargs["env"]["AUTO_NOTIFY_COOKIE_DUMP_PATH"])
            attempt_paths.append(attempt_path)
            write_json(attempt_path, {"stages": []} if len(attempt_paths) == 1 else refreshed)
            assert json.loads(shared_path.read_text(encoding="utf-8"))["generated_at"] == "original"
            return {"returncode": 0}

        monkeypatch.setattr(session_manager, "run_login_command", fake_login)
        original_retry = session_manager.run_login_with_retry
        sleeps = []
        monkeypatch.setattr(
            session_manager,
            "run_login_with_retry",
            lambda login_attempt, **kwargs: original_retry(
                login_attempt,
                **kwargs,
                sleeper=sleeps.append,
            ),
        )

        result = prepare_session(session_config(shared_path))

        assert result["login_attempt_count"] == 2
        assert sleeps == []
        assert len(set(attempt_paths)) == 2
        assert all(not path.exists() for path in attempt_paths)
        assert json.loads(shared_path.read_text(encoding="utf-8"))["generated_at"] == "second-attempt"
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def test_post_login_authentication_failure_retries(monkeypatch, tmp_path):
    shared_path = tmp_path / "cookie_dump.json"
    probe_results = iter(
        [
            {"valid": False, "results": [{"stage": "city_ops", "ok": False, "status_code": 401}]},
            {"valid": True, "results": [{"stage": "city_ops", "ok": True}]},
        ]
    )
    monkeypatch.setattr(
        session_manager,
        "validate_stage_probes",
        lambda *_args, **_kwargs: next(probe_results),
    )
    monkeypatch.setattr(
        session_manager,
        "close_browser_session",
        lambda *_args, **_kwargs: {"stopped_pids": []},
    )
    login_calls = []

    def fake_login(_command, **kwargs):
        attempt_path = Path(kwargs["env"]["AUTO_NOTIFY_COOKIE_DUMP_PATH"])
        login_calls.append(attempt_path)
        snapshot = valid_city_ops_cookie_dump()
        snapshot["stages"][0]["cookies"][0]["value"] = "sentinel-cookie-secret"
        snapshot["generated_at"] = f"attempt-{len(login_calls)}"
        write_json(attempt_path, snapshot)
        return {"returncode": 0}

    monkeypatch.setattr(session_manager, "run_login_command", fake_login)
    original_retry = session_manager.run_login_with_retry
    monkeypatch.setattr(
        session_manager,
        "run_login_with_retry",
        lambda login_attempt, **kwargs: original_retry(
            login_attempt, **kwargs, sleeper=lambda _seconds: None
        ),
    )

    result = prepare_session(session_config(shared_path), force_refresh=True)

    assert result["status"] == "refreshed"
    assert result["login_attempt_count"] == 2
    assert len(login_calls) == 2


def test_post_login_infrastructure_failure_aborts_without_retry(monkeypatch, tmp_path):
    shared_path = tmp_path / "cookie_dump.json"
    monkeypatch.setattr(
        session_manager,
        "validate_stage_probes",
        lambda *_args, **_kwargs: {
            "valid": False,
            "results": [
                {
                    "stage": "city_ops",
                    "ok": False,
                    "reason": "probe_error",
                    "error": "ConnectTimeout",
                }
            ],
        },
    )
    monkeypatch.setattr(
        session_manager,
        "close_browser_session",
        lambda *_args, **_kwargs: {"stopped_pids": []},
    )
    login_calls = []

    def fake_login(_command, **kwargs):
        login_calls.append(True)
        write_json(
            Path(kwargs["env"]["AUTO_NOTIFY_COOKIE_DUMP_PATH"]),
            valid_city_ops_cookie_dump(),
        )
        return {"returncode": 0}

    monkeypatch.setattr(session_manager, "run_login_command", fake_login)

    with pytest.raises(SessionInfrastructureError, match="ConnectTimeout"):
        prepare_session(session_config(shared_path), force_refresh=True)

    assert login_calls == [True]


def test_prepare_session_converts_login_lock_timeout_to_infrastructure(monkeypatch):
    config = session_config(Path(tempfile.gettempdir()) / f"unused-cookie-{uuid4().hex}.json")
    monkeypatch.setattr(session_manager, "file_lock", lambda *_args, **_kwargs: (_ for _ in ()).throw(TimeoutError("busy")))

    with pytest.raises(SessionInfrastructureError, match="busy"):
        prepare_session(config, force_refresh=True)


def test_lock_wait_must_cover_complete_login_retry_envelope():
    config = session_config(Path(tempfile.gettempdir()) / f"unused-cookie-{uuid4().hex}.json")
    config.update({"login_timeout_seconds": 600, "login_lock_wait_seconds": 1200})

    with pytest.raises(ValueError, match="login_lock_wait_seconds"):
        prepare_session(config, force_refresh=True)


def test_run_login_command_redacts_sensitive_child_diagnostic(monkeypatch):
    completed = type(
        "Completed",
        (),
        {
            "returncode": 1,
            "stdout": "",
            "stderr": "password=raw-password",
        },
    )()
    monkeypatch.setattr(session_manager.subprocess, "run", lambda *_args, **_kwargs: completed)

    with pytest.raises(RuntimeError) as exc_info:
        session_manager.run_login_command("sensitive command")

    message = str(exc_info.value)
    assert "错误类别=unknown" in message
    assert "诊断=" not in message
    assert "raw-password" not in message
    assert "sensitive command" not in message


def test_run_login_command_preserves_raw_diagnostic_when_requested(monkeypatch):
    completed = type(
        "Completed",
        (),
        {
            "returncode": 1,
            "stdout": "",
            "stderr": "password=raw-password\nlogin failed",
        },
    )()
    monkeypatch.setattr(session_manager.subprocess, "run", lambda *_args, **_kwargs: completed)

    with pytest.raises(RuntimeError) as exc_info:
        session_manager.run_login_command(
            "sensitive command", preserve_diagnostics=True
        )

    message = str(exc_info.value)
    assert "退出码=1" in message
    assert "password=raw-password" in message
    assert "login failed" in message


@pytest.mark.parametrize(
    ("diagnostic", "expected_category"),
    [
        ("等待 Gotify 验证码超时", "otp_timeout"),
        ("打开登录页后未找到 loginName 输入框", "login_page_timeout"),
        ("WebDriverException: Edge browser failed to start", "browser_error"),
        ("数据超市 Cookie/Storage 写入失败", "session_capture_failed"),
        ("unexpected child process error", "unknown"),
    ],
)
def test_classify_login_failure_returns_stable_safe_category(
    diagnostic, expected_category
):
    assert session_manager.classify_login_failure(diagnostic) == expected_category


def test_run_login_command_redacts_values_and_keeps_safe_reason(monkeypatch):
    completed = type(
        "Completed",
        (),
        {
            "returncode": 1,
            "stdout": "",
            "stderr": (
                "等待 Gotify 验证码超时; username=alice; "
                "password=correct horse battery staple; "
                "token=raw-token; Authorization: Bearer raw-auth; "
                "authorization=Bearer raw-assignment-auth; "
                "X-Token: raw-header-token; "
                "verification_code=123456; otp code: 654321; "
                "Cookie: sid=raw-cookie; "
                "endpoint=/login?ticket=raw-relative-ticket; "
                "www.internal.example/login?ticket=raw-host-ticket; "
                "https://internal.example/login?ticket=raw-ticket"
            ),
        },
    )()
    monkeypatch.setattr(
        session_manager.subprocess, "run", lambda *_args, **_kwargs: completed
    )

    with pytest.raises(RuntimeError) as exc_info:
        session_manager.run_login_command("sensitive command")

    message = str(exc_info.value)
    assert "错误类别=otp_timeout" in message
    assert "等待验证码超时" in message
    for secret in (
        "alice",
        "raw-password",
        "correct horse battery staple",
        "raw-token",
        "raw-auth",
        "raw-assignment-auth",
        "raw-header-token",
        "123456",
        "654321",
        "raw-cookie",
        "raw-relative-ticket",
        "raw-host-ticket",
        "raw-ticket",
    ):
        assert secret not in message
    assert "internal.example" not in message


def test_run_login_command_hides_timeout_command(monkeypatch):
    timeout = session_manager.subprocess.TimeoutExpired(
        ["python", "login.py", "--password", "raw-password"], 30
    )
    monkeypatch.setattr(
        session_manager.subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(timeout),
    )

    with pytest.raises(RuntimeError) as exc_info:
        session_manager.run_login_command("sensitive command")

    message = str(exc_info.value)
    assert "错误类别=unknown" in message
    assert "诊断=" not in message
    assert "raw-password" not in message
    assert "login.py" not in message


def test_run_login_command_success_result_does_not_include_command(monkeypatch):
    completed = type(
        "Completed",
        (),
        {
            "returncode": 0,
            "stdout": "",
            "stderr": "",
        },
    )()
    monkeypatch.setattr(
        session_manager.subprocess, "run", lambda *_args, **_kwargs: completed
    )

    result = session_manager.run_login_command(
        "python login.py --password raw-password"
    )

    assert result == {"returncode": 0}
    assert "command" not in result
    assert "raw-password" not in repr(result)


def test_run_login_command_includes_sanitized_stderr_diagnostic(monkeypatch):
    completed = type(
        "Completed",
        (),
        {
            "returncode": 1,
            "stdout": "",
            "stderr": "等待 Gotify 验证码超时：45 秒内未收到可用短信转发。",
        },
    )()
    monkeypatch.setattr(session_manager.subprocess, "run", lambda *_args, **_kwargs: completed)

    with pytest.raises(RuntimeError) as exc_info:
        session_manager.run_login_command("sensitive command")

    message = str(exc_info.value)
    assert "退出码=1" in message
    assert "错误类别=otp_timeout" in message
    assert "等待验证码超时" in message
    assert "sensitive command" not in message


def test_run_login_command_includes_only_valid_safe_child_diagnostic(monkeypatch):
    completed = type(
        "Completed",
        (),
        {
            "returncode": 1,
            "stdout": "",
            "stderr": (
                "AUTO_NOTIFY_LOGIN_DIAGNOSTIC phase=init_driver "
                "exception=WebDriverException reason=driver_unreachable\n"
                "password=raw-password https://internal.example/login?token=raw-token"
            ),
        },
    )()
    monkeypatch.setattr(
        session_manager.subprocess, "run", lambda *_args, **_kwargs: completed
    )

    with pytest.raises(RuntimeError) as exc_info:
        session_manager.run_login_command("sensitive command")

    message = str(exc_info.value)
    assert "错误类别=browser_error" in message
    assert "阶段=init_driver" in message
    assert "异常=WebDriverException" in message
    assert "原因=driver_unreachable" in message
    for secret in ("raw-password", "internal.example", "raw-token"):
        assert secret not in message


def test_run_login_command_discards_invalid_child_diagnostic_marker(monkeypatch):
    completed = type(
        "Completed",
        (),
        {
            "returncode": 1,
            "stdout": "",
            "stderr": (
                "AUTO_NOTIFY_LOGIN_DIAGNOSTIC phase=operator_input "
                "exception=PasswordError reason=raw-token\n"
                "WebDriverException: driver failed"
            ),
        },
    )()
    monkeypatch.setattr(
        session_manager.subprocess, "run", lambda *_args, **_kwargs: completed
    )

    with pytest.raises(RuntimeError) as exc_info:
        session_manager.run_login_command("sensitive command")

    message = str(exc_info.value)
    assert "错误类别=browser_error" in message
    assert "阶段=" not in message
    assert "operator_input" not in message
    assert "PasswordError" not in message
    assert "raw-token" not in message


def test_session_login_error_contains_only_bounded_sanitized_summaries():
    def fail():
        raise RuntimeError("用户名: sentinel-user STDOUT: raw-stdout-secret " + "x" * 1000)

    with pytest.raises(session_manager.SessionLoginError) as exc_info:
        session_manager.run_login_with_retry(
            fail,
            max_attempts=1,
            retry_delay_seconds=60,
        )

    summary = exc_info.value.errors[0]
    assert "sentinel-user" not in summary
    assert "raw-stdout-secret" not in summary
    assert len(summary) <= 500


def test_session_login_error_preserves_diagnostics_when_requested():
    raw_diagnostic = "password=raw-password\nlogin failed"

    with pytest.raises(session_manager.SessionLoginError) as exc_info:
        session_manager.run_login_with_retry(
            lambda: (_ for _ in ()).throw(RuntimeError(raw_diagnostic)),
            max_attempts=1,
            retry_delay_seconds=0,
            preserve_diagnostics=True,
        )

    assert exc_info.value.errors == [raw_diagnostic]


def test_probe_classification_marks_request_exception_as_infrastructure_failure():
    result = classify_probe_validation(
        {
            "valid": False,
            "results": [
                {
                    "stage": "smart_ops",
                    "ok": False,
                    "reason": "probe_error",
                    "error": "ConnectTimeout",
                }
            ],
        }
    )

    assert result == "infrastructure"


def test_probe_classification_prioritizes_authentication_when_failures_are_mixed():
    result = classify_probe_validation(
        {
            "valid": False,
            "results": [
                {"stage": "report_analysis", "ok": False, "status_code": 401},
                {"stage": "smart_ops", "ok": False, "reason": "probe_error"},
            ],
        }
    )

    assert result == "authentication"


@pytest.mark.parametrize(
    ("stage_authentication_material", "probe_authentication_source"),
    [
        ({"cookies": []}, {"headers_from_cookies": {"Authorization": "access_token"}}),
        (
            {"cookies": [], "session_storage": {}},
            {"headers_from_session_storage": {"Authorization": "access_token"}},
        ),
        (
            {"cookies": [], "local_storage": {}},
            {"headers_from_local_storage": {"Authorization": "access_token"}},
        ),
    ],
)
def test_probe_classification_marks_missing_authentication_material_as_authentication(
    monkeypatch,
    stage_authentication_material,
    probe_authentication_source,
):
    monkeypatch.setattr(session_manager.requests, "Session", FakeSession)
    FakeSession.responses = []
    result = validate_stage_probes(
        {
            "stages": [
                {
                    "stage": "smart_ops",
                    **stage_authentication_material,
                }
            ]
        },
        ["smart_ops"],
        {
            "smart_ops": {
                "method": "GET",
                "url": "https://example/refresh",
                **probe_authentication_source,
            }
        },
    )

    assert result["results"] == [
        {
            "stage": "smart_ops",
            "enabled": True,
            "ok": False,
            "reason": "missing_authentication_material",
        }
    ]
    assert classify_probe_validation(result) == "authentication"


def test_lock_is_stale_when_recorded_pid_is_gone(monkeypatch):
    work_dir = make_work_dir()
    try:
        lock_path = work_dir / "login.lock"
        write_json(lock_path, {"pid": 999999})
        monkeypatch.setattr(session_manager, "process_is_running", lambda pid: False)

        assert lock_is_stale(lock_path, stale_seconds=600) is True
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def test_lock_is_not_stale_while_recorded_pid_is_alive(monkeypatch):
    work_dir = make_work_dir()
    try:
        lock_path = work_dir / "login.lock"
        write_json(lock_path, {"pid": 12345})
        monkeypatch.setattr(session_manager, "process_is_running", lambda pid: True)
        monkeypatch.setattr(session_manager.time, "time", lambda: 10**12)

        assert lock_is_stale(lock_path, stale_seconds=1) is False
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def test_login_lock_stale_eviction_does_not_remove_replacement_owner(monkeypatch, tmp_path):
    lock_path = tmp_path / "login.lock"
    write_json(
        lock_path,
        {
            "pid": 100,
            "process_started_at": "2026-07-12T00:00:00+00:00",
            "owner_token": "stale-owner",
            "acquired_at": "2026-07-12T00:00:00+00:00",
        },
    )
    monkeypatch.setattr(session_manager, "process_is_running", lambda *_args: False)
    original_snapshot = session_manager._read_lock_snapshot(lock_path)
    write_json(
        lock_path,
        {
            "pid": 200,
            "process_started_at": "2026-07-12T00:01:00+00:00",
            "owner_token": "replacement-owner",
            "acquired_at": "2026-07-12T00:01:00+00:00",
        },
    )

    assert (
        session_manager._remove_stale_lock(lock_path, original_snapshot["identity"])
        == "retry"
    )
    assert json.loads(lock_path.read_text(encoding="utf-8"))["owner_token"] == "replacement-owner"


def test_login_lock_release_does_not_remove_replacement_owner(monkeypatch, tmp_path):
    lock_path = tmp_path / "login.lock"
    monkeypatch.setattr(
        session_manager,
        "current_process_started_at",
        lambda: "2026-07-12T00:00:00+00:00",
    )
    lock = session_manager.LoginFileLock(
        lock_path,
        wait_seconds=1,
        poll_seconds=0.01,
        stale_seconds=600,
    ).acquire()
    write_json(
        lock_path,
        {
            "pid": 200,
            "process_started_at": "2026-07-12T00:01:00+00:00",
            "owner_token": "replacement-owner",
            "acquired_at": "2026-07-12T00:01:00+00:00",
        },
    )

    lock.release()

    assert json.loads(lock_path.read_text(encoding="utf-8"))["owner_token"] == "replacement-owner"


def test_login_lock_os_guard_blocks_second_owner_when_metadata_is_replaced(
    monkeypatch, tmp_path
):
    lock_path = tmp_path / "login.lock"
    monkeypatch.setattr(
        session_manager,
        "current_process_started_at",
        lambda: "2026-07-12T00:00:00+00:00",
    )
    first = session_manager.LoginFileLock(
        lock_path,
        wait_seconds=1,
        poll_seconds=0.01,
        stale_seconds=600,
    ).acquire()
    write_json(
        lock_path,
        {
            "pid": os.getpid(),
            "process_started_at": "2026-07-12T00:01:00+00:00",
            "owner_token": "replacement-owner",
            "acquired_at": "2026-07-12T00:01:00+00:00",
        },
    )
    second = session_manager.LoginFileLock(
        lock_path,
        wait_seconds=0.05,
        poll_seconds=0.01,
        stale_seconds=0,
    )

    with pytest.raises(TimeoutError):
        second.acquire()

    first.release()
    assert json.loads(lock_path.read_text(encoding="utf-8"))["owner_token"] == "replacement-owner"


def test_login_lock_permission_denied_still_honors_wait_deadline(
    monkeypatch, tmp_path
):
    lock_path = tmp_path / "login.lock"
    write_json(lock_path, {"pid": 999999, "owner_token": "stale"})
    monkeypatch.setattr(session_manager, "process_is_running", lambda *_args: False)
    monkeypatch.setattr(
        session_manager,
        "_remove_stale_lock",
        lambda *_args: "permission_denied",
    )
    lock = session_manager.LoginFileLock(
        lock_path,
        wait_seconds=0.05,
        poll_seconds=0.01,
        stale_seconds=0,
    )

    with pytest.raises(TimeoutError):
        lock.acquire()
