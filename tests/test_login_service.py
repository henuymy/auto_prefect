from __future__ import annotations

import json
from pathlib import Path

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


def test_headless_switch_controls_browser_retention():
    assert browser_config(
        {"browser": {"headless": True, "keep_open_after_login": True}}
    )["keep_open_after_login"] is False
    assert browser_config(
        {"browser": {"headless": False, "keep_open_after_login": False}}
    )["keep_open_after_login"] is True


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


def test_default_login_config_uses_headless_ephemeral_browser():
    config = json.loads(
        (PROJECT_ROOT / "config" / "modules" / "login_config.json").read_text(
            encoding="utf-8"
        )
    )

    assert config["browser"]["headless"] is True
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
