from __future__ import annotations

import json
import os
import re
import signal
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib import parse as urllib_parse
from urllib import request as urllib_request
from urllib.parse import urljoin, urlparse

import requests
import urllib3
from selenium import webdriver
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.edge.options import Options
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from urllib3.exceptions import InsecureRequestWarning


urllib3.disable_warnings(InsecureRequestWarning)


@dataclass(frozen=True)
class ExperimentCase:
    name: str
    headless: bool
    retain_browser: bool
    heartbeat_seconds: int | None


@dataclass(frozen=True)
class OtpWaitContext:
    trigger_time: datetime
    latest_message_id: int | None


@dataclass(frozen=True)
class OtpResult:
    code: str
    message_id: int | None


class OwnedBrowserExited(RuntimeError):
    pass


CASES: tuple[ExperimentCase, ...] = (
    ExperimentCase("headless_retained_idle", True, True, None),
    ExperimentCase("headed_closed_idle", False, False, None),
    ExperimentCase("headed_retained_idle", False, True, None),
    ExperimentCase("headless_closed_heartbeat", True, False, 300),
)

PROJECT_DIR = Path(__file__).resolve().parent
RESULTS_ROOT = PROJECT_DIR / "session_lifetime_results"
PROBE_TIMEOUT_SECONDS = 8
PROBE_STAGE_ORDER = ("report_analysis", "smart_ops", "city_ops")
_ACTIVE_RUN_DIR: Path | None = None
_ACTIVE_SUMMARY: dict | None = None


def _now() -> datetime:
    return datetime.now(timezone.utc).astimezone()


def _sleep(seconds: float) -> None:
    time.sleep(seconds)


def _raise_keyboard_interrupt(signum: int, frame: object) -> None:
    raise KeyboardInterrupt


def _install_signal_handlers() -> dict[int, object]:
    previous = {signal.SIGINT: signal.getsignal(signal.SIGINT)}
    signal.signal(signal.SIGINT, _raise_keyboard_interrupt)
    if hasattr(signal, "SIGBREAK"):
        previous[signal.SIGBREAK] = signal.getsignal(signal.SIGBREAK)
        signal.signal(signal.SIGBREAK, _raise_keyboard_interrupt)
    return previous


def _restore_signal_handlers(previous: dict[int, object]) -> None:
    for signum, handler in previous.items():
        signal.signal(signum, handler)


def _deep_merge(base: dict, override: dict) -> dict:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _load_private_config() -> dict:
    config_path = Path(
        os.environ.get(
            "SESSION_LIFETIME_CONFIG_PATH",
            PROJECT_DIR / "config" / "modules" / "login_config.json",
        )
    )
    local_config_path = Path(
        os.environ.get(
            "SESSION_LIFETIME_LOCAL_CONFIG_PATH",
            config_path.with_name(f"{config_path.stem}.local{config_path.suffix}"),
        )
    )
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if local_config_path.exists():
        local_config = json.loads(local_config_path.read_text(encoding="utf-8"))
        config = _deep_merge(config, local_config)

    credentials = config.setdefault("credentials", {})
    otp_config = config.setdefault("otp_config", {})
    environment_overrides = (
        (credentials, "username", "AUTO_NOTIFY_USERNAME"),
        (credentials, "password", "AUTO_NOTIFY_PASSWORD"),
        (otp_config, "gotify_url", "GOTIFY_URL"),
        (otp_config, "client_token", "GOTIFY_CLIENT_TOKEN"),
    )
    for target, key, environment_name in environment_overrides:
        value = os.environ.get(environment_name)
        if value:
            target[key] = value

    # The lifetime experiment intentionally retains the browser profile between
    # cases unless an individual case closes its browser.
    config.setdefault("browser", {})["clear_profile_before_start"] = False
    return config


def _validate_private_config(config: dict) -> None:
    required_values = {
        "credentials.username": (config.get("credentials") or {}).get("username"),
        "credentials.password": (config.get("credentials") or {}).get("password"),
    }
    if str(config.get("otp_mode", "gotify")).lower() == "gotify":
        required_values.update(
            {
                "otp_config.gotify_url": (config.get("otp_config") or {}).get("gotify_url"),
                "otp_config.client_token": (config.get("otp_config") or {}).get("client_token"),
            }
        )
    missing = [name for name, value in required_values.items() if not value]
    if missing:
        raise RuntimeError(
            "缺少 session lifetime 登录配置: "
            + ", ".join(missing)
            + "；请填写忽略提交的 login_config.local.json 或对应环境变量"
        )


PRIVATE_CONFIG = _load_private_config()
PROBE_DEFINITIONS = {
    "report_analysis": {
        "method": "POST",
        "url": "https://usm.ha.cmcc:19011/bicpreport/client/getLoginName",
        "headers": {
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/x-www-form-urlencoded",
            "Origin": "https://usm.ha.cmcc:19011",
            "Referer": "https://usm.ha.cmcc:19011/bicpreport/index.html",
        },
        "cookie_header": ("Ssr-Token", "ssr-token"),
        "body_key": "data",
        "success_json": ("returnCode", "0"),
    },
    "smart_ops": {
        "method": "GET",
        "url": "https://usm.ha.cmcc:19011/zhyypt/smop/base/refresh",
        "headers": {
            "Accept": "*/*",
            "Referer": "https://usm.ha.cmcc:19011/zhyypt/dist/",
        },
        "storage_header": ("user-info", "zhyyptInfo.accessToken"),
        "allow_empty_body": True,
    },
    "city_ops": {
        "method": "POST",
        "url": "https://usm.ha.cmcc:19011/dszzCombat/dszzRestful/combatreal/base/getUserInfo",
        "headers": {
            "Accept": "*/*",
            "Content-Type": "application/json",
            "Origin": "https://usm.ha.cmcc:19011",
            "Referer": "https://usm.ha.cmcc:19011/dszzCombat/dszzWeb/h5combatplatform/",
        },
        "storage_header": ("Uaptoken", "uapToken"),
        "body_key": "json",
        "success_json": ("reCode", "0000"),
    },
}

AUTH_PATH_SUFFIXES = (
    "/uac/web3/jsp/login/login.jsp",
    "/login/login.jsp",
    "/login.jsp",
    "/logout.action",
    "/kickedout",
)
AUTH_BODY_MARKERS = (
    "单点登录超时",
    "请重新登录",
    "请登录",
    "logging down",
    "login.jsp",
    "session_expired",
    "recode=1101",
    "login required",
    "login-required",
    "login_required",
)

SENSITIVE_VALUES: tuple[str, ...] = tuple(
    str(value)
    for value in (
        *(PRIVATE_CONFIG.get("credentials") or {}).values(),
        (PRIVATE_CONFIG.get("otp_config") or {}).get("client_token"),
    )
    if value
)
SENSITIVE_KEY_MARKERS = frozenset(
    {
        "authorization",
        "cookie",
        "credential",
        "otp",
        "passwd",
        "password",
        "secret",
        "storage",
        "token",
    }
)


