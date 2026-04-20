"""Login handler adapter used by the main auto notify workflow."""

from dataclasses import dataclass
from typing import Any

from autologin.scripts.login_flow import AutoLogin


@dataclass
class LoginCapture:
    cookie_dump: str | None
    opened_app_handles: dict[str, Any]
    jsessionid: str | None = None
    raw_cookie: dict[str, Any] | None = None


def login(config_path=None) -> LoginCapture:
    """Log in and return the staged Cookie JSON result."""
    autologin = AutoLogin(config_path)
    result = autologin.login_and_capture_cookies()
    return LoginCapture(
        cookie_dump=result.get("cookie_dump"),
        opened_app_handles=result.get("opened_app_handles") or {},
        jsessionid=result.get("jsessionid"),
        raw_cookie=result.get("cookie_info"),
    )
