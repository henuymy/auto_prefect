from __future__ import annotations

import json
from pathlib import Path

import pytest

from services import login_service
from services.browser_session import browser_config


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class FakeCommandExecutor:
    def __init__(self) -> None:
        self.timeout = None

    def set_timeout(self, timeout: int) -> None:
        self.timeout = timeout


class FakeDriver:
    def __init__(self) -> None:
        self.command_executor = FakeCommandExecutor()
        self.page_load_timeout = None
        self.script_timeout = None
        self.implicit_wait_seconds = None

    def set_page_load_timeout(self, timeout: int) -> None:
        self.page_load_timeout = timeout

    def set_script_timeout(self, timeout: int) -> None:
        self.script_timeout = timeout

    def implicitly_wait(self, timeout: int) -> None:
        self.implicit_wait_seconds = timeout


class FakeWindowSwitch:
    def __init__(self, driver) -> None:
        self.driver = driver

    def window(self, handle: str) -> None:
        self.driver.current_window_handle = handle


class FakeWindowDriver:
    def __init__(self, urls: dict[str, str]) -> None:
        self.urls = urls
        self.window_handles = list(urls)
        self.current_window_handle = self.window_handles[0]
        self.switch_to = FakeWindowSwitch(self)

    @property
    def current_url(self) -> str:
        return self.urls[self.current_window_handle]


class ImmediateWait:
    def __init__(self, driver, timeout, poll_frequency=None) -> None:
        self.driver = driver

    def until(self, condition):
        result = condition(self.driver)
        if not result:
            raise login_service.TimeoutException()
        return result


def make_login(tmp_path: Path, **browser_overrides) -> login_service.AutoLogin:
    instance = login_service.AutoLogin.__new__(login_service.AutoLogin)
    instance.config = {
        "browser": {
            "headless": True,
            "keep_open_after_login": False,
            "user_data_dir": str(tmp_path / "profile"),
            "session_state_path": str(tmp_path / "session.json"),
            "window_width": 1440,
            "window_height": 900,
            **browser_overrides,
        }
    }
    instance.driver = None
    return instance


def test_login_source_does_not_print_actual_username():
    source = (PROJECT_ROOT / "services" / "login_service.py").read_text(encoding="utf-8")

    assert 'print(f"[INFO] 输入用户名: {username}")' not in source


def test_auto_login_accepts_in_memory_config_without_reading_disk(monkeypatch):
    monkeypatch.setattr(
        login_service,
        "load_json_with_local_override",
        lambda path: (_ for _ in ()).throw(AssertionError(f"unexpected read: {path}")),
    )
    config = {
        "credentials": {"username": "memory-user", "password": "memory-password"},
        "cookie_dump": {"enabled": False},
    }

    login = login_service.AutoLogin(config=config, config_label="session-experiment")

    assert login.config == config
    assert str(login.config_path) == "session-experiment"


def test_login_scopes_usm_capture_and_validation_to_requested_stages(monkeypatch):
    login = login_service.AutoLogin.__new__(login_service.AutoLogin)
    login.config = {
        "usm_cookie_apps": [
            {"stage": "report_analysis", "name": "报表分析"},
            {"stage": "smart_ops", "name": "智慧运营"},
            {"stage": "city_ops", "name": "市级运营"},
            {"stage": "data_market", "name": "数据超市"},
        ]
    }
    monkeypatch.setenv(
        login_service.REQUIRED_STAGES_ENV,
        "city_ops, report_analysis, city_ops",
    )

    assert login.requested_session_stages() == ["city_ops", "report_analysis"]
    assert [app["stage"] for app in login.get_usm_cookie_apps()] == [
        "report_analysis",
        "city_ops",
    ]


def test_capture_usm_app_cookies_logs_per_stage_elapsed_time(monkeypatch, capsys):
    login = login_service.AutoLogin.__new__(login_service.AutoLogin)
    login.usm_window_handle = None
    login.opened_app_handles = {}
    app_config = {
        "stage": "city_ops",
        "name": "地市作战平台",
        "source_stage": "usm_console",
    }

    monkeypatch.setattr(login, "get_usm_cookie_apps", lambda: [app_config])
    monkeypatch.setattr(login, "enter_usm_app_from_source", lambda _: "city-window")
    monkeypatch.setattr(login, "wait_for_storage_ready", lambda _: None)
    monkeypatch.setattr(login, "wait_for_cookie_ready", lambda _: None)
    monkeypatch.setattr(login, "capture_cookies", lambda _: {"stage": "city_ops"})
    monkeypatch.setattr(login_service.time, "monotonic", lambda: 100.0)

    login.capture_usm_apps_cookies()

    output = capsys.readouterr().out
    assert "USM 应用 Cookie 捕获完成: stage=city_ops attempts=1 elapsed_seconds=0.00" in output


