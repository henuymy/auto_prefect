"""Selenium login flow for capturing NGBOSS/USM cookies."""
import json
import shutil
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.edge.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

from services.cookie_recorder import CookieRecorder, resolve_cookie_dump_path
from services.otp_service import delete_message, prepare_wait_context, wait_for_otp
from services.browser_session import browser_config, close_browser_session, record_browser_session
from services.session_manager import format_probe_validation_error, validate_existing_session
from utils.config_loader import load_json_with_local_override


PROJECT_DIR = Path(__file__).resolve().parents[1]


def xpath_literal(value: str) -> str:
    if "'" not in value:
        return f"'{value}'"
    if '"' not in value:
        return f'"{value}"'
    parts = value.split("'")
    return "concat(" + ', "\'", '.join(f"'{part}'" for part in parts) + ")"


def popup_input(prompt: str, title: str = "输入") -> str:
    try:
        import tkinter as tk
        from tkinter import simpledialog
        root = tk.Tk()
        root.withdraw()
        root.attributes('-topmost', True)
        result = simpledialog.askstring(title, prompt, parent=root)
        root.destroy()
        if result is None:
            raise EOFError("用户取消输入")
        return result
    except Exception:
        return input(prompt)


class AutoLogin:
    def __init__(self, config_path=None):
        self.config_path = Path(config_path).resolve() if config_path else PROJECT_DIR / "legacy_modules/modules/autologin/config.json"
        self.config, _ = load_json_with_local_override(self.config_path)
        self.driver = None
        self.pending_otp_message_id = None
        self.otp_wait_context = None
        self.sms_auth_button_id = "smsAuthen_div"
        self.usm_window_handle = None
        self.usm_entry_url = None
        self.opened_app_handles = {}
        self.cookie_recorder = self._init_cookie_recorder()

    def _init_cookie_recorder(self):
        cookie_dump_config = self.config.get("cookie_dump", {})
        if not cookie_dump_config.get("enabled", True):
            return None
        output_path = resolve_cookie_dump_path(PROJECT_DIR, self.config)
        return CookieRecorder(output_path, config_path=self.config_path)

    def capture_cookies(self, stage_name):
        if not self.cookie_recorder or not self.driver:
            return None
        try:
            return self.cookie_recorder.capture(self.driver, stage_name)
        except Exception as e:
            print(f"[WARN] 写入 {stage_name} Cookie JSON 失败: {e}")
            return None

    def get_login_url(self):
        return self.config.get("login_url") or self.config["urls"]["ngboss_login"]

    def get_logout_url(self):
        configured = self.config.get("logout_url")
        if configured:
            return configured
        parsed = urlparse(self.get_login_url())
        return f"{parsed.scheme}://{parsed.netloc}/uac/web3/jsp/login/login3!logout.action"

    def get_usm_host(self):
        if "usm_host" in self.config:
            return self.config["usm_host"]
        return urlparse(self.config.get("urls", {}).get("usm_console", "")).hostname or ""

    def get_usm_cookie_apps(self):
        return self.config.get("usm_cookie_apps") or [{"stage": "data_market", "name": "数据超市"}]

    def ensure_ngboss_main_loaded(self):
        try:
            WebDriverWait(self.driver, 30).until(
                EC.presence_of_element_located((By.ID, "buttonList"))
            )
        except TimeoutException as exc:
            raise RuntimeError("未进入 ngboss_main，停止后续 USM/应用 Cookie 捕获") from exc

    def wait_and_click(self, locator, timeout=30):
        element = WebDriverWait(self.driver, timeout).until(EC.element_to_be_clickable(locator))
        element.click()
        return element

    def dump_login_page_debug(self, reason):
        debug_dir = PROJECT_DIR / "runtime" / "login_debug"
        debug_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        payload = {
            "reason": reason,
            "url": "",
            "title": "",
            "page_source_path": str(debug_dir / f"login_{stamp}.html"),
            "screenshot_path": str(debug_dir / f"login_{stamp}.png"),
        }
        try:
            payload["url"] = self.driver.current_url
            payload["title"] = self.driver.title
        except Exception:
            pass
        try:
            Path(payload["page_source_path"]).write_text(self.driver.page_source or "", encoding="utf-8")
        except Exception as exc:
            payload["page_source_error"] = str(exc)
        try:
            self.driver.save_screenshot(payload["screenshot_path"])
        except Exception as exc:
            payload["screenshot_error"] = str(exc)
        print(f"[WARN] 登录页调试信息: {json.dumps(payload, ensure_ascii=False)}")
        return payload

    def wait_for_login_entry_or_home(self, timeout=30, attempts=2):
        for attempt in range(1, attempts + 1):
            try:
                return WebDriverWait(self.driver, timeout).until(self.detect_login_entry_or_home)
            except TimeoutException:
                debug = self.dump_login_page_debug(f"login_entry_timeout_attempt_{attempt}")
                if attempt < attempts:
                    if self.is_ngboss_no_permission_page():
                        self.reset_ngboss_login_state("检测到系统异常页")
                    else:
                        print("[WARN] 未找到登录框或主页，刷新登录页后重试")
                        self.driver.get(self.get_login_url())
                    continue
                raise RuntimeError(
                    "打开登录页后未找到 loginName 输入框，也未检测到 NGBOSS 主页；"
                    f"当前URL={debug.get('url')}, 标题={debug.get('title')}, "
                    f"截图={debug.get('screenshot_path')}, HTML={debug.get('page_source_path')}"
                )
            except Exception as exc:
                debug = self.dump_login_page_debug(f"login_entry_error_attempt_{attempt}")
                raise RuntimeError(
                    "检测登录页状态时 WebDriver 无响应或异常；"
                    f"错误={type(exc).__name__}: {exc}, "
                    f"当前URL={debug.get('url')}, 标题={debug.get('title')}, "
                    f"截图={debug.get('screenshot_path')}, HTML={debug.get('page_source_path')}"
                ) from exc

    def is_ngboss_no_permission_page(self):
        try:
            current_url = self.driver.current_url or ""
            title = self.driver.title or ""
            body_text = self.driver.find_element(By.TAG_NAME, "body").text or ""
        except Exception:
            return False
        return (
            "nopermission.jsp" in current_url
            or title == "系统异常"
            or "网络环境发生变化" in body_text
            or "安全检查失败" in body_text
        )

    def reset_ngboss_login_state(self, reason):
        logout_url = self.get_logout_url()
        login_url = self.get_login_url()
        print(f"[WARN] {reason}，访问退出地址清理登录状态: {logout_url}")
        self.driver.get(logout_url)
        time.sleep(1)
        print(f"[INFO] 重新打开登录页面: {login_url}")
        self.driver.get(login_url)

    def detect_login_entry_or_home(self, driver):
        if self.is_ngboss_no_permission_page():
            return False
        if self.confirm_terminal_tool_dialog_if_present() and self.terminal_tool_dialog_is_visible():
            return False
        if driver.find_elements(By.ID, "buttonList"):
            return "home"
        items = driver.find_elements(By.ID, "loginName")
        for item in items:
            try:
                if item.is_displayed() and item.is_enabled():
                    return "login"
            except Exception:
                continue
        return False

    def terminal_tool_dialog_is_visible(self):
        try:
            dialogs = self.driver.find_elements(By.ID, "jMsgboxBox")
        except Exception:
            return False
        for dialog in dialogs:
            try:
                if not dialog.is_displayed():
                    continue
                text = (dialog.text or "").strip()
            except Exception:
                continue
            if "多终端工具" in text or "没有安装或运行" in text:
                return True
        return False

    def confirm_terminal_tool_dialog_if_present(self):
        try:
            dialogs = self.driver.find_elements(By.ID, "jMsgboxBox")
        except Exception as exc:
            raise RuntimeError(f"检测多终端工具弹窗时 WebDriver 无响应: {type(exc).__name__}: {exc}") from exc
        for dialog in dialogs:
            try:
                if not dialog.is_displayed():
                    continue
                text = (dialog.text or "").strip()
            except Exception:
                continue
            if "多终端工具" not in text and "没有安装或运行" not in text:
                continue
            print("[INFO] 检测到多终端工具提示弹窗，自动点击确认后继续登录")
            buttons = []
            locators = [
                (By.CSS_SELECTOR, "#jMsgboxBox .msgbox_button"),
                (By.XPATH, "//*[@id='jMsgboxBox']//input[@type='button' and (contains(@value,'确') or contains(@value,'关'))]"),
                (By.XPATH, "//*[@id='jMsgboxBox']//*[self::button or self::input][contains(.,'确认') or contains(@value,'确认')]"),
            ]
            for locator in locators:
                try:
                    buttons.extend(self.driver.find_elements(*locator))
                except Exception:
                    continue
            for button in buttons:
                try:
                    self.driver.execute_script("arguments[0].scrollIntoView({block:'center'});", button)
                    self.driver.execute_script(
                        """
                        const el = arguments[0];
                        for (const type of ['mouseover', 'mousedown', 'mouseup', 'click']) {
                          el.dispatchEvent(new MouseEvent(type, {bubbles: true, cancelable: true, view: window}));
                        }
                        if (typeof el.click === 'function') {
                          el.click();
                        }
                        """,
                        button,
                    )
                    try:
                        WebDriverWait(self.driver, 3).until_not(
                            lambda _: self.terminal_tool_dialog_is_visible()
                        )
                    except TimeoutException:
                        continue
                    print("[INFO] 多终端工具提示弹窗已确认关闭")
                    return True
                except Exception:
                    continue
            print("[WARN] 多终端工具提示弹窗存在，但未找到可点击的确认按钮")
            return True
        return False

    def init_driver(self):
        options = Options()
        browser = browser_config(self.config)
        browser_options = self.config.get("browser") or {}
        options.page_load_strategy = browser_options.get("page_load_strategy", "eager") or "eager"
        if browser["user_data_dir"]:
            if browser_options.get("close_existing_before_start", True):
                close_result = close_browser_session(
                    browser["session_state_path"],
                    user_data_dir=browser["user_data_dir"],
                    wait_seconds=float(browser_options.get("close_wait_seconds", 10) or 10),
                )
                if close_result.get("stopped_pids"):
                    print(f"[INFO] 启动前已关闭旧自动登录浏览器: {close_result}")
                elif close_result.get("remaining_pids"):
                    print(f"[WARN] 启动前旧自动登录浏览器仍有残留: {close_result}")
            if browser_options.get("clear_profile_before_start", False):
                shutil.rmtree(browser["user_data_dir"], ignore_errors=True)
                print(f"[INFO] 启动前已删除自动登录浏览器 profile: {browser['user_data_dir']}")
            browser["user_data_dir"].mkdir(parents=True, exist_ok=True)
            options.add_argument(f"--user-data-dir={browser['user_data_dir']}")
        if browser["headless"]:
            options.add_argument("--headless=new")
        elif browser["keep_open_after_login"]:
            options.add_experimental_option("detach", True)
        options.add_argument('--ignore-certificate-errors')
        options.add_argument('--allow-insecure-localhost')
        options.add_argument('--disable-web-security')
        options.add_argument('--disable-logging')
        options.add_argument('--log-level=3')
        options.add_argument('--silent')
        options.add_argument('--no-sandbox')
        options.add_argument('--disable-dev-shm-usage')
        options.add_argument('--disable-gpu')
        window_width = int(browser_options.get("window_width", 1920) or 1920)
        window_height = int(browser_options.get("window_height", 1080) or 1080)
        if window_width <= 0 or window_height <= 0:
            raise ValueError("浏览器窗口宽高必须大于 0")
        options.add_argument(f"--window-size={window_width},{window_height}")
        self.driver = webdriver.Edge(options=options)
        webdriver_timeout = int(browser_options.get("webdriver_timeout_seconds", 30) or 30)
        if hasattr(self.driver.command_executor, "set_timeout"):
            self.driver.command_executor.set_timeout(webdriver_timeout)
        self.driver.set_page_load_timeout(int(browser_options.get("page_load_timeout_seconds", 60) or 60))
        self.driver.set_script_timeout(int(browser_options.get("script_timeout_seconds", 15) or 15))
        implicit_wait = float(browser_options.get("implicit_wait", 0) or 0)
        if implicit_wait < 0:
            raise ValueError("浏览器 implicit_wait 不能小于 0")
        self.driver.implicitly_wait(implicit_wait)
        browser_mode = "无头模式" if browser["headless"] else "有头模式"
        print(f"[INFO] Edge浏览器已启动（{browser_mode}，已忽略SSL证书错误）")

    def keep_open_after_login(self):
        return browser_config(self.config)["keep_open_after_login"]

    def record_browser_session(self):
        if not self.driver:
            return None
        browser = browser_config(self.config)
        driver_pid = None
        try:
            driver_pid = self.driver.service.process.pid
        except Exception:
            pass
        path = record_browser_session(
            browser["session_state_path"],
            browser["user_data_dir"],
            driver_pid=driver_pid,
            config_path=self.config_path,
        )
        print(f"[INFO] 已保留自动登录浏览器会话: {path}")
        return path

    def fill_login_credentials(self):
        wait = WebDriverWait(self.driver, 30)
        username = self.config['credentials']['username']
        print(f"[INFO] 输入用户名: {username}")
        login_name = wait.until(EC.presence_of_element_located((By.ID, "loginName")))
        self.set_input_value(login_name, username)
        self.confirm_terminal_tool_dialog_if_present()
        try:
            password_tab = self.driver.find_element(By.CSS_SELECTOR, "#pwdDiv > .main_tab_l")
            self.driver.execute_script("arguments[0].click();", password_tab)
        except Exception:
            pass
        self.driver.execute_script(
            """
            const password = document.getElementById('loginPassword');
            const placeholder = document.getElementById('passwd_input_placeholder');
            const passwordDiv = document.getElementById('pwdDiv');
            if (passwordDiv) {
              passwordDiv.style.display = 'block';
            }
            if (placeholder) {
              placeholder.style.display = 'none';
            }
            if (password) {
              password.style.display = '';
              password.removeAttribute('disabled');
              password.removeAttribute('readonly');
            }
            """
        )
        print("[INFO] 输入密码")
        try:
            login_password = wait.until(
                lambda driver: self.find_password_input(driver)
            )
            self.driver.execute_script("arguments[0].focus();", login_password)
            self.set_input_value(login_password, self.config['credentials']['password'])
        except Exception as exc:
            debug = self.dump_login_page_debug("password_input_timeout")
            raise RuntimeError(
                "输入密码失败，未找到可用的密码输入框；"
                f"当前URL={debug.get('url')}, 标题={debug.get('title')}, "
                f"截图={debug.get('screenshot_path')}, HTML={debug.get('page_source_path')}"
            ) from exc

    def find_password_input(self, driver):
        candidates = []
        for locator in [
            (By.ID, "loginPassword"),
            (By.CSS_SELECTOR, "input[type='password']"),
        ]:
            try:
                candidates.extend(driver.find_elements(*locator))
            except Exception:
                continue
        for item in candidates:
            try:
                if item.is_displayed() and item.is_enabled():
                    return item
            except Exception:
                continue
        return False

    def set_input_value(self, element, value):
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

    def confirm_existing_session_if_needed(self):
        def visible_dialog(driver):
            dialogs = driver.find_elements(By.ID, "jMsgboxBox")
            return next((item for item in dialogs if item.is_displayed()), False)

        try:
            confirm_dialog = visible_dialog(self.driver) or WebDriverWait(
                self.driver,
                float(self.config.get("login_waits", {}).get("existing_session_dialog_seconds", 1.5)),
                poll_frequency=0.2,
            ).until(
                visible_dialog
            )
            print("[INFO] 检测到登录确认弹窗，自动点击「确认」...")
            confirm_btn = confirm_dialog.find_element(
                By.XPATH, ".//input[@class='msgbox_button' and contains(@value,'确')]"
            )
            confirm_btn.click()
        except TimeoutException:
            pass
        except Exception as e:
            print(f"[WARN] 处理确认弹窗时出错: {e}")

    def trigger_first_login(self):
        print("[INFO] 第一次点击登录按钮")
        self.wait_and_click((By.ID, "login_btn"), timeout=30)
        self.confirm_existing_session_if_needed()

    def submit_sms_code(self, sms_code):
        print("[INFO] 已获取验证码，准备提交...")
        try:
            sms_input = WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located((By.ID, "smKey"))
            )
            sms_input.click()
            sms_input.clear()
            sms_input.send_keys(sms_code)
        except Exception:
            pass
        self.wait_and_click((By.ID, "login_btn"), timeout=30)
        print("[INFO] 已提交验证码")

    def wait_for_ngboss_home(self):
        print("[INFO] 等待登录完成（自动检测主页 / 法律声明）...")
        combined_xpath = (
            "//*["
            "  @id='buttonList'"
            "  or @id='saveDrsCfgBtn' or @id='confirmBtn' or @id='agreeBtn'"
            "  or (self::input and (contains(@value,'确') or contains(@value,'同意')))"
            "  or (self::button and (contains(text(),'确认') or contains(text(),'同意')))"
            "  or (self::input and @type='submit')"
            "]"
        )
        try:
            first_elem = WebDriverWait(self.driver, 30).until(
                EC.presence_of_element_located((By.XPATH, combined_xpath))
            )
            elem_id = first_elem.get_attribute('id') or ''
            print(f"[INFO] 命中元素: id={elem_id!r}, tag={first_elem.tag_name}")
            if elem_id != 'buttonList':
                print("[INFO] 检测到法律声明页面，自动点击确认按钮...")
                self.driver.execute_script("arguments[0].click();", first_elem)
                WebDriverWait(self.driver, 20).until(
                    EC.presence_of_element_located((By.ID, "buttonList"))
                )
            self.ack_otp_message()
        except TimeoutException as exc:
            raise RuntimeError("等待 NGBOSS 主页面超时，登录流程未完成") from exc
        except Exception as e:
            raise RuntimeError(f"处理登录跳转时出错: {e}") from e

    def prepare_otp_wait_context(self):
        otp_mode = self.config.get("otp_mode", "gotify").lower()
        if otp_mode != "gotify":
            return None
        otp_config = self.config.get("otp_config")
        if not otp_config:
            raise ValueError("otp_mode=gotify 时必须在配置中提供 otp_config")
        return prepare_wait_context(otp_config)

    def request_sms_code(self, retry_index):
        wait_seconds = 10 if retry_index == 0 else 20
        try:
            WebDriverWait(self.driver, wait_seconds).until(
                EC.element_to_be_clickable((By.ID, self.sms_auth_button_id))
            ).click()
            time.sleep(1)
            return True
        except Exception as e:
            print(f"[WARN] 触发短信验证码失败: {e}")
            return False

    def get_sms_code(self):
        otp_mode = self.config.get("otp_mode", "gotify").lower()
        if otp_mode == "gotify":
            otp_config = self.config.get("otp_config")
            if not otp_config:
                raise ValueError("otp_mode=gotify 时必须在配置中提供 otp_config")
            max_request_count = int(otp_config.get("max_request_count", 3))
            retry_interval_seconds = int(otp_config.get("retry_interval_seconds", 5))
            last_error = None
            for retry_index in range(max_request_count):
                if retry_index == 0:
                    wait_context = self.otp_wait_context or prepare_wait_context(otp_config)
                else:
                    wait_context = prepare_wait_context(otp_config)
                    self.otp_wait_context = wait_context
                    self.request_sms_code(retry_index)
                try:
                    result = wait_for_otp(otp_config, wait_context.trigger_time, latest_message_id=wait_context.latest_message_id)
                    self.pending_otp_message_id = result.message_id
                    return result.code
                except TimeoutError as exc:
                    last_error = exc
                    if retry_index < max_request_count - 1:
                        print(f"[WARN] 本轮 Gotify 验证码等待超时，{retry_interval_seconds} 秒后重新触发短信...")
                        time.sleep(retry_interval_seconds)
            raise TimeoutError(
                f"多次重新发送验证码后仍未获取到 Gotify 动态密钥：已尝试 {max_request_count} 轮。"
                "请确认手机已开机、SMSForwarder 正在运行、Gotify 服务可访问；"
                "如果手机已经收到短信但未转发，请打开 SMSForwarder 后重新运行本次流程。"
                f"最后错误: {last_error}"
            )
        sms_code = None
        while not sms_code:
            sms_code = popup_input("请输入短信验证码:", "短信验证码")
        return sms_code

    def ack_otp_message(self):
        if self.config.get("otp_mode", "gotify").lower() != "gotify":
            return
        otp_config = self.config.get("otp_config", {})
        if not otp_config.get("delete_after_success", True) or not self.pending_otp_message_id:
            return
        try:
            delete_message(otp_config, self.pending_otp_message_id)
            print(f"[INFO] 已删除 Gotify 验证码消息: id={self.pending_otp_message_id}")
            self.pending_otp_message_id = None
        except Exception as e:
            print(f"[WARN] 删除 Gotify 验证码消息失败: {e}")

    def login_ngboss(self):
        login_url = self.get_login_url()
        print(f"[INFO] 正在打开登录页面: {login_url}")
        self.driver.get(login_url)
        page_state = self.wait_for_login_entry_or_home()
        if page_state == "home":
            print("[INFO] 打开登录页后已处于 NGBOSS 主页，跳过用户名密码登录")
            return
        self.fill_login_credentials()
        self.otp_wait_context = self.prepare_otp_wait_context()
        self.trigger_first_login()
        sms_code = self.get_sms_code()
        self.submit_sms_code(sms_code)
        self.wait_for_ngboss_home()

    def click_app_login(self):
        try:
            self.find_app_login_button().click()
            self.switch_to_app_login_frame()
            self.open_usm_from_app_access()
        except Exception as e:
            raise RuntimeError(f"点击应用登录时出错: {e}") from e

    def find_app_login_button(self):
        wait = WebDriverWait(self.driver, 15)
        for locator in [
            (By.ID, "p1050000"),
            (By.XPATH, "//li[@id='p1050000']"),
            (By.XPATH, "//li[contains(@href, '/uac/web3/jsp/resource/app/appRes3.action')]"),
            (By.XPATH, "//*[@href='/uac/web3/jsp/resource/app/appRes3.action' or contains(@href, 'appRes3.action')]"),
            (By.XPATH, "//li[contains(text(),'应用登录')]"),
            (By.CSS_SELECTOR, "li#p1050000"),
        ]:
            try:
                return wait.until(EC.element_to_be_clickable(locator))
            except TimeoutException:
                continue
        raise RuntimeError("未找到应用登录按钮，无法继续进入 USM")

    def switch_to_app_login_frame(self):
        self.driver.switch_to.default_content()
        wait = WebDriverWait(self.driver, 30)
        frame = None
        for locator in [
            (By.CSS_SELECTOR, "iframe#title5tab"),
            (By.XPATH, "//iframe[contains(@src, '/uac/web3/jsp/resource/app/appRes3.action')]"),
            (By.XPATH, "//iframe[contains(@src, 'appRes3.action')]"),
        ]:
            try:
                frame = wait.until(EC.presence_of_element_located(locator))
                break
            except TimeoutException:
                continue
        if frame is None:
            raise RuntimeError("未找到应用登录 iframe：title5tab / appRes3.action")
        self.driver.switch_to.frame(frame)
        wait.until(EC.presence_of_element_located((By.ID, "viewdatalist")))
        wait.until(EC.presence_of_element_located((By.XPATH, "//*[starts-with(@id,'appLogin_div')]")))

    def open_usm_from_app_access(self):
        entry_config = self.config.get("app_login_entry") or {}
        entry_name = entry_config.get("name")
        if entry_name:
            entry_name_literal = xpath_literal(entry_name)
            title_xpath = (
                "//div[starts-with(@id,'appLogin_div')"
                " and ("
                f" .//*[contains(@id,'appNameDiv') and contains(normalize-space(.), {entry_name_literal})]"
                f" or contains(normalize-space(.), {entry_name_literal})"
                " )]"
            )
            title_card = WebDriverWait(self.driver, 30).until(
                EC.presence_of_element_located((By.XPATH, title_xpath))
            )
            title_id = title_card.get_attribute("id") or ""
            suffix = title_id.replace("appLogin_div", "")
            if not suffix.isdigit():
                raise RuntimeError(f"无法从应用卡片 id 推断入口编号: {title_id}")
            print(f"[INFO] 应用登录入口匹配到: {entry_name} -> {title_id}")
            self.driver.execute_script("arguments[0].scrollIntoView({block:'center'});", title_card)
            self.driver.execute_script("arguments[0].click();", title_card)
            choose_div_id = f"appLoginChoose_div{suffix}"
            choose_div = WebDriverWait(self.driver, 30).until(
                lambda d: self._visible_element_by_id(choose_div_id)
            )
            self.driver.switch_to.frame(
                WebDriverWait(choose_div, 10).until(lambda e: e.find_element(By.TAG_NAME, "iframe"))
            )
            login_button = self.find_entry_login_button()
        else:
            raise ValueError("login_config.json 缺少 app_login_entry.name，不能按位置点击应用登录入口")
        self.driver.execute_script("arguments[0].click();", login_button)
        try:
            WebDriverWait(self.driver, 5).until(lambda d: len(d.window_handles) > 1)
            self.driver.switch_to.window(self.driver.window_handles[-1])
        except TimeoutException:
            pass

    def _visible_element_by_id(self, element_id):
        element = self.driver.find_element(By.ID, element_id)
        if element.is_displayed() and element.value_of_css_property("display") != "none":
            return element
        return False

    def find_entry_login_button(self):
        for locator in [
            (By.ID, "appAccess"),
            (By.XPATH, "//*[@id='appAccess' or contains(normalize-space(.), '登录') or contains(@value, '登录')]"),
        ]:
            try:
                return WebDriverWait(self.driver, 15).until(EC.element_to_be_clickable(locator))
            except TimeoutException:
                continue
        raise RuntimeError("已进入应用入口 iframe，但未找到登录按钮")

    def access_usm_console(self):
        usm_host = self.get_usm_host()
        self.driver.switch_to.default_content()
        try:
            WebDriverWait(self.driver, 30, poll_frequency=0.1).until(lambda d: usm_host in d.current_url)
        except TimeoutException as exc:
            raise RuntimeError(f"应用登录后未进入 USM，当前URL={self.driver.current_url}") from exc
        self.usm_window_handle = self.driver.current_window_handle
        self.usm_entry_url = self.driver.current_url
        try:
            wait = WebDriverWait(self.driver, 60, poll_frequency=0.1)
            wait.until(EC.presence_of_element_located((By.ID, "iFrame1")))
            self.driver.switch_to.frame("iFrame1")
            try:
                wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "div.vue-grid-item")))
            except Exception:
                pass
        except Exception as e:
            raise RuntimeError(f"访问 USM 控制台失败: {e}") from e

    def enter_usm_app(self, app_config):
        handle = self.launch_usm_app(app_config)
        self.driver.switch_to.window(handle)
        self.wait_for_app_navigation(app_config)
        return handle

    def launch_usm_app(self, app_config):
        app_name = app_config["name"]
        self._switch_to_usm_app_list()
        app_div = WebDriverWait(self.driver, 30, poll_frequency=0.1).until(
            EC.visibility_of_element_located((By.XPATH,
                f"//div[contains(@class,'customizedList') and contains(@class,'module')][contains(., '{app_name}')]"
            ))
        )
        old_handles = set(self.driver.window_handles)
        self.driver.execute_script("arguments[0].click();", app_div)
        try:
            WebDriverWait(self.driver, 15, poll_frequency=0.1).until(lambda d: len(set(d.window_handles) - old_handles) > 0)
        except TimeoutException as exc:
            raise RuntimeError(f"点击 {app_name} 后未打开独立页面") from exc
        new_handle = list(set(self.driver.window_handles) - old_handles)[-1]
        self.driver.switch_to.window(new_handle)
        return self.driver.current_window_handle

    def enter_usm_app_from_source(self, app_config):
        handle = self.launch_usm_app_from_source(app_config)
        self.driver.switch_to.window(handle)
        self.wait_for_app_navigation(app_config)
        return handle

    def launch_usm_app_from_source(self, app_config):
        app_name = app_config["name"]
        source_stage = app_config.get("source_stage")
        source_handle = self.usm_window_handle if source_stage == "usm_console" else self.opened_app_handles.get(source_stage)
        if not source_handle or source_handle not in self.driver.window_handles:
            raise RuntimeError(f"{app_name} 依赖的 source_stage 不存在或已关闭: {source_stage}")

        if source_stage == "usm_console":
            self._switch_to_usm_app_list()
        else:
            self.driver.switch_to.window(source_handle)
            self.driver.switch_to.default_content()

        icon_src_contains = app_config.get("icon_src_contains")
        if not icon_src_contains:
            raise ValueError(f"{app_name} 使用 source_stage 时必须配置 icon_src_contains")

        icon_xpath = f"//img[contains(@src, '{icon_src_contains}')]"
        menu_item_xpath = (
            f"{icon_xpath}/ancestor::li[contains(@class,'el-menu-item')][1]"
        )
        old_handles = set(self.driver.window_handles)
        element = None
        for locator in [
            (By.XPATH, menu_item_xpath),
            (By.XPATH, icon_xpath),
        ]:
            try:
                element = WebDriverWait(self.driver, 30, poll_frequency=0.1).until(EC.element_to_be_clickable(locator))
                break
            except TimeoutException:
                continue
        if element is None:
            raise RuntimeError(f"未找到 {app_name} 图标入口: img[src*={icon_src_contains!r}]")

        print(f"[INFO] 从 {source_stage} 点击应用入口: {app_name} / {icon_src_contains}")
        self.driver.execute_script("arguments[0].scrollIntoView({block:'center'});", element)
        self.driver.execute_script("arguments[0].click();", element)

        try:
            WebDriverWait(self.driver, 10, poll_frequency=0.1).until(lambda d: len(set(d.window_handles) - old_handles) > 0)
            new_handle = list(set(self.driver.window_handles) - old_handles)[-1]
            self.driver.switch_to.window(new_handle)
        except TimeoutException:
            self.driver.switch_to.window(source_handle)

        return self.driver.current_window_handle

    def wait_for_app_navigation(self, app_config):
        expected = str(app_config.get("url_contains") or "").strip()
        timeout_seconds = float(app_config.get("navigation_timeout_seconds", 30) or 30)
        try:
            if expected:
                WebDriverWait(self.driver, timeout_seconds, poll_frequency=0.1).until(
                    lambda d: expected in (d.current_url or "")
                )
            else:
                WebDriverWait(self.driver, timeout_seconds, poll_frequency=0.1).until(
                    lambda d: d.execute_script("return document.readyState") == "complete"
                )
        except TimeoutException as exc:
            if expected:
                raise RuntimeError(
                    f"{app_config.get('name') or app_config.get('stage')} 未进入目标页面 "
                    f"{expected!r}，当前URL={self.driver.current_url}"
                ) from exc
            raise

    def wait_for_storage_ready(self, app_config):
        storage_ready = app_config.get("storage_ready")
        if not storage_ready:
            return
        storage_type = storage_ready.get("type", "session_storage")
        storage_path = storage_ready.get("path")
        timeout_seconds = int(storage_ready.get("timeout_seconds", 30))
        if not storage_path:
            return
        script = """
            const storageType = arguments[0];
            const storagePath = arguments[1];
            const storage = storageType === 'local_storage' ? window.localStorage : window.sessionStorage;
            const parts = storagePath.split('.');
            let value = storage.getItem(parts.shift());
            for (const part of parts) {
              if (!value) return "";
              try {
                value = JSON.parse(value);
              } catch (err) {
                return "";
              }
              value = value ? value[part] : "";
            }
            return value || "";
        """
        try:
            WebDriverWait(self.driver, timeout_seconds, poll_frequency=0.1).until(
                lambda d: d.execute_script(script, storage_type, storage_path)
            )
            print(f"[INFO] Storage 已就绪: {storage_type}.{storage_path}")
        except TimeoutException:
            print(f"[WARN] 等待 Storage 超时，继续捕获: {storage_type}.{storage_path}")

    def _switch_to_usm_app_list(self):
        if self.usm_window_handle and self.usm_window_handle in self.driver.window_handles:
            self.driver.switch_to.window(self.usm_window_handle)
        usm_host = self.get_usm_host()
        if usm_host not in self.driver.current_url:
            raise RuntimeError(f"当前不在 USM，当前URL={self.driver.current_url}")
        self.driver.switch_to.default_content()
        self.driver.switch_to.frame(
            WebDriverWait(self.driver, 30, poll_frequency=0.1).until(EC.presence_of_element_located((By.ID, "iFrame1")))
        )

    def capture_usm_apps_cookies(self):
        app_configs = self.get_usm_cookie_apps()
        for app_config in app_configs:
            stage = app_config.get("stage")
            app_name = app_config.get("name")
            if not stage or not app_name:
                raise ValueError("usm_cookie_apps 每一项都必须包含 stage 和 name")

        for app_config in app_configs:
            stage = app_config["stage"]
            app_name = app_config["name"]
            print(f"[INFO] 准备捕获 USM 应用 Cookie: {stage} / {app_name}")
            if app_config.get("source_stage"):
                self.opened_app_handles[stage] = self.enter_usm_app_from_source(app_config)
            else:
                self.opened_app_handles[stage] = self.enter_usm_app(app_config)
            self.wait_for_storage_ready(app_config)
            self.capture_cookies(stage)
            if self.usm_window_handle and self.usm_window_handle in self.driver.window_handles:
                self.driver.switch_to.window(self.usm_window_handle)

    def verify_captured_session(self):
        validation_config = self.config.get("session_validation") or {}
        if not validation_config.get("enabled", False):
            return None
        if not self.cookie_recorder:
            raise RuntimeError("已启用 session 探活，但 Cookie 导出未启用")

        source_path = validation_config.get("config_path", "config/modules/autologin.json")
        source_path = Path(source_path)
        if not source_path.is_absolute():
            source_path = PROJECT_DIR / source_path
        session_config, _ = load_json_with_local_override(source_path)
        required_stages = validation_config.get("required_stages") or session_config.get("required_stages") or []
        cookie_dump = json.loads(self.cookie_recorder.output_path.read_text(encoding="utf-8"))
        validation, probe_validation = validate_existing_session(
            cookie_dump,
            required_stages,
            session_config.get("stage_probes") or {},
            min_ttl_seconds=int(validation_config.get("min_ttl_seconds", 0) or 0),
        )
        if not validation.get("valid"):
            raise RuntimeError(f"登录后 Cookie 静态校验失败: {validation}")
        if not probe_validation or not probe_validation.get("valid"):
            raise RuntimeError(
                "登录后 session 探活失败: "
                f"{format_probe_validation_error(probe_validation)}"
            )
        checked = ", ".join(required_stages)
        print(f"[INFO] Session 探活通过: {checked}")
        return validation

    def login_and_capture_cookies(self):
        if not self.driver:
            self.init_driver()
        self.login_ngboss()
        self.ensure_ngboss_main_loaded()
        self.capture_cookies("ngboss_main")
        self.click_app_login()
        self.access_usm_console()
        self.capture_cookies("usm_console")
        self.capture_usm_apps_cookies()
        self.verify_captured_session()
        return {
            "cookie_dump": str(self.cookie_recorder.output_path) if self.cookie_recorder else None,
            "opened_app_handles": dict(self.opened_app_handles),
        }

    def close(self):
        if self.driver:
            self.driver.quit()
            self.driver = None

    def run(self):
        success = False
        try:
            print("=" * 60)
            print("自动登录系统 - 开始执行")
            print("=" * 60)
            self.login_and_capture_cookies()
            success = True
            print("\n" + "=" * 60)
            print("执行完成！")
            print("=" * 60)
        except Exception as e:
            print(f"\n[ERROR] 执行过程中出错: {e}")
            import traceback
            traceback.print_exc()
            raise
        finally:
            if success and self.keep_open_after_login():
                self.record_browser_session()
                self.driver = None
            else:
                self.close()


def run_login(config_path):
    login = AutoLogin(config_path)
    login.run()
