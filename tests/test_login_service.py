from services.login_service import AutoLogin, app_page_is_ready


class FakeDriver:
    def __init__(self, url, cookies=None, ready_state="complete"):
        self.current_url = url
        self._cookies = cookies or []
        self._ready_state = ready_state

    def get_cookies(self):
        return self._cookies

    def execute_script(self, _script):
        return self._ready_state


REPORT_APP = {
    "name": "报表分析系统",
    "page_ready": {
        "url_contains": "/bicpreport/",
        "cookie_names": ["ssr-token"],
    },
}


def test_app_page_is_not_ready_while_popup_is_about_blank():
    driver = FakeDriver("about:blank")

    assert app_page_is_ready(driver, REPORT_APP) is False


def test_app_page_is_not_ready_before_required_cookie_arrives():
    driver = FakeDriver("https://usm.ha.cmcc:19011/bicpreport/index.html")

    assert app_page_is_ready(driver, REPORT_APP) is False


def test_app_page_is_ready_after_url_and_cookie_are_available():
    driver = FakeDriver(
        "https://usm.ha.cmcc:19011/bicpreport/index.html",
        cookies=[{"name": "ssr-token", "value": "token"}],
    )

    assert app_page_is_ready(driver, REPORT_APP) is True


def test_app_page_is_not_ready_if_same_window_has_not_navigated():
    url = "https://usm.ha.cmcc:19011/console/jf-index.html#/home"
    driver = FakeDriver(url)

    assert app_page_is_ready(driver, {"name": "数据超市"}, previous_url=url) is False


def test_capture_retries_app_after_transient_initialization_failure():
    login = AutoLogin.__new__(AutoLogin)
    login.config = {
        "usm_cookie_apps": [
            {
                "stage": "report_analysis",
                "name": "报表分析系统",
                "capture_attempts": 3,
                "capture_retry_seconds": 0.001,
            }
        ]
    }
    login.driver = type("Driver", (), {"window_handles": ["usm"]})()
    login.usm_window_handle = None
    login.opened_app_handles = {}
    attempts = []
    captured = []

    def enter_app(_app_config):
        attempts.append(1)
        if len(attempts) < 3:
            raise RuntimeError("transient failure")
        return "report-window"

    login.enter_usm_app = enter_app
    login.close_failed_app_windows = lambda _handles: None
    login.wait_for_storage_ready = lambda _app_config: None
    login.capture_cookies = lambda stage: captured.append(stage)

    login.capture_usm_apps_cookies()

    assert len(attempts) == 3
    assert captured == ["report_analysis"]
    assert login.opened_app_handles["report_analysis"] == "report-window"