def test_login_keeps_full_usm_capture_when_requested_stage_is_unknown(monkeypatch):
    login = login_service.AutoLogin.__new__(login_service.AutoLogin)
    login.config = {
        "usm_cookie_apps": [
            {"stage": "report_analysis", "name": "报表分析"},
            {"stage": "city_ops", "name": "市级运营"},
        ]
    }
    monkeypatch.setenv(login_service.REQUIRED_STAGES_ENV, "city_ops, unknown_stage")

    assert [app["stage"] for app in login.get_usm_cookie_apps()] == [
        "report_analysis",
        "city_ops",
    ]


def test_login_page_no_permission_retries_without_waiting_for_timeout(monkeypatch):
    login = login_service.AutoLogin.__new__(login_service.AutoLogin)
    login.driver = object()
    states = iter(["no_permission", "login"])
    reset_reasons = []

    monkeypatch.setattr(login_service, "WebDriverWait", ImmediateWait)
    monkeypatch.setattr(login, "detect_login_entry_or_home", lambda driver: next(states))
    monkeypatch.setattr(
        login,
        "reset_ngboss_login_state",
        lambda reason: reset_reasons.append(reason),
    )

    assert login.wait_for_login_entry_or_home() == "login"
    assert reset_reasons == ["检测到系统异常页"]


def test_init_driver_enables_headless_edge(monkeypatch, tmp_path):
    captured = {}
    fake_driver = FakeDriver()
    monkeypatch.setattr(
        login_service,
        "close_browser_session",
        lambda *args, **kwargs: {"stopped_pids": [], "remaining_pids": []},
    )

    def create_driver(*, options):
        captured["options"] = options
        return fake_driver

    monkeypatch.setattr(login_service.webdriver, "Edge", create_driver)

    login = make_login(tmp_path)
    login.init_driver()

    options = captured["options"]
    assert "--headless=new" in options.arguments
    assert "--window-size=1440,900" in options.arguments
    assert "detach" not in options.experimental_options
    assert login.driver is fake_driver
    assert login.driver.implicit_wait_seconds == 0


def test_init_driver_logs_browser_close_summary_without_paths_or_process_ids(monkeypatch, tmp_path, capsys):
    fake_driver = FakeDriver()
    monkeypatch.setattr(
        login_service,
        "close_browser_session",
        lambda *args, **kwargs: {
            "status": "closed",
            "stopped_pids": [15880, 8170],
            "remaining_pids": [],
            "state_path": r"C:\\AutoNotifyRuntime\\session\\browser-session.json",
            "user_data_dir": r"C:\\AutoNotifyRuntime\\session\\browser-profile",
        },
    )
    monkeypatch.setattr(login_service.webdriver, "Edge", lambda *, options: fake_driver)

    make_login(tmp_path).init_driver()

    output = capsys.readouterr().out
    assert "stopped_count=2" in output
    assert "remaining_count=0" in output
    assert "15880" not in output
    assert "browser-session.json" not in output


def test_init_driver_detaches_explicitly_retained_headless_edge(monkeypatch, tmp_path):
    captured = {}
    fake_driver = FakeDriver()
    monkeypatch.setattr(
        login_service,
        "close_browser_session",
        lambda *args, **kwargs: {"stopped_pids": [], "remaining_pids": []},
    )

    def create_driver(*, options):
        captured["options"] = options
        return fake_driver

    monkeypatch.setattr(login_service.webdriver, "Edge", create_driver)

    login = make_login(tmp_path, retain_after_login=True)
    login.init_driver()

    options = captured["options"]
    assert "--headless=new" in options.arguments
    assert options.experimental_options["detach"] is True


def test_headless_browser_is_ephemeral_by_default():
    config = browser_config({"browser": {"headless": True}})

    assert config["retain_after_login"] is False


def test_headless_browser_can_be_explicitly_retained():
    config = browser_config(
        {"browser": {"headless": True, "retain_after_login": True}}
    )

    assert config["headless"] is True
    assert config["retain_after_login"] is True


def test_headed_browser_keeps_legacy_retained_default():
    config = browser_config({"browser": {"headless": False}})

    assert config["retain_after_login"] is True


