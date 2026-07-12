import json
import shutil
from pathlib import Path
from uuid import uuid4

import pytest

from services import session_manager
from services.session_manager import (
    SessionInfrastructureError,
    classify_probe_validation,
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
    work_dir = (Path("runtime/test_work") / uuid4().hex).resolve()
    work_dir.mkdir(parents=True, exist_ok=True)
    return work_dir


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


def session_config(cookie_dump_path):
    return {
        "cookie_dump_path": str(cookie_dump_path),
        "required_stages": ["city_ops"],
        "login_command": "fake-login",
        "login_max_attempts": 2,
        "login_retry_delay_seconds": 60,
        "stage_probes": {"city_ops": {"method": "POST", "url": "https://example/getUserInfo"}},
    }


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


def test_prepare_session_from_config_keeps_project_base_dir():
    work_dir = make_work_dir()
    try:
        config_path = work_dir / "config" / "modules" / "autologin.json"
        cookie_dump_path = work_dir / "state" / "cookies" / "cookie_dump.json"
        write_json(
            config_path,
            {
                "cookie_dump_path": "state/cookies/cookie_dump.json",
                "required_stages": ["report_analysis"],
                "allow_login": False,
            },
        )
        write_json(
            cookie_dump_path,
            {"stages": [{"stage": "report_analysis", "cookies": [{"name": "sid", "value": "x"}]}]},
        )

        result = prepare_session_from_config(
            "config/modules/autologin.json",
            base_dir=work_dir,
        )

        assert result["status"] == "reused"
        assert result["cookie_dump_path"] == str(cookie_dump_path.resolve())
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def test_prepare_session_from_config_rebases_runtime_state(monkeypatch, tmp_path):
    config_path = tmp_path / "config" / "modules" / "autologin.json"
    shared_cookie_path = tmp_path / "shared" / "cookies" / "cookie_dump.json"
    write_json(
        config_path,
        {
            "cookie_dump_path": "runtime/cookies/cookie_dump.json",
            "required_stages": ["report_analysis"],
            "allow_login": False,
        },
    )
    write_json(
        shared_cookie_path,
        {
            "stages": [
                {
                    "stage": "report_analysis",
                    "cookies": [{"name": "sid", "value": "x"}],
                }
            ]
        },
    )
    monkeypatch.setenv("AUTO_NOTIFY_RUNTIME_ROOT", str(tmp_path / "shared"))

    result = prepare_session_from_config(
        "config/modules/autologin.json", base_dir=tmp_path
    )

    assert result["status"] == "reused"
    assert result["cookie_dump_path"] == str(shared_cookie_path.resolve())


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
        ("login_retry_delay_seconds", 0),
    ],
)
def test_prepare_session_rejects_non_production_retry_configuration(key, value):
    config = session_config(Path("runtime/unused-cookie-dump.json"))
    config[key] = value
    config["allow_login"] = False

    with pytest.raises(ValueError, match=key):
        prepare_session(config)


def test_login_failure_waits_sixty_seconds_and_retries_once(monkeypatch):
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
        retry_delay_seconds=60,
        sleeper=sleeps.append,
    )

    assert calls == ["login", "login"]
    assert sleeps == [60]
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
            retry_delay_seconds=60,
            sleeper=sleeps.append,
        )

    assert calls == ["login", "login"]
    assert sleeps == [60]
    assert exc_info.value.attempt_count == 2


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
        assert sleeps == [60]
        assert len(set(attempt_paths)) == 2
        assert all(not path.exists() for path in attempt_paths)
        assert json.loads(shared_path.read_text(encoding="utf-8"))["generated_at"] == "second-attempt"
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def test_prepare_session_converts_login_lock_timeout_to_infrastructure(monkeypatch):
    config = session_config(Path("runtime/unused-cookie-dump.json"))
    monkeypatch.setattr(session_manager, "file_lock", lambda *_args, **_kwargs: (_ for _ in ()).throw(TimeoutError("busy")))

    with pytest.raises(SessionInfrastructureError, match="busy"):
        prepare_session(config, force_refresh=True)


def test_lock_wait_must_cover_complete_login_retry_envelope():
    config = session_config(Path("runtime/unused-cookie-dump.json"))
    config.update({"login_timeout_seconds": 600, "login_lock_wait_seconds": 1200})

    with pytest.raises(ValueError, match="login_lock_wait_seconds"):
        prepare_session(config, force_refresh=True)


def test_login_error_does_not_transport_raw_command_output(monkeypatch):
    completed = type("Completed", (), {"returncode": 1, "stdout": "raw-stdout-secret", "stderr": "raw-stderr-secret"})()
    monkeypatch.setattr(session_manager.subprocess, "run", lambda *_args, **_kwargs: completed)

    with pytest.raises(RuntimeError) as exc_info:
        session_manager.run_login_command("sensitive command")

    message = str(exc_info.value)
    assert "raw-stdout-secret" not in message
    assert "raw-stderr-secret" not in message
    assert "sensitive command" not in message


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
