"""Selenium login flow for capturing NGBOSS/USM cookies."""
import json
import time
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

    def init_driver(self):
        options = Options()
        options.add_argument('--ignore-certificate-errors')
        options.add_argument('--allow-insecure-localhost')
        options.add_argument('--disable-web-security')
        options.add_argument('--disable-logging')
        options.add_argument('--log-level=3')
        options.add_argument('--silent')
        options.add_argument('--no-sandbox')
        options.add_argument('--disable-dev-shm-usage')
        options.add_argument('--disable-gpu')
        options.add_argument('--window-size=1920,1080')
        self.driver = webdriver.Edge(options=options)
        self.driver.implicitly_wait(10)
        print("[INFO] Edge浏览器已启动（有头模式，已忽略SSL证书错误）")

    def fill_login_credentials(self):
        wait = WebDriverWait(self.driver, 30)
        username = self.config['credentials']['username']
        print(f"[INFO] 输入用户名: {username}")
        login_name = wait.until(EC.element_to_be_clickable((By.ID, "loginName")))
        login_name.click()
        login_name.clear()
        login_name.send_keys(username)
        try:
            self.driver.find_element(By.CSS_SELECTOR, "#pwdDiv > .main_tab_l").click()
        except Exception:
            pass
        print("[INFO] 输入密码")
        login_password = wait.until(EC.element_to_be_clickable((By.ID, "loginPassword")))
        login_password.click()
        login_password.clear()
        login_password.send_keys(self.config['credentials']['password'])

    def confirm_existing_session_if_needed(self):
        try:
            confirm_dialog = WebDriverWait(self.driver, 5).until(
                EC.visibility_of_element_located((By.ID, "jMsgboxBox"))
            )
            print("[INFO] 检测到登录确认弹窗，自动点击「确认」...")
            confirm_btn = confirm_dialog.find_element(
                By.XPATH, ".//input[@class='msgbox_button' and contains(@value,'确')]"
            )
            confirm_btn.click()
            time.sleep(1)
        except TimeoutException:
            pass
        except Exception as e:
            print(f"[WARN] 处理确认弹窗时出错: {e}")

    def trigger_first_login(self):
        print("[INFO] 第一次点击登录按钮")
        self.wait_and_click((By.ID, "login_btn"), timeout=30)
        time.sleep(1)
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
            raise TimeoutError(f"多次重新发送验证码后仍未获取到 Gotify 动态密钥: {last_error}")
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
            WebDriverWait(self.driver, 30).until(lambda d: usm_host in d.current_url)
        except TimeoutException as exc:
            raise RuntimeError(f"应用登录后未进入 USM，当前URL={self.driver.current_url}") from exc
        self.usm_window_handle = self.driver.current_window_handle
        self.usm_entry_url = self.driver.current_url
        try:
            wait = WebDriverWait(self.driver, 60)
            wait.until(EC.presence_of_element_located((By.ID, "iFrame1")))
            self.driver.switch_to.frame("iFrame1")
            try:
                wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "div.vue-grid-item")))
            except Exception:
                pass
        except Exception as e:
            raise RuntimeError(f"访问 USM 控制台失败: {e}") from e

    def enter_usm_app(self, app_config):
        app_name = app_config["name"]
        self._switch_to_usm_app_list()
        app_div = WebDriverWait(self.driver, 30).until(
            EC.visibility_of_element_located((By.XPATH,
                f"//div[contains(@class,'customizedList') and contains(@class,'module')][contains(., '{app_name}')]"
            ))
        )
        old_handles = set(self.driver.window_handles)
        self.driver.execute_script("arguments[0].click();", app_div)
        try:
            WebDriverWait(self.driver, 15).until(lambda d: len(set(d.window_handles) - old_handles) > 0)
        except TimeoutException as exc:
            raise RuntimeError(f"点击 {app_name} 后未打开独立页面") from exc
        new_handle = list(set(self.driver.window_handles) - old_handles)[-1]
        self.driver.switch_to.window(new_handle)
        try:
            WebDriverWait(self.driver, 30).until(lambda d: d.execute_script("return document.readyState") == "complete")
        except TimeoutException:
            pass
        return self.driver.current_window_handle

    def enter_usm_app_from_source(self, app_config):
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
                element = WebDriverWait(self.driver, 30).until(EC.element_to_be_clickable(locator))
                break
            except TimeoutException:
                continue
        if element is None:
            raise RuntimeError(f"未找到 {app_name} 图标入口: img[src*={icon_src_contains!r}]")

        print(f"[INFO] 从 {source_stage} 点击应用入口: {app_name} / {icon_src_contains}")
        self.driver.execute_script("arguments[0].scrollIntoView({block:'center'});", element)
        self.driver.execute_script("arguments[0].click();", element)

        try:
            WebDriverWait(self.driver, 10).until(lambda d: len(set(d.window_handles) - old_handles) > 0)
            new_handle = list(set(self.driver.window_handles) - old_handles)[-1]
            self.driver.switch_to.window(new_handle)
        except TimeoutException:
            self.driver.switch_to.window(source_handle)

        try:
            WebDriverWait(self.driver, 30).until(lambda d: d.execute_script("return document.readyState") == "complete")
        except TimeoutException:
            pass
        return self.driver.current_window_handle

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
            WebDriverWait(self.driver, timeout_seconds).until(
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
            WebDriverWait(self.driver, 30).until(EC.presence_of_element_located((By.ID, "iFrame1")))
        )

    def capture_usm_apps_cookies(self):
        for app_config in self.get_usm_cookie_apps():
            stage = app_config.get("stage")
            app_name = app_config.get("name")
            if not stage or not app_name:
                raise ValueError("usm_cookie_apps 每一项都必须包含 stage 和 name")
            print(f"[INFO] 准备捕获 USM 应用 Cookie: {stage} / {app_name}")
            if app_config.get("source_stage"):
                self.opened_app_handles[stage] = self.enter_usm_app_from_source(app_config)
            else:
                self.opened_app_handles[stage] = self.enter_usm_app(app_config)
            self.wait_for_storage_ready(app_config)
            self.capture_cookies(stage)
            if self.usm_window_handle and self.usm_window_handle in self.driver.window_handles:
                self.driver.switch_to.window(self.usm_window_handle)

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
        return {
            "cookie_dump": str(self.cookie_recorder.output_path) if self.cookie_recorder else None,
            "opened_app_handles": dict(self.opened_app_handles),
        }

    def close(self):
        if self.driver:
            self.driver.quit()
            self.driver = None

    def run(self):
        try:
            print("=" * 60)
            print("自动登录系统 - 开始执行")
            print("=" * 60)
            self.login_and_capture_cookies()
            print("\n" + "=" * 60)
            print("执行完成！")
            print("=" * 60)
        except Exception as e:
            print(f"\n[ERROR] 执行过程中出错: {e}")
            import traceback
            traceback.print_exc()
        finally:
            self.close()


def run_login(config_path):
    login = AutoLogin(config_path)
    login.run()