def test_browser_runtime_paths_rebase_to_external_root(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTO_NOTIFY_RUNTIME_ROOT", str(tmp_path / "shared"))

    config = browser_config(
        {
            "browser": {
                "session_state_path": "runtime/session/browser-session.json",
                "user_data_dir": "runtime/session/browser-profile",
            }
        },
        base_dir=tmp_path,
    )

    assert config["session_state_path"] == (
        tmp_path / "shared" / "session" / "browser-session.json"
    ).resolve()
    assert config["user_data_dir"] == (
        tmp_path / "shared" / "session" / "browser-profile"
    ).resolve()


def test_cookie_dump_runtime_path_rebases_to_external_root(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTO_NOTIFY_RUNTIME_ROOT", str(tmp_path / "shared"))
    login = login_service.AutoLogin(
        config={
            "credentials": {},
            "cookie_dump": {
                "enabled": True,
                "output_file": "runtime/session/cookie_dump.json",
            },
        },
        config_label="runtime-path-test",
    )

    assert login.cookie_recorder.output_path == (
        tmp_path / "shared" / "session" / "cookie_dump.json"
    ).resolve()


def test_init_driver_uses_original_retained_mode_when_headed(monkeypatch, tmp_path):
    captured = {}
    fake_driver = FakeDriver()
    monkeypatch.setattr(
        login_service,
        "close_browser_session",
        lambda *args, **kwargs: {"stopped_pids": [], "remaining_pids": []},
    )
    def create_driver(*, options):
        captured["options"] = options
        return fake_driver

    monkeypatch.setattr(login_service.webdriver, "Edge", create_driver)

    login = make_login(tmp_path, headless=False, keep_open_after_login=False)
    login.init_driver()

    options = captured["options"]
    assert "--headless=new" not in options.arguments
    assert options.experimental_options["detach"] is True


def test_default_login_config_uses_retained_headless_browser():
    config = json.loads(
        (PROJECT_ROOT / "config" / "modules" / "login_config.json").read_text(
            encoding="utf-8"
        )
    )

    assert config["browser"]["headless"] is True
    assert config["browser"]["retain_after_login"] is True
    assert "keep_open_after_login" not in config["browser"]
    assert config["browser"]["clear_profile_before_start"] is False
    assert config["browser"]["implicit_wait"] == 0
    assert config["otp_config"]["poll_interval_seconds"] == 0.5
    assert config["session_validation"]["enabled"] is True
    assert [item["stage"] for item in config["usm_cookie_apps"]] == [
        "report_analysis",
        "smart_ops",
        "city_ops",
        "data_market",
    ]
    report = config["usm_cookie_apps"][0]
    assert report["cookie_ready"] == "ssr-token"
    assert report["capture_attempts"] == 3


def test_launched_app_window_ignores_about_blank(monkeypatch):
    driver = FakeWindowDriver(
        {
            "usm": "https://usm.example/console/",
            "blank": "about:blank",
            "report": "https://usm.example/bicpreport/index.html",
        }
    )
    login = login_service.AutoLogin.__new__(login_service.AutoLogin)
    login.driver = driver
    monkeypatch.setattr(login_service, "WebDriverWait", ImmediateWait)

    handle = login.wait_for_launched_app_window(
        {"usm"},
        {"stage": "report_analysis", "name": "报表分析系统", "url_contains": "/bicpreport/"},
    )

    assert handle == "report"
    assert driver.current_window_handle == "report"


def test_launched_app_window_skips_blank_variants_and_restores_source(monkeypatch):
    driver = FakeWindowDriver(
        {
            "usm": "https://usm.example/console/",
            "blank": "about:blank#helper",
        }
    )
    login = login_service.AutoLogin.__new__(login_service.AutoLogin)
    login.driver = driver
    monkeypatch.setattr(login_service, "WebDriverWait", ImmediateWait)

    with pytest.raises(RuntimeError, match="新窗口 URL"):
        login.wait_for_launched_app_window(
            {"usm"},
            {"stage": "report_analysis", "name": "报表分析系统", "url_contains": "/bicpreport/"},
        )

    assert driver.current_window_handle == "usm"


def test_capture_cookies_returns_recorder_stage():
    expected = {"stage": "report_analysis", "cookies": [{"name": "sid"}]}

    class Recorder:
        def capture(self, driver, stage_name):
            assert stage_name == "report_analysis"
            return expected

    login = login_service.AutoLogin.__new__(login_service.AutoLogin)
    login.driver = object()
    login.cookie_recorder = Recorder()

    assert login.capture_cookies("report_analysis") == expected