def next_fixed_probe_time(now: datetime, interval_minutes: int = 15) -> datetime:
    if interval_minutes <= 0 or interval_minutes > 60 or 60 % interval_minutes != 0:
        raise ValueError(
            "interval_minutes must be between 1 and 60 and divide 60 evenly"
        )

    aligned_minute = (now.minute // interval_minutes) * interval_minutes
    candidate = now.replace(minute=aligned_minute, second=0, microsecond=0)
    if candidate <= now:
        candidate += timedelta(minutes=interval_minutes)
    return candidate


def next_probe_decision(previous_failures: int, classification: str) -> dict:
    if classification == "valid":
        return {"failures": 0, "advance": False, "retry_seconds": None}
    if classification == "infrastructure_failure":
        return {
            "failures": previous_failures,
            "advance": False,
            "retry_seconds": 180,
        }
    if classification == "authentication_failure":
        failures = previous_failures + 1
        advance = failures >= 2
        return {
            "failures": failures,
            "advance": advance,
            "retry_seconds": None if advance else 180,
        }
    raise ValueError(f"unknown probe classification: {classification}")


def redact(value: object) -> object:
    if isinstance(value, dict):
        return {
            key: "[REDACTED]" if _is_sensitive_key(key) else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact(item) for item in value)
    if isinstance(value, str):
        redacted = value
        for sensitive_value in sorted(
            (item for item in SENSITIVE_VALUES if item), key=len, reverse=True
        ):
            redacted = redacted.replace(sensitive_value, "[REDACTED]")
        return redacted
    return value


def _is_sensitive_key(key: object) -> bool:
    normalized = "".join(
        character for character in str(key).lower() if character.isalnum()
    )
    return any(marker in normalized for marker in SENSITIVE_KEY_MARKERS)


def read_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            json.dump(redact(payload), handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _write_private_json_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def build_driver(case: ExperimentCase, case_dir: Path) -> webdriver.Edge:
    browser = PRIVATE_CONFIG["browser"]
    profile_dir = case_dir / "edge_profile"
    profile_dir.mkdir(parents=True, exist_ok=True)
    options = Options()
    options.page_load_strategy = browser.get("page_load_strategy", "eager") or "eager"
    options.add_argument(f"--user-data-dir={profile_dir}")
    if case.headless:
        options.add_argument("--headless=new")
    if case.retain_browser:
        options.add_experimental_option("detach", True)
    for argument in (
        "--ignore-certificate-errors",
        "--allow-insecure-localhost",
        "--disable-web-security",
        "--disable-logging",
        "--log-level=3",
        "--silent",
        "--no-sandbox",
        "--disable-dev-shm-usage",
        "--disable-gpu",
    ):
        options.add_argument(argument)
    width = int(browser.get("window_width", 1920) or 1920)
    height = int(browser.get("window_height", 1080) or 1080)
    options.add_argument(f"--window-size={width},{height}")

    driver = webdriver.Edge(options=options)
    driver.set_page_load_timeout(
        int(browser.get("page_load_timeout_seconds", 60) or 60)
    )
    driver.set_script_timeout(int(browser.get("script_timeout_seconds", 15) or 15))
    driver.implicitly_wait(float(browser.get("implicit_wait", 0) or 0))
    return driver


def _fetch_otp_messages() -> list[dict]:
    config = PRIVATE_CONFIG["otp_config"]
    base_url = str(config["gotify_url"]).rstrip("/")
    query = urllib_parse.urlencode({"limit": int(config.get("fetch_limit", 20))})
    request = urllib_request.Request(f"{base_url}/message?{query}")
    request.add_header("X-Gotify-Key", str(config["client_token"]))
    opener = urllib_request.build_opener(urllib_request.ProxyHandler({}))
    with opener.open(
        request, timeout=int(config.get("request_timeout_seconds", 10))
    ) as response:
        payload = json.loads(response.read().decode("utf-8"))
    return list(payload.get("messages") or [])


def fetch_otp(triggered_after: datetime) -> str:
    if str(PRIVATE_CONFIG.get("otp_mode", "gotify")).lower() != "gotify":
        raise RuntimeError("standalone runner supports only the configured Gotify mode")
    result = _wait_for_otp(
        OtpWaitContext(triggered_after, None), PRIVATE_CONFIG["otp_config"]
    )
    return result.code


def _wait_for_otp(context: OtpWaitContext, config: dict) -> OtpResult:
    deadline = time.monotonic() + float(config.get("timeout_seconds", 180))
    poll_interval = max(0.0, float(config.get("poll_interval_seconds", 3)))
    while True:
        try:
            messages = _fetch_otp_messages()
        except (OSError, TimeoutError, json.JSONDecodeError, KeyError):
            messages = []
        for message in messages:
            message_id = message.get("id")
            if (
                config.get("require_new_message", True)
                and context.latest_message_id is not None
                and isinstance(message_id, int)
                and message_id <= context.latest_message_id
            ):
                continue
            code = _matching_otp(message, config, context.trigger_time)
            if code:
                return OtpResult(
                    code, message_id if isinstance(message_id, int) else None
                )
        if time.monotonic() >= deadline:
            break
        time.sleep(poll_interval)
    raise TimeoutError("configured OTP source did not return a matching code in time")


def _delete_otp_message(message_id: int) -> None:
    config = PRIVATE_CONFIG["otp_config"]
    base_url = str(config["gotify_url"]).rstrip("/")
    request = urllib_request.Request(
        f"{base_url}/message/{message_id}", method="DELETE"
    )
    request.add_header("X-Gotify-Key", str(config["client_token"]))
    opener = urllib_request.build_opener(urllib_request.ProxyHandler({}))
    with opener.open(request, timeout=int(config.get("request_timeout_seconds", 10))):
        return None


def _matching_otp(message: dict, config: dict, triggered_after: datetime) -> str | None:
    title = str(message.get("title") or "")
    body = str(message.get("message") or "")
    text = f"{title}\n{body}"
    if config.get("title_prefix") and not title.startswith(config["title_prefix"]):
        return None
    if config.get("allowed_senders") and not any(
        str(sender) in text for sender in config["allowed_senders"]
    ):
        return None
    keywords = [str(item) for item in config.get("required_keywords") or []]
    if keywords and not any(keyword in text for keyword in keywords):
        return None
    message_time = _parse_otp_time(message.get("date"))
    trigger_utc = triggered_after.astimezone(timezone.utc)
    grace = timedelta(seconds=float(config.get("trigger_grace_seconds", 30)))
    ttl = timedelta(seconds=float(config.get("message_ttl_seconds", 600)))
    if message_time and not (trigger_utc - grace <= message_time <= trigger_utc + ttl):
        return None
    matches = list(
        re.finditer(config.get("code_regex", r"(?<!\d)(\d{4,8})(?!\d)"), text)
    )
    if not matches:
        return None
    candidates = [
        (match, match.group(1) if match.groups() else match.group(0))
        for match in matches
    ]
    keyword_positions = [
        found.start()
        for keyword in keywords
        for found in re.finditer(re.escape(keyword), text)
    ]
    if keyword_positions:
        return min(
            candidates,
            key=lambda candidate: min(
                abs(candidate[0].start() - position) for position in keyword_positions
            ),
        )[1]
    for preferred_length in config.get("preferred_code_lengths") or []:
        for _, value in candidates:
            if len(value) == int(preferred_length):
                return value
    return candidates[0][1]


def _parse_otp_time(value: object) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def build_cookie_dump(captured_stages: list[dict]) -> dict:
    by_name = {stage.get("stage"): stage for stage in captured_stages}
    missing = [stage for stage in PROBE_STAGE_ORDER if stage not in by_name]
    if missing:
        raise RuntimeError(f"required application stages were not captured: {missing}")
    return {
        "generated_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "login_completed_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "stages": [by_name[stage] for stage in PROBE_STAGE_ORDER],
    }


def build_public_snapshot(cookie_dump: dict) -> dict:
    snapshot = redact(cookie_dump)
    for stage in snapshot.get("stages", []):
        url = stage.get("url")
        if isinstance(url, str):
            parsed = urllib_parse.urlsplit(url)
            stage["url"] = urllib_parse.urlunsplit(
                (parsed.scheme, parsed.netloc, parsed.path, "", "")
            )
    return snapshot


def login_and_capture(
    case: ExperimentCase, case_dir: Path
) -> tuple[dict, webdriver.Edge | None]:
    driver = build_driver(case, case_dir)
    try:
        dump = build_cookie_dump(_perform_login_and_capture(driver))
        _write_private_json_atomic(case_dir / "cookie_dump.json", dump)
        write_json_atomic(
            case_dir / "session_snapshot.json", build_public_snapshot(dump)
        )
        if case.retain_browser:
            return dump, driver
        driver.quit()
        return dump, None
    except KeyboardInterrupt:
        try:
            driver.quit()
        except Exception:
            pass
        raise
    except Exception:
        driver.quit()
        raise


def _perform_login_and_capture(driver: webdriver.Edge) -> list[dict]:
    return _StandaloneLogin(driver).run()


class _StandaloneLogin:
    def __init__(self, driver: webdriver.Edge):
        self.driver = driver
        self.usm_handle: str | None = None
        self.app_handles: dict[str, str] = {}
        self.otp_wait_context: OtpWaitContext | None = None
        self.pending_otp_message_id: int | None = None

    def run(self) -> list[dict]:
        self._login_ngboss()
        self._open_usm_console()
        stages = []
        for app in PRIVATE_CONFIG["usm_cookie_apps"]:
            if app.get("stage") not in PROBE_STAGE_ORDER:
                continue
            max_attempts = int(app.get("capture_attempts", 3) or 3)
            last_error: Exception | None = None
            for attempt in range(1, max_attempts + 1):
                previous_handles = set(self.driver.window_handles)
                handle = None
                try:
                    handle = self._open_app(app)
                    self.app_handles[app["stage"]] = handle
                    self._wait_for_app_navigation(app)
                    self._wait_auth_ready(app)
                    stages.append(self._capture_stage(app["stage"]))
                    last_error = None
                    break
                except Exception as exc:
                    last_error = exc
                    self._close_failed_app_windows(previous_handles, handle)
                    self.app_handles.pop(app["stage"], None)
                    if attempt < max_attempts:
                        time.sleep(float(app.get("capture_retry_seconds", 2) or 2))
            if last_error is not None:
                raise RuntimeError(
                    f"{app['name']} capture failed after {max_attempts} attempts"
                ) from last_error
        return stages

    def _close_failed_app_windows(
        self, previous_handles: set[str], attempted_handle: str | None
    ) -> None:
        try:
            current_handles = list(self.driver.window_handles)
        except Exception:
            return
        failed_handles = [
            handle
            for handle in current_handles
            if handle not in previous_handles or handle == attempted_handle
        ]
        for handle in failed_handles:
            if handle == self.usm_handle:
                continue
            try:
                self.driver.switch_to.window(handle)
                self.driver.close()
            except Exception:
                continue
        if self.usm_handle and self.usm_handle in self.driver.window_handles:
            self.driver.switch_to.window(self.usm_handle)

    def _login_ngboss(self) -> None:
        self.driver.get(PRIVATE_CONFIG["login_url"])
        state = self._wait_for_login_entry_or_home()
        if state == "home":
            return
        self._fill_login_credentials()
        self.otp_wait_context = self._prepare_otp_wait_context()
        self._click((By.ID, "login_btn"))
        self._confirm_existing_session()
        otp = self._get_sms_code()
        otp_input = WebDriverWait(self.driver, 10).until(
            EC.presence_of_element_located((By.ID, "smKey"))
        )
        otp_input.click()
        otp_input.clear()
        otp_input.send_keys(otp)
        self._click((By.ID, "login_btn"))
        self._wait_ngboss_home()

    def _fill_login_credentials(self) -> None:
        credentials = PRIVATE_CONFIG["credentials"]
        username = WebDriverWait(self.driver, 30).until(
            EC.presence_of_element_located((By.ID, "loginName"))
        )
        self._set_input(username, credentials["username"])
        self._confirm_terminal_tool_dialog_if_present()
        try:
            password_tab = self.driver.find_element(
                By.CSS_SELECTOR, "#pwdDiv > .main_tab_l"
            )
            self.driver.execute_script("arguments[0].click();", password_tab)
        except Exception:
            pass
        self.driver.execute_script(
            """
            const password = document.getElementById('loginPassword');
            const placeholder = document.getElementById('passwd_input_placeholder');
            const passwordDiv = document.getElementById('pwdDiv');
            if (passwordDiv) passwordDiv.style.display = 'block';
            if (placeholder) placeholder.style.display = 'none';
            if (password) {
              password.style.display = '';
              password.removeAttribute('disabled');
              password.removeAttribute('readonly');
            }
            """
        )
        password = WebDriverWait(self.driver, 30).until(self._password_input)
        self._set_input(password, credentials["password"])

    def _wait_for_login_entry_or_home(
        self, timeout: float = 30, attempts: int = 2
    ) -> str:
        for attempt in range(1, attempts + 1):
            try:
                return WebDriverWait(self.driver, timeout).until(self._login_state)
            except TimeoutException:
                if attempt >= attempts:
                    raise RuntimeError(
                        "login entry or NGBOSS home did not become available"
                    )
                if self._is_no_permission_page():
                    self._reset_ngboss_login_state()
                else:
                    self.driver.get(PRIVATE_CONFIG["login_url"])
        raise RuntimeError("login entry state machine exhausted")

    def _login_state(self, driver: webdriver.Edge) -> str | bool:
        if self._is_no_permission_page():
            return False
        if (
            self._confirm_terminal_tool_dialog_if_present()
            and self._terminal_tool_dialog_is_visible()
        ):
            return False
        if driver.find_elements(By.ID, "buttonList"):
            return "home"
        for element in driver.find_elements(By.ID, "loginName"):
            if element.is_displayed() and element.is_enabled():
                return "login"
        return False

    def _is_no_permission_page(self) -> bool:
        try:
            current_url = self.driver.current_url or ""
            title = self.driver.title or ""
            body = self.driver.find_element(By.TAG_NAME, "body").text or ""
        except Exception:
            return False
        return (
            "nopermission.jsp" in current_url
            or title == "系统异常"
            or "网络环境发生变化" in body
            or "安全检查失败" in body
        )

    def _reset_ngboss_login_state(self) -> None:
        self.driver.get(PRIVATE_CONFIG["logout_url"])
        time.sleep(1)
        self.driver.get(PRIVATE_CONFIG["login_url"])

    def _terminal_tool_dialog_is_visible(self) -> bool:
        try:
            dialogs = self.driver.find_elements(By.ID, "jMsgboxBox")
        except Exception:
            return False
        for dialog in dialogs:
            try:
                if dialog.is_displayed() and (
                    "多终端工具" in (dialog.text or "")
                    or "没有安装或运行" in (dialog.text or "")
                ):
                    return True
            except Exception:
                continue
        return False

    def _confirm_terminal_tool_dialog_if_present(self) -> bool:
        dialogs = self.driver.find_elements(By.ID, "jMsgboxBox")
        for dialog in dialogs:
            try:
                text = (dialog.text or "").strip()
                if not dialog.is_displayed() or (
                    "多终端工具" not in text and "没有安装或运行" not in text
                ):
                    continue
            except Exception:
                continue
            buttons = []
            for locator in (
                (By.CSS_SELECTOR, "#jMsgboxBox .msgbox_button"),
                (
                    By.XPATH,
                    "//*[@id='jMsgboxBox']//input[@type='button' and "
                    "(contains(@value,'确') or contains(@value,'关'))]",
                ),
                (
                    By.XPATH,
                    "//*[@id='jMsgboxBox']//*[self::button or self::input]"
                    "[contains(.,'确认') or contains(@value,'确认')]",
                ),
            ):
                buttons.extend(self.driver.find_elements(*locator))
            for button in buttons:
                try:
                    self.driver.execute_script(
                        "arguments[0].scrollIntoView({block:'center'});", button
                    )
                    self.driver.execute_script(
                        """
                        const el = arguments[0];
                        for (const type of ['mouseover', 'mousedown', 'mouseup', 'click']) {
                          el.dispatchEvent(new MouseEvent(type, {bubbles: true}));
                        }
                        if (typeof el.click === 'function') el.click();
                        """,
                        button,
                    )
                    WebDriverWait(self.driver, 3).until_not(
                        lambda _: self._terminal_tool_dialog_is_visible()
                    )
                    return True
                except Exception:
                    continue
            return True
        return False

    def _password_input(self, driver: webdriver.Edge) -> object:
        candidates = []
        for locator in (
            (By.ID, "loginPassword"),
            (By.CSS_SELECTOR, "input[type='password']"),
        ):
            candidates.extend(driver.find_elements(*locator))
        return next(
            (item for item in candidates if item.is_displayed() and item.is_enabled()),
            False,
        )

    def _set_input(self, element: object, value: str) -> None:
        self.driver.execute_script(
            """
            const el = arguments[0];
            const value = arguments[1];
            el.removeAttribute('disabled');
            el.removeAttribute('readonly');
            el.focus();
            el.value = '';
            el.dispatchEvent(new Event('input', {bubbles: true}));
            el.value = value;
            for (const type of ['input', 'change', 'keyup', 'blur']) {
              el.dispatchEvent(new Event(type, {bubbles: true}));
            }
            """,
            element,
            value,
        )

    def _confirm_existing_session(self) -> None:
        def visible_dialog(driver: webdriver.Edge) -> object:
            return next(
                (
                    item
                    for item in driver.find_elements(By.ID, "jMsgboxBox")
                    if item.is_displayed()
                ),
                False,
            )

        try:
            timeout = float(
                PRIVATE_CONFIG.get("login_waits", {}).get(
                    "existing_session_dialog_seconds", 1.5
                )
            )
            dialog = WebDriverWait(self.driver, timeout, poll_frequency=0.2).until(
                visible_dialog
            )
            dialog.find_element(
                By.XPATH,
                ".//input[@class='msgbox_button' and contains(@value,'确')]",
            ).click()
        except TimeoutException:
            pass
        except Exception:
            print("[WARN] existing-session confirmation failed")

    def _wait_ngboss_home(self) -> None:
        self._wait_for_home_element()
        self._ack_otp_message()

    def _wait_for_home_element(self) -> None:
        xpath = (
            "//*[@id='buttonList' or @id='saveDrsCfgBtn' or @id='confirmBtn' "
            "or @id='agreeBtn' or (self::input and contains(@value,'确')) "
            "or (self::input and contains(@value,'同意')) "
            "or (self::button and contains(text(),'确认')) "
            "or (self::button and contains(text(),'同意')) "
            "or (self::input and @type='submit')]"
        )
        first = WebDriverWait(self.driver, 30).until(
            EC.presence_of_element_located((By.XPATH, xpath))
        )
        if (first.get_attribute("id") or "") != "buttonList":
            self.driver.execute_script("arguments[0].click();", first)
            WebDriverWait(self.driver, 20).until(
                EC.presence_of_element_located((By.ID, "buttonList"))
            )

    def _prepare_otp_wait_context(self) -> OtpWaitContext:
        try:
            messages = _fetch_otp_messages()
        except (OSError, TimeoutError, json.JSONDecodeError, KeyError):
            messages = []
        latest_id = max(
            (
                message["id"]
                for message in messages
                if isinstance(message.get("id"), int)
            ),
            default=None,
        )
        return OtpWaitContext(datetime.now(timezone.utc), latest_id)

    def _request_sms_code(self, retry_index: int) -> bool:
        wait_seconds = 10 if retry_index == 0 else 20
        try:
            WebDriverWait(self.driver, wait_seconds).until(
                EC.element_to_be_clickable((By.ID, "smsAuthen_div"))
            ).click()
            time.sleep(1)
            return True
        except Exception:
            return False

    def _get_sms_code(self) -> str:
        config = PRIVATE_CONFIG["otp_config"]
        max_requests = int(config.get("max_request_count", 3))
        retry_interval = float(config.get("retry_interval_seconds", 5))
        last_error: TimeoutError | None = None
        for retry_index in range(max_requests):
            if retry_index == 0:
                context = self.otp_wait_context or self._prepare_otp_wait_context()
            else:
                context = self._prepare_otp_wait_context()
                self.otp_wait_context = context
                self._request_sms_code(retry_index)
            try:
                result = _wait_for_otp(context, config)
                self.pending_otp_message_id = result.message_id
                return result.code
            except TimeoutError as exc:
                last_error = exc
                if retry_index < max_requests - 1:
                    time.sleep(retry_interval)
        raise TimeoutError(f"OTP attempts exhausted: {last_error}")

    def _ack_otp_message(self) -> None:
        config = PRIVATE_CONFIG.get("otp_config") or {}
        message_id = self.pending_otp_message_id
        if not config.get("delete_after_success", True) or not message_id:
            return
        try:
            _delete_otp_message(message_id)
        except Exception:
            return
        self.pending_otp_message_id = None

    def _open_usm_console(self) -> None:
        entry = None
        for locator in (
            (By.ID, "p1050000"),
            (By.XPATH, "//li[@id='p1050000']"),
            (By.XPATH, "//li[contains(@href, 'appRes3.action')]"),
            (
                By.XPATH,
                "//*[@href='/uac/web3/jsp/resource/app/appRes3.action' "
                "or contains(@href, 'appRes3.action')]",
            ),
            (By.XPATH, "//li[contains(text(),'应用登录')]"),
            (By.CSS_SELECTOR, "li#p1050000"),
        ):
            try:
                entry = WebDriverWait(self.driver, 15).until(
                    EC.element_to_be_clickable(locator)
                )
                break
            except TimeoutException:
                continue
        if entry is None:
            raise RuntimeError("application login entry was not found")
        entry.click()
        self.driver.switch_to.default_content()
        frame = WebDriverWait(self.driver, 30).until(
            EC.presence_of_element_located(
                (
                    By.XPATH,
                    "//iframe[@id='title5tab' or contains(@src,'appRes3.action')]",
                )
            )
        )
        self.driver.switch_to.frame(frame)
        name = PRIVATE_CONFIG["app_login_entry"]["name"]
        card = WebDriverWait(self.driver, 30).until(
            EC.presence_of_element_located(
                (
                    By.XPATH,
                    "//div[starts-with(@id,'appLogin_div') and contains(., "
                    f"{_xpath_literal(name)})]",
                )
            )
        )
        suffix = (card.get_attribute("id") or "").replace("appLogin_div", "")
        self.driver.execute_script("arguments[0].click();", card)
        choose = WebDriverWait(self.driver, 30).until(
            lambda driver: self._visible_by_id(driver, f"appLoginChoose_div{suffix}")
        )
        self.driver.switch_to.frame(choose.find_element(By.TAG_NAME, "iframe"))
        old_handles = set(self.driver.window_handles)
        login_button = None
        for locator in (
            (By.ID, "appAccess"),
            (
                By.XPATH,
                "//*[@id='appAccess' or contains(normalize-space(.), '登录') "
                "or contains(@value, '登录')]",
            ),
        ):
            try:
                login_button = WebDriverWait(self.driver, 15).until(
                    EC.element_to_be_clickable(locator)
                )
                break
            except TimeoutException:
                continue
        if login_button is None:
            raise RuntimeError("USM entry login button was not found")
        login_button.click()
        self._complete_usm_entry(old_handles)
        WebDriverWait(self.driver, 60).until(
            EC.presence_of_element_located((By.ID, "iFrame1"))
        )

    def _complete_usm_entry(self, old_handles: set[str]) -> str:
        try:
            WebDriverWait(self.driver, 5).until(
                lambda driver: len(driver.window_handles) > len(old_handles)
            )
            self.driver.switch_to.window(self.driver.window_handles[-1])
        except (TimeoutException, TimeoutError):
            pass
        self.driver.switch_to.default_content()
        usm_host = str(PRIVATE_CONFIG["usm_host"])
        WebDriverWait(self.driver, 30, poll_frequency=0.1).until(
            lambda driver: usm_host in (driver.current_url or "")
        )
        self.usm_handle = self.driver.current_window_handle
        return self.usm_handle

    def _open_app(self, app: dict) -> str:
        source_stage = app.get("source_stage")
        if source_stage:
            if source_stage == "usm_console":
                self._switch_to_usm_list()
            else:
                source_handle = self.app_handles.get(source_stage)
                if not source_handle:
                    raise RuntimeError(f"missing source stage: {source_stage}")
                self.driver.switch_to.window(source_handle)
                self.driver.switch_to.default_content()
            icon = app["icon_src_contains"]
            icon_xpath = f"//img[contains(@src, '{icon}')]"
            locators = (
                (
                    By.XPATH,
                    f"{icon_xpath}/ancestor::li[contains(@class,'el-menu-item')][1]",
                ),
                (By.XPATH, icon_xpath),
            )
        else:
            self._switch_to_usm_list()
            locators = (
                (
                    By.XPATH,
                    "//div[contains(@class,'customizedList') and "
                    f"contains(@class,'module')][contains(., {_xpath_literal(app['name'])})]",
                ),
            )
        old_handles = set(self.driver.window_handles)
        element = self._find_clickable(locators)
        self.driver.execute_script("arguments[0].click();", element)
        handle = self._wait_new_window(old_handles, app)
        self.driver.switch_to.window(handle)
        return handle

    def _find_clickable(self, locators: tuple, timeout: float = 30) -> object:
        for locator in locators:
            try:
                return WebDriverWait(self.driver, timeout, poll_frequency=0.1).until(
                    EC.element_to_be_clickable(locator)
                )
            except TimeoutException:
                continue
        raise RuntimeError("configured application entry was not found")

    def _switch_to_usm_list(self) -> None:
        if not self.usm_handle:
            raise RuntimeError("USM console window is unavailable")
        self.driver.switch_to.window(self.usm_handle)
        self.driver.switch_to.default_content()
        frame = WebDriverWait(self.driver, 30).until(
            EC.presence_of_element_located((By.ID, "iFrame1"))
        )
        self.driver.switch_to.frame(frame)

    def _wait_new_window(self, old_handles: set[str], app: dict) -> str:
        expected_url = str(app.get("url_contains") or "").strip()
        timeout = float(app.get("navigation_timeout_seconds", 60) or 60)

        def matching(driver: webdriver.Edge) -> str | bool:
            for handle in driver.window_handles:
                if handle in old_handles:
                    continue
                driver.switch_to.window(handle)
                current_url = driver.current_url or ""
                if (expected_url and expected_url in current_url) or (
                    not expected_url and current_url != "about:blank"
                ):
                    return handle
            return False

        return WebDriverWait(self.driver, timeout, poll_frequency=0.2).until(matching)

    def _wait_for_app_navigation(self, app: dict) -> None:
        expected = str(app.get("url_contains") or "").strip()
        timeout = float(app.get("navigation_timeout_seconds", 30) or 30)
        if expected:
            WebDriverWait(self.driver, timeout, poll_frequency=0.1).until(
                lambda driver: self._url_matches(driver, expected)
            )
        else:
            WebDriverWait(self.driver, timeout, poll_frequency=0.1).until(
                lambda driver: driver.execute_script("return document.readyState")
                == "complete"
            )

    @staticmethod
    def _url_matches(driver: webdriver.Edge, expected: str) -> bool:
        return expected in (driver.current_url or "")

    def _wait_auth_ready(self, app: dict) -> None:
        storage_ready = app.get("storage_ready")
        if storage_ready:
            script = """
                const parts = arguments[0].split('.');
                let value = window.sessionStorage.getItem(parts.shift());
                for (const part of parts) {
                  if (!value) return '';
                  try { value = JSON.parse(value); } catch (error) { return ''; }
                  value = value ? value[part] : '';
                }
                return value || '';
            """
            WebDriverWait(
                self.driver, int(storage_ready.get("timeout_seconds", 30))
            ).until(lambda driver: driver.execute_script(script, storage_ready["path"]))
        WebDriverWait(self.driver, 45, poll_frequency=0.2).until(
            lambda driver: any(
                cookie.get("value") for cookie in (driver.get_cookies() or [])
            )
        )

    def _capture_stage(self, stage: str) -> dict:
        storage = self.driver.execute_script(
            """
            const values = {};
            for (let index = 0; index < window.sessionStorage.length; index += 1) {
              const key = window.sessionStorage.key(index);
              values[key] = window.sessionStorage.getItem(key);
            }
            const url = new URL(window.location.href);
            const hashQuery = (url.hash.split('?')[1] || '');
            const hashParams = new URLSearchParams(hashQuery);
            const routeToken = url.searchParams.get('uapToken') || hashParams.get('uapToken');
            if (routeToken && !values.uapToken) values.uapToken = routeToken;
            return values;
            """
        )
        return {
            "stage": stage,
            "captured_at": datetime.now(timezone.utc).astimezone().isoformat(),
            "url": self.driver.current_url,
            "title": self.driver.title,
            "cookies": self.driver.get_cookies(),
            "session_storage": storage or {},
        }

    def _click(self, locator: tuple[str, str]) -> object:
        element = WebDriverWait(self.driver, 30).until(
            EC.element_to_be_clickable(locator)
        )
        element.click()
        return element

    @staticmethod
    def _visible_by_id(driver: webdriver.Edge, element_id: str) -> object:
        element = driver.find_element(By.ID, element_id)
        if (
            element.is_displayed()
            and element.value_of_css_property("display") != "none"
        ):
            return element
        return False


def _xpath_literal(value: str) -> str:
    if "'" not in value:
        return f"'{value}'"
    if '"' not in value:
        return f'"{value}"'
    parts = value.split("'")
    return "concat(" + ', "\'", '.join(f"'{part}'" for part in parts) + ")"


def append_jsonl(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        json.dump(redact(payload), handle, ensure_ascii=False)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())


def format_console_event(event: dict) -> str | None:
    event_type = event.get("event_type")
    case = event.get("case", "unknown")
    if event_type == "case_started":
        return f"[START] case={case} login is starting"
    if event_type == "login_completed":
        return f"[LOGIN] case={case} succeeded at={event.get('timestamp')}"
    if event_type == "aggregate_probe":
        return (
            f"[PROBE] case={case} type={event.get('probe_kind')} "
            f"result={event.get('classification')} "
            f"elapsed={event.get('elapsed_seconds')}s"
        )
    if event_type == "schedule_updated":
        return (
            f"[SCHEDULE] case={case} next_probe_kind={event.get('next_probe_kind')} "
            f"next_probe_at={event.get('next_probe_at')}"
        )
    return None


def _emit_console_event(event: dict) -> None:
    message = format_console_event(event)
    if message is not None:
        print(message, flush=True)


def _emit_event(run_dir: Path, payload: dict) -> None:
    sanitized = redact(payload)
    append_jsonl(run_dir / "events.jsonl", sanitized)
    with (run_dir / "runner.log").open("a", encoding="utf-8") as handle:
        json.dump(sanitized, handle, ensure_ascii=False)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    _update_active_summary(run_dir, sanitized)
    _emit_console_event(sanitized)


def _update_active_summary(run_dir: Path, event: dict) -> None:
    if _ACTIVE_RUN_DIR != run_dir or _ACTIVE_SUMMARY is None:
        return
    event_type = event.get("event_type")
    _ACTIVE_SUMMARY["updated_at"] = event.get("timestamp") or _now().isoformat()
    current_case = _ACTIVE_SUMMARY.get("current_case")
    if event_type == "case_started":
        current_case = {
            "name": event.get("case"),
            "status": "starting",
        }
        _ACTIVE_SUMMARY["current_case"] = current_case
    elif current_case is not None:
        if event_type == "login_completed":
            current_case["status"] = "monitoring"
            current_case["login_completed_at"] = event.get("timestamp")
            current_case["retained_browser"] = event.get("retained_browser")
        elif event_type == "aggregate_probe":
            current_case["status"] = "monitoring"
            current_case["last_probe"] = {
                "timestamp": event.get("timestamp"),
                "probe_kind": event.get("probe_kind"),
                "classification": event.get("classification"),
                "elapsed_seconds": event.get("elapsed_seconds"),
                "events": event.get("events", []),
            }
        elif event_type == "heartbeat":
            current_case["last_heartbeat"] = {
                "timestamp": event.get("timestamp"),
                "classification": event.get("classification"),
            }
        elif event_type == "case_completed":
            current_case["status"] = "completed"
        elif event_type in {"browser_exited", "browser_cleanup_failed"}:
            current_case["status"] = "failed"
        elif event_type == "runner_stopped":
            current_case["status"] = "stopped"
        current_case["last_event"] = event
    if event_type == "runner_stopped":
        _ACTIVE_SUMMARY["status"] = "stopped"
        _ACTIVE_SUMMARY["stopped_at"] = event.get("timestamp")
    elif event_type in {"runner_failed", "browser_exited"}:
        _ACTIVE_SUMMARY["status"] = "failed"
        _ACTIVE_SUMMARY["failed_at"] = event.get("timestamp")
        if current_case is not None:
            current_case["status"] = "failed"
            current_case["last_event"] = event
    write_json_atomic(run_dir / "results.json", _ACTIVE_SUMMARY)


def _record_schedule(
    run_dir: Path,
    failures: int,
    fixed_due: datetime | None,
    retry_due: datetime | None,
    heartbeat_due: datetime | None,
) -> None:
    if _ACTIVE_RUN_DIR != run_dir or _ACTIVE_SUMMARY is None:
        return
    current_case = _ACTIVE_SUMMARY.get("current_case")
    if current_case is None:
        return
    current_case["authentication_failures"] = failures
    current_case["next_probe_kind"] = "retry" if retry_due else "fixed"
    next_probe = retry_due or fixed_due
    current_case["next_probe_at"] = next_probe.isoformat() if next_probe else None
    current_case["next_heartbeat_at"] = (
        heartbeat_due.isoformat() if heartbeat_due else None
    )
    _ACTIVE_SUMMARY["updated_at"] = _now().isoformat()
    write_json_atomic(run_dir / "results.json", _ACTIVE_SUMMARY)
    _emit_console_event(
        {
            "event_type": "schedule_updated",
            "case": current_case.get("name"),
            "next_probe_kind": current_case["next_probe_kind"],
            "next_probe_at": current_case["next_probe_at"],
        }
    )


def classify_probe_result(result: dict) -> str:
    if result.get("exception") is not None:
        return "infrastructure_failure"

    status_code = result.get("status_code")
    if status_code in {401, 403} or (
        isinstance(status_code, int) and 300 <= status_code < 400
    ):
        return "authentication_failure"

    response_urls = [result.get("url"), result.get("location")]
    response_urls.extend(result.get("redirect_urls") or [])
    if any(_is_auth_page_url(value) for value in response_urls):
        return "authentication_failure"

    if _contains_auth_marker(result.get("body")):
        return "authentication_failure"

    payload = result.get("payload")
    if isinstance(payload, dict):
        code = str(payload.get("reCode") or payload.get("status") or "")
        message = str(payload.get("reMsg") or payload.get("message") or "")
        if code in {"1101", "401", "403"}:
            return "authentication_failure"
        if _contains_auth_marker(message):
            return "authentication_failure"

    return "valid" if result.get("ok") else "infrastructure_failure"


def execute_stage_probe(
    stage_name: str, snapshot: dict, session: requests.Session
) -> dict:
    definition = PROBE_DEFINITIONS.get(stage_name)
    if definition is None:
        raise ValueError(f"unknown probe stage: {stage_name}")

    timestamp = (
        snapshot.get("_probe_timestamp")
        or datetime.now(timezone.utc).astimezone().isoformat()
    )
    elapsed_seconds = int(snapshot.get("_elapsed_seconds") or 0)
    started_at = time.perf_counter()
    status_code = None
    internal_result: dict = {"ok": False}

    try:
        headers = dict(definition["headers"])
        cookie_header = definition.get("cookie_header")
        if cookie_header:
            header_name, cookie_name = cookie_header
            headers[header_name] = _find_cookie(snapshot, cookie_name)
        storage_header = definition.get("storage_header")
        if storage_header:
            header_name, storage_path = storage_header
            headers[header_name] = _find_storage(snapshot, storage_path)

        kwargs: dict = {
            "headers": headers,
            "cookies": _build_cookie_jar(snapshot),
            "timeout": PROBE_TIMEOUT_SECONDS,
            "allow_redirects": False,
            "verify": False,
        }
        body_key = definition.get("body_key")
        if body_key:
            kwargs[body_key] = {}

        response = session.request(definition["method"], definition["url"], **kwargs)
        status_code = response.status_code
        payload = _response_json(response)
        success = 200 <= status_code < 300
        success_json = definition.get("success_json")
        if success_json:
            key, expected = success_json
            success = success and isinstance(payload, dict)
            success = success and str(payload.get(key)) == expected
        elif not definition.get("allow_empty_body") and not response.content:
            success = False

        internal_result = {
            "status_code": status_code,
            "url": getattr(response, "url", ""),
            "location": _response_location_url(response),
            "redirect_urls": _response_redirect_urls(response),
            "body": getattr(response, "text", ""),
            "payload": payload,
            "ok": success,
        }
    except requests.RequestException as exc:
        internal_result = {"ok": False, "exception": exc}
    except (KeyError, ValueError):
        internal_result = {"ok": False, "missing_authentication_material": True}

    classification = classify_probe_result(internal_result)
    reason = _probe_reason(internal_result, classification)
    return {
        "event_type": "stage_probe",
        "stage": stage_name,
        "timestamp": timestamp,
        "elapsed_seconds": elapsed_seconds,
        "status_code": status_code,
        "ok": classification == "valid",
        "classification": classification,
        "duration_seconds": round(time.perf_counter() - started_at, 6),
        "reason": reason,
    }


def probe_all_stages(
    cookie_dump: dict, session: requests.Session | None = None
) -> dict:
    probe_session = session or requests.Session()
    timestamp_dt = datetime.now(timezone.utc).astimezone()
    timestamp = timestamp_dt.isoformat()
    elapsed_seconds = _elapsed_since_login(cookie_dump, timestamp_dt)
    stages = {stage.get("stage"): stage for stage in cookie_dump.get("stages", [])}
    events = []
    for stage_name in PROBE_STAGE_ORDER:
        stage = dict(stages.get(stage_name) or {})
        stage["_probe_timestamp"] = timestamp
        stage["_elapsed_seconds"] = elapsed_seconds
        events.append(execute_stage_probe(stage_name, stage, probe_session))

    classifications = {event["classification"] for event in events}
    if "authentication_failure" in classifications:
        classification = "authentication_failure"
    elif "infrastructure_failure" in classifications:
        classification = "infrastructure_failure"
    else:
        classification = "valid"
    return {
        "event_type": "aggregate_probe",
        "timestamp": timestamp,
        "elapsed_seconds": elapsed_seconds,
        "ok": classification == "valid",
        "classification": classification,
        "events": events,
    }


def build_lifetime_result(case: ExperimentCase, events: list[dict]) -> dict:
    login_completed_at = None
    last_success = None
    pending_fixed_failure = None
    first_failure = None
    confirmation = None
    for event in events:
        if event.get("event_type") == "login_completed":
            login_completed_at = event.get("timestamp")
            continue
        if event.get("event_type") != "aggregate_probe":
            continue
        classification = event.get("classification")
        probe_kind = event.get("probe_kind")
        if classification == "valid":
            pending_fixed_failure = None
            last_success = event
            continue
        if classification != "authentication_failure":
            continue
        if probe_kind == "fixed":
            pending_fixed_failure = event
        elif probe_kind == "retry" and pending_fixed_failure is not None:
            first_failure = pending_fixed_failure
            confirmation = event
            break
    return {
        "case": case.name,
        "headless": case.headless,
        "retain_browser": case.retain_browser,
        "heartbeat_seconds": case.heartbeat_seconds,
        "login_completed_at": login_completed_at,
        "last_success_at": last_success.get("timestamp") if last_success else None,
        "first_authentication_failure_at": (
            first_failure.get("timestamp") if first_failure else None
        ),
        "confirmed_at": confirmation.get("timestamp") if confirmation else None,
        "lower_bound_seconds": (
            last_success.get("elapsed_seconds") if last_success else None
        ),
        "upper_bound_seconds": (
            first_failure.get("elapsed_seconds") if first_failure else None
        ),
        "confirmed_at_seconds": (
            confirmation.get("elapsed_seconds") if confirmation else None
        ),
        "first_failure_stages": (
            first_failure.get("events", []) if first_failure else []
        ),
        "confirmation_stages": (confirmation.get("events", []) if confirmation else []),
    }


def _find_cookie(snapshot: dict, cookie_name: str) -> str:
    for cookie in snapshot.get("cookies", []):
        if cookie.get("name") == cookie_name and cookie.get("value") is not None:
            return str(cookie["value"])
    raise KeyError(cookie_name)


def _build_cookie_jar(snapshot: dict) -> requests.cookies.RequestsCookieJar:
    jar = requests.cookies.RequestsCookieJar()
    for cookie in snapshot.get("cookies", []):
        name = cookie.get("name")
        value = cookie.get("value")
        if name and value is not None:
            cookie_kwargs = {"path": cookie.get("path") or "/"}
            if cookie.get("domain"):
                cookie_kwargs["domain"] = cookie["domain"]
            jar.set(name, str(value), **cookie_kwargs)
    return jar


def _find_storage(snapshot: dict, storage_path: str) -> str:
    parts = storage_path.split(".")
    value: object = (snapshot.get("session_storage") or {}).get(parts[0])
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            pass
    for part in parts[1:]:
        if not isinstance(value, dict):
            value = None
            break
        value = value.get(part)
    if value is None or value == "":
        raise KeyError(storage_path)
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _response_json(response: object) -> object:
    try:
        return response.json()
    except (TypeError, ValueError):
        return None


def _is_auth_page_url(value: object) -> bool:
    if not value:
        return False
    path = urlparse(str(value)).path.lower().rstrip("/")
    return any(path.endswith(suffix) for suffix in AUTH_PATH_SUFFIXES)


def _contains_auth_marker(value: object) -> bool:
    lowered = str(value or "").lower()
    return any(marker.lower() in lowered for marker in AUTH_BODY_MARKERS)


def _response_location_url(response: object) -> str:
    location = (getattr(response, "headers", {}) or {}).get("Location", "")
    if not location:
        return ""
    return urljoin(str(getattr(response, "url", "") or ""), str(location))


def _response_redirect_urls(response: object) -> list[str]:
    urls = []
    for historical_response in getattr(response, "history", []) or []:
        if getattr(historical_response, "url", None):
            urls.append(str(historical_response.url))
        location = (getattr(historical_response, "headers", {}) or {}).get("Location")
        if location:
            urls.append(
                urljoin(
                    str(getattr(historical_response, "url", "") or ""),
                    str(location),
                )
            )
    return urls


def _probe_reason(result: dict, classification: str) -> str:
    if classification == "valid":
        return "ok"
    if classification == "authentication_failure":
        return "authentication_rejected"
    exception = result.get("exception")
    if isinstance(exception, requests.exceptions.SSLError):
        return "tls_error"
    if isinstance(exception, requests.Timeout):
        return "request_timeout"
    if isinstance(exception, requests.ConnectionError):
        return "connection_error"
    if result.get("missing_authentication_material"):
        return "missing_authentication_material"
    return "unexpected_response"


def _elapsed_since_login(cookie_dump: dict, now: datetime) -> int:
    value = cookie_dump.get("login_completed_at") or cookie_dump.get("generated_at")
    if not value:
        return 0
    try:
        started_at = datetime.fromisoformat(str(value))
    except ValueError:
        return 0
    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=now.tzinfo)
    return max(0, int((now - started_at.astimezone(now.tzinfo)).total_seconds()))


def _driver_is_alive(driver: webdriver.Edge | None) -> bool:
    if driver is None:
        return True
    process = getattr(getattr(driver, "service", None), "process", None)
    if process is not None and process.poll() is not None:
        return False
    try:
        handles = list(driver.window_handles)
        current_handle = driver.current_window_handle
    except Exception:
        return False
    return bool(handles) and current_handle in handles


def _wait_until(deadline: datetime, driver: webdriver.Edge | None = None) -> None:
    while True:
        if not _driver_is_alive(driver):
            raise OwnedBrowserExited("owned retained browser exited")
        remaining = (deadline - _now()).total_seconds()
        if remaining <= 0:
            return
        _sleep(min(30, remaining))


def _record_probe(
    case: ExperimentCase,
    run_dir: Path,
    events: list[dict],
    probe_kind: str,
    probe: dict,
) -> dict:
    recorded = dict(probe)
    recorded["case"] = case.name
    recorded["probe_kind"] = probe_kind
    events.append(recorded)
    _emit_event(run_dir, recorded)
    return recorded


def run_case(case: ExperimentCase, run_dir: Path) -> dict:
    case_dir = run_dir / case.name
    case_dir.mkdir(parents=True, exist_ok=True)
    events: list[dict] = []
    driver: webdriver.Edge | None = None
    _emit_event(
        run_dir,
        {
            "event_type": "case_started",
            "case": case.name,
            "timestamp": _now().isoformat(),
        },
    )

    try:
        cookie_dump, driver = login_and_capture(case, case_dir)
        login_event = {
            "event_type": "login_completed",
            "case": case.name,
            "timestamp": cookie_dump.get("login_completed_at") or _now().isoformat(),
            "retained_browser": driver is not None,
        }
        events.append(login_event)
        _emit_event(run_dir, login_event)

        current = _now()
        heartbeat_due = (
            current + timedelta(seconds=case.heartbeat_seconds)
            if case.heartbeat_seconds is not None
            else None
        )
        probe = _record_probe(
            case, run_dir, events, "fixed", probe_all_stages(cookie_dump)
        )
        failures = 0
        decision = next_probe_decision(failures, probe["classification"])
        failures = decision["failures"]
        if decision["advance"]:
            result = build_lifetime_result(case, events)
            _emit_event(run_dir, {"event_type": "case_completed", **result})
            return result

        fixed_due = (
            next_fixed_probe_time(_now()) if decision["retry_seconds"] is None else None
        )
        retry_due = (
            _now() + timedelta(seconds=decision["retry_seconds"])
            if decision["retry_seconds"] is not None
            else None
        )
        _record_schedule(run_dir, failures, fixed_due, retry_due, heartbeat_due)

        while True:
            deadlines = [item for item in (fixed_due, retry_due, heartbeat_due) if item]
            _wait_until(min(deadlines), driver)
            current = _now()
            due_kind = None
            if retry_due is not None and retry_due <= current:
                due_kind = "retry"
            elif fixed_due is not None and fixed_due <= current:
                due_kind = "fixed"

            heartbeat_probe = None
            if heartbeat_due is not None and heartbeat_due <= current:
                heartbeat_probe = probe_all_stages(cookie_dump)
                _emit_event(
                    run_dir,
                    {
                        "event_type": "heartbeat",
                        "case": case.name,
                        "timestamp": heartbeat_probe.get("timestamp")
                        or current.isoformat(),
                        "classification": heartbeat_probe.get("classification"),
                    },
                )
                while heartbeat_due <= current:
                    heartbeat_due += timedelta(seconds=case.heartbeat_seconds)
                if due_kind is None:
                    heartbeat_kind = "heartbeat"
                    if (
                        heartbeat_probe.get("classification")
                        == "authentication_failure"
                    ):
                        heartbeat_kind = "fixed" if failures == 0 else "retry"
                    probe = _record_probe(
                        case,
                        run_dir,
                        events,
                        heartbeat_kind,
                        heartbeat_probe,
                    )
                    decision = next_probe_decision(failures, probe["classification"])
                    failures = decision["failures"]
                    if decision["advance"]:
                        result = build_lifetime_result(case, events)
                        _emit_event(
                            run_dir,
                            {"event_type": "case_completed", **result},
                        )
                        return result
                    if decision["retry_seconds"] is not None:
                        fixed_due = None
                        retry_due = current + timedelta(
                            seconds=decision["retry_seconds"]
                        )
                    else:
                        retry_due = None
                        fixed_due = next_fixed_probe_time(current)
                    _record_schedule(
                        run_dir, failures, fixed_due, retry_due, heartbeat_due
                    )
                    continue
            if due_kind is None:
                continue

            raw_probe = heartbeat_probe or probe_all_stages(cookie_dump)
            recorded_kind = due_kind
            if (
                due_kind == "retry"
                and failures == 0
                and raw_probe.get("classification") == "authentication_failure"
            ):
                recorded_kind = "fixed"
            probe = _record_probe(case, run_dir, events, recorded_kind, raw_probe)
            decision = next_probe_decision(failures, probe["classification"])
            failures = decision["failures"]
            if decision["advance"]:
                result = build_lifetime_result(case, events)
                _emit_event(
                    run_dir,
                    {"event_type": "case_completed", **result},
                )
                return result

            if decision["retry_seconds"] is not None:
                fixed_due = None
                retry_due = current + timedelta(seconds=decision["retry_seconds"])
            else:
                retry_due = None
                fixed_due = next_fixed_probe_time(current)
            _record_schedule(run_dir, failures, fixed_due, retry_due, heartbeat_due)
    except OwnedBrowserExited:
        _emit_event(
            run_dir,
            {
                "event_type": "browser_exited",
                "case": case.name,
                "timestamp": _now().isoformat(),
            },
        )
        raise
    finally:
        if driver is not None:
            exception_active = sys.exc_info()[0] is not None
            try:
                driver.quit()
            except Exception as cleanup_error:
                try:
                    _emit_event(
                        run_dir,
                        {
                            "event_type": "browser_cleanup_failed",
                            "case": case.name,
                            "timestamp": _now().isoformat(),
                            "exception_type": type(cleanup_error).__name__,
                        },
                    )
                except Exception:
                    pass
                if not exception_active:
                    raise
            else:
                try:
                    _emit_event(
                        run_dir,
                        {
                            "event_type": "browser_cleanup",
                            "case": case.name,
                            "timestamp": _now().isoformat(),
                        },
                    )
                except Exception:
                    if not exception_active:
                        raise


def run_experiments() -> dict:
    global _ACTIVE_RUN_DIR, _ACTIVE_SUMMARY
    started_at = _now()
    run_dir = RESULTS_ROOT / started_at.strftime("%Y%m%d_%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=False)
    _ACTIVE_RUN_DIR = run_dir
    summary = {
        "started_at": started_at.isoformat(),
        "updated_at": started_at.isoformat(),
        "run_dir": str(run_dir),
        "status": "running",
        "current_case": None,
        "results": [],
    }
    _ACTIVE_SUMMARY = summary
    write_json_atomic(run_dir / "results.json", summary)
    for index, case in enumerate(CASES):
        summary["results"].append(run_case(case, run_dir))
        if index == len(CASES) - 1:
            summary["status"] = "completed"
            summary["completed_at"] = _now().isoformat()
        summary["updated_at"] = _now().isoformat()
        write_json_atomic(run_dir / "results.json", summary)
    return summary


def main() -> int:
    previous_signal_handlers = _install_signal_handlers()
    try:
        _validate_private_config(PRIVATE_CONFIG)
        run_experiments()
        return 0
    except KeyboardInterrupt:
        if _ACTIVE_RUN_DIR is not None:
            _emit_event(
                _ACTIVE_RUN_DIR,
                {"event_type": "runner_stopped", "timestamp": _now().isoformat()},
            )
        return 130
    except Exception as exc:
        if _ACTIVE_RUN_DIR is not None:
            _emit_event(
                _ACTIVE_RUN_DIR,
                {
                    "event_type": "runner_failed",
                    "timestamp": _now().isoformat(),
                    "exception_type": type(exc).__name__,
                },
            )
        return 1
    finally:
        _restore_signal_handlers(previous_signal_handlers)


if __name__ == "__main__":
    raise SystemExit(main())
