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

try:
    from autologin.scripts.cookie_recorder import CookieRecorder, resolve_cookie_dump_path
    from autologin.scripts.otp_provider_gotify import delete_message, prepare_wait_context, wait_for_otp
except ModuleNotFoundError:
    from cookie_recorder import CookieRecorder, resolve_cookie_dump_path
    from otp_provider_gotify import delete_message, prepare_wait_context, wait_for_otp


SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent
BASE_DIR = SKILL_DIR
DEFAULT_CONFIG_PATH = SKILL_DIR / "config.json"


def popup_input(prompt: str, title: str = "输入") -> str:
    """弹出 GUI 输入框获取用户输入，失败时回退到终端输入"""
    try:
        import tkinter as tk
        from tkinter import simpledialog

        root = tk.Tk()
        root.withdraw()  # 隐藏主窗口
        root.attributes('-topmost', True)  # 置顶

        result = simpledialog.askstring(title, prompt, parent=root)
        root.destroy()

        if result is None:
            raise EOFError("用户取消输入")
        return result
    except Exception:
        # 回退到终端输入
        return input(prompt)


class AutoLogin:
    def __init__(self, config_path=None):
        """初始化自动登录类"""
        self.config_path = Path(config_path) if config_path else DEFAULT_CONFIG_PATH
        if not self.config_path.is_absolute():
            self.config_path = BASE_DIR / self.config_path
        self.config = self.load_config(self.config_path)
        self.driver = None
        self.pending_otp_message_id = None
        self.otp_wait_context = None
        self.sms_auth_button_id = "smsAuthen_div"
        self.usm_window_handle = None
        self.usm_entry_url = None
        self.opened_app_handles = {}
        self.cookie_recorder = self.init_cookie_recorder()
        
    def load_config(self, config_path):
        """加载配置文件"""
        with open(config_path, 'r', encoding='utf-8') as f:
            return json.load(f)

    def init_cookie_recorder(self):
        cookie_dump_config = self.config.get("cookie_dump", {})
        if not cookie_dump_config.get("enabled", True):
            return None
        output_path = resolve_cookie_dump_path(BASE_DIR, self.config)
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
        if "login_url" in self.config:
            return self.config["login_url"]
        return self.config["urls"]["ngboss_login"]

    def get_usm_host(self):
        if "usm_host" in self.config:
            return self.config["usm_host"]
        usm_url = self.config.get("urls", {}).get("usm_console", "")
        return urlparse(usm_url).hostname or ""

    def get_jsessionid_stage(self):
        return self.config.get("jsessionid_stage")

    def get_usm_cookie_apps(self):
        apps = self.config.get("usm_cookie_apps")
        if apps:
            return apps
        return [
            {
                "stage": "data_market",
                "name": "数据超市",
            }
        ]

    def ensure_ngboss_main_loaded(self):
        """确认已经进入 NGBOSS 主页面，不直接访问 main URL 兜底。"""
        try:
            WebDriverWait(self.driver, 30).until(
                EC.presence_of_element_located((By.ID, "buttonList"))
            )
        except TimeoutException as exc:
            raise RuntimeError("未进入 ngboss_main，停止后续 USM/应用 Cookie 捕获") from exc

    def wait_and_click(self, locator, timeout=30):
        element = WebDriverWait(self.driver, timeout).until(
            EC.element_to_be_clickable(locator)
        )
        element.click()
        return element
    
    def init_driver(self):
        """初始化浏览器驱动（有头模式）"""
        options = Options()
        # 有头模式，显示浏览器界面
        # 不设置 --headless，保持有头模式以便人工输入验证码
        
        # 忽略SSL证书错误（内网自签名证书）
        options.add_argument('--ignore-certificate-errors')
        options.add_argument('--allow-insecure-localhost')
        options.add_argument('--disable-web-security')
        
        # 禁用日志和错误提示
        options.add_argument('--disable-logging')
        options.add_argument('--log-level=3')
        options.add_argument('--silent')
        
        # 其他优化选项
        options.add_argument('--no-sandbox')
        options.add_argument('--disable-dev-shm-usage')
        options.add_argument('--disable-gpu')
        options.add_argument('--window-size=1920,1080')
        
        self.driver = webdriver.Edge(options=options)
        self.driver.implicitly_wait(10)
        print("[INFO] Edge浏览器已启动（有头模式，已忽略SSL证书错误）")
        
    def login_ngboss(self):
        """步骤1: 登录ngboss系统"""
        login_url = self.get_login_url()
        print(f"[INFO] 正在打开登录页面: {login_url}")
        self.driver.get(login_url)

        self.fill_login_credentials()
        self.otp_wait_context = self.prepare_otp_wait_context()
        self.trigger_first_login()
        sms_code = self.get_sms_code()
        self.submit_sms_code(sms_code)
        self.wait_for_ngboss_home()

    def fill_login_credentials(self):
        """填写 NGBOSS 用户名和密码。"""
        wait = WebDriverWait(self.driver, 30)
        username = self.config['credentials']['username']
        print(f"[INFO] 输入用户名: {username}")
        login_name = wait.until(EC.element_to_be_clickable((By.ID, "loginName")))
        login_name.click()
        login_name.clear()
        login_name.send_keys(username)

        try:
            pwd_div = self.driver.find_element(By.CSS_SELECTOR, "#pwdDiv > .main_tab_l")
            pwd_div.click()
        except:
            pass

        password = self.config['credentials']['password']
        print("[INFO] 输入密码")
        login_password = wait.until(EC.element_to_be_clickable((By.ID, "loginPassword")))
        login_password.click()
        login_password.clear()
        login_password.send_keys(password)

    def trigger_first_login(self):
        """第一次点击登录，触发短信验证码流程。"""
        print("[INFO] 第一次点击登录按钮")
        self.wait_and_click((By.ID, "login_btn"), timeout=30)
        time.sleep(1)
        self.confirm_existing_session_if_needed()

    def confirm_existing_session_if_needed(self):
        """处理相同主账号已在其他终端登录的确认弹窗。"""
        try:
            confirm_dialog = WebDriverWait(self.driver, 5).until(
                EC.visibility_of_element_located((By.ID, "jMsgboxBox"))
            )
            print("[INFO] 检测到登录确认弹窗，自动点击「确认」...")
            confirm_btn = confirm_dialog.find_element(
                By.XPATH, ".//input[@class='msgbox_button' and contains(@value,'确')]"
            )
            confirm_btn.click()
            print("[INFO] 已点击确认，继续登录流程")
            time.sleep(1)
        except TimeoutException:
            print("[INFO] 未出现登录确认弹窗，继续")
        except Exception as e:
            print(f"[WARN] 处理确认弹窗时出错: {e}")

    def submit_sms_code(self, sms_code):
        """填写短信验证码并再次点击登录。"""
        print(f"[INFO] 已获取验证码，准备提交...")
        try:
            sms_input = WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located((By.ID, "smKey"))
            )
            sms_input.click()
            sms_input.clear()
            sms_input.send_keys(sms_code)
        except:
            pass

        self.wait_and_click((By.ID, "login_btn"), timeout=30)
        print("[INFO] 已提交验证码")

    def wait_for_ngboss_home(self):
        """等待 NGBOSS 主页面；如出现法律声明则自动确认。"""
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
                # 命中的是法律声明确认按钮，立即点击，无需等待 readyState
                print("[INFO] 检测到法律声明页面，自动点击确认按钮...")
                self.driver.execute_script("arguments[0].click();", first_elem)
                print("[INFO] 已确认，等待主页 buttonList 出现...")
                WebDriverWait(self.driver, 20).until(
                    EC.presence_of_element_located((By.ID, "buttonList"))
                )
                print("[INFO] 主页已加载")
            else:
                print("[INFO] 直接进入主页，无法律声明页面")
            self.ack_otp_message()
        except TimeoutException as exc:
            raise RuntimeError("等待 NGBOSS 主页面超时，登录流程未完成") from exc
        except Exception as e:
            raise RuntimeError(f"处理登录跳转时出错: {e}") from e

    def prepare_otp_wait_context(self):
        """在触发短信前建立 Gotify 基线，避免误用历史验证码。"""
        otp_mode = self.config.get("otp_mode", "gotify").lower()
        if otp_mode != "gotify":
            return None

        otp_config = self.config.get("otp_config")
        if not otp_config:
            raise ValueError("otp_mode=gotify 时必须在配置中提供 otp_config")

        return prepare_wait_context(otp_config)

    def request_sms_code(self, retry_index):
        """点击短信验证入口，触发业务系统发送新动态密钥。"""
        wait_seconds = 10 if retry_index == 0 else 20
        try:
            print(f"[INFO] 触发短信验证码发送，第 {retry_index + 1} 次...")
            WebDriverWait(self.driver, wait_seconds).until(
                EC.element_to_be_clickable((By.ID, self.sms_auth_button_id))
            ).click()
            time.sleep(1)
            return True
        except Exception as e:
            print(f"[WARN] 触发短信验证码失败: {e}")
            return False

    def get_sms_code(self):
        """根据配置获取短信验证码。"""
        otp_mode = self.config.get("otp_mode", "gotify").lower()
        if otp_mode == "gotify":
            print("[INFO] 使用 Gotify 获取短信验证码...")
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
                    result = wait_for_otp(
                        otp_config,
                        wait_context.trigger_time,
                        latest_message_id=wait_context.latest_message_id,
                    )
                    self.pending_otp_message_id = result.message_id
                    return result.code
                except TimeoutError as exc:
                    last_error = exc
                    if retry_index < max_request_count - 1:
                        print(
                            "[WARN] 本轮 Gotify 验证码等待超时，"
                            f"{retry_interval_seconds} 秒后重新触发短信..."
                        )
                        time.sleep(retry_interval_seconds)

            raise TimeoutError(f"多次重新发送验证码后仍未获取到 Gotify 动态密钥: {last_error}")

        sms_code = None
        while not sms_code:
            print("[INFO] 弹出系统输入框，请在弹窗中输入短信验证码...")
            sms_code = popup_input("请输入短信验证码:", "短信验证码")
            if not sms_code:
                print("[WARN] 未输入验证码，请重新输入")
        return sms_code

    def ack_otp_message(self):
        """登录成功后按配置删除已消费的 Gotify 验证码消息。"""
        if self.config.get("otp_mode", "gotify").lower() != "gotify":
            return
        otp_config = self.config.get("otp_config", {})
        if not otp_config.get("delete_after_success", True):
            return
        if not self.pending_otp_message_id:
            return
        try:
            delete_message(otp_config, self.pending_otp_message_id)
            print(f"[INFO] 已删除 Gotify 验证码消息: id={self.pending_otp_message_id}")
            self.pending_otp_message_id = None
        except Exception as e:
            print(f"[WARN] 删除 Gotify 验证码消息失败: {e}")
        
    def click_app_login(self):
        """步骤2: 从 NGBOSS 主页面点击应用登录，进入 USM 控制台。"""
        print(f"[DEBUG] 当前URL: {self.driver.current_url}")
        print(f"[DEBUG] 页面标题: {self.driver.title}")

        try:
            self.find_app_login_button().click()
            self.switch_to_app_login_frame()
            self.open_usm_from_app_access()
        except Exception as e:
            raise RuntimeError(f"点击应用登录时出错: {e}") from e

    def find_app_login_button(self):
        print("[INFO] 查找应用登录按钮...")
        wait = WebDriverWait(self.driver, 15)
        selectors = [
            (By.ID, "p1050000"),
            (By.XPATH, "//li[@id='p1050000']"),
            (By.XPATH, "//li[contains(text(),'应用登录')]"),
            (By.XPATH, "//li[contains(text(),'应用')]"),
            (By.CSS_SELECTOR, "li#p1050000"),
            (By.CSS_SELECTOR, "#buttonList li:nth-child(4)"),
        ]

        for locator in selectors:
            try:
                button = wait.until(EC.element_to_be_clickable(locator))
                print(f"[INFO] 找到应用登录按钮: {locator[0]}={locator[1]}")
                return button
            except TimeoutException:
                continue

        raise RuntimeError("未找到应用登录按钮，无法继续进入 USM")

    def switch_to_app_login_frame(self):
        print("[INFO] 切换到应用登录 iframe...")
        self.driver.switch_to.default_content()
        wait = WebDriverWait(self.driver, 30)
        title5tab_frame = wait.until(EC.presence_of_element_located((By.ID, "title5tab")))
        self.driver.switch_to.frame(title5tab_frame)

        app_login_container = WebDriverWait(self.driver, 10).until(
            EC.presence_of_element_located((By.ID, "appLoginChoose_div0"))
        )
        inner_iframe = WebDriverWait(app_login_container, 10).until(
            lambda element: element.find_element(By.TAG_NAME, "iframe")
        )
        self.driver.switch_to.frame(inner_iframe)
        print("[INFO] 已进入应用登录 iframe")

    def open_usm_from_app_access(self):
        print("[INFO] 点击 appAccess 进入 USM...")
        self.wait_and_click((By.ID, "appAccess"), timeout=30)
        try:
            WebDriverWait(self.driver, 5).until(lambda d: len(d.window_handles) > 1)
            self.driver.switch_to.window(self.driver.window_handles[-1])
            print("[INFO] 已切换到 USM 新窗口")
        except TimeoutException:
            print("[INFO] 未检测到新窗口，继续在当前窗口等待 USM")
            
    def access_usm_console(self):
        """步骤3: 访问 USM 控制台并等待应用列表加载。"""
        usm_host = self.get_usm_host()
        print(f"[INFO] 确认USM控制台已打开: {usm_host}")
        
        # 切回主页面
        self.driver.switch_to.default_content()

        try:
            WebDriverWait(self.driver, 30).until(
                lambda d: usm_host in d.current_url
            )
        except TimeoutException as exc:
            raise RuntimeError(
                f"应用登录后未进入 USM，当前URL={self.driver.current_url}"
            ) from exc
        self.usm_window_handle = self.driver.current_window_handle
        self.usm_entry_url = self.driver.current_url
        
        # 打印当前页面信息
        print(f"[DEBUG] 当前URL: {self.driver.current_url}")
        print(f"[DEBUG] 页面标题: {self.driver.title}")
        
        try:
            # 等待iFrame1元素出现（WebDriverWait 动态等待，无需提前 sleep）
            print("[INFO] 等待iFrame1加载...")
            wait = WebDriverWait(self.driver, 60)
            wait.until(EC.presence_of_element_located((By.ID, "iFrame1")))
            print("[INFO] iFrame1已加载")
            
            # 切换到iFrame1
            print("[INFO] 切换到iFrame1...")
            self.driver.switch_to.frame("iFrame1")
            print("[INFO] 已切换到iFrame1")
            
            # 等待vue-grid-item元素出现
            print("[INFO] 等待页面内容加载...")
            try:
                wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "div.vue-grid-item")))
                print("[INFO] 页面内容已加载")
            except:
                print("[WARN] vue-grid-item未找到，继续执行")
            
        except Exception as e:
            raise RuntimeError(f"访问 USM 控制台失败: {e}") from e
            
    def enter_usm_app(self, app_config):
        """从 USM 控制台点击指定应用入口，并切换到打开后的页面。"""
        app_name = app_config["name"]
        self.switch_to_usm_app_list(app_name)
        app_div = self.find_usm_app_card(app_name)
        old_handles = set(self.driver.window_handles)

        print(f"[INFO] 从 USM 点击应用入口: {app_name}")
        self.driver.execute_script("arguments[0].click();", app_div)
        return self.switch_to_new_usm_app_window(app_name, old_handles)

    def switch_to_usm_app_list(self, app_name):
        if self.usm_window_handle and self.usm_window_handle in self.driver.window_handles:
            self.driver.switch_to.window(self.usm_window_handle)

        usm_host = self.get_usm_host()
        if usm_host not in self.driver.current_url:
            raise RuntimeError(
                f"当前不在 USM，无法点击 {app_name}。当前URL={self.driver.current_url}"
            )

        self.driver.switch_to.default_content()
        wait = WebDriverWait(self.driver, 30)
        iframe1 = wait.until(EC.presence_of_element_located((By.ID, "iFrame1")))
        self.driver.switch_to.frame(iframe1)

    def find_usm_app_card(self, app_name):
        app_xpath = (
            "//div[contains(@class,'customizedList') and contains(@class,'module')]"
            f"[contains(., '{app_name}')]"
        )
        return WebDriverWait(self.driver, 30).until(
            EC.visibility_of_element_located((By.XPATH, app_xpath))
        )

    def switch_to_new_usm_app_window(self, app_name, old_handles):
        try:
            WebDriverWait(self.driver, 15).until(
                lambda d: len(set(d.window_handles) - old_handles) > 0
            )
        except TimeoutException as exc:
            raise RuntimeError(f"点击 {app_name} 后未打开独立页面") from exc

        new_handle = list(set(self.driver.window_handles) - old_handles)[-1]
        self.driver.switch_to.window(new_handle)
        print(f"[INFO] 已切换到 {app_name} 页面: {self.driver.current_url}")

        try:
            WebDriverWait(self.driver, 30).until(
                lambda d: d.execute_script("return document.readyState") == "complete"
            )
        except TimeoutException:
            print(f"[WARN] {app_name} 页面未确认加载完成: 当前URL={self.driver.current_url}")
        return self.driver.current_window_handle

    def capture_usm_apps_cookies(self):
        """逐个打开 USM 应用并捕获对应 Cookie。"""
        for app_config in self.get_usm_cookie_apps():
            stage = app_config.get("stage")
            app_name = app_config.get("name")
            if not stage or not app_name:
                raise ValueError("usm_cookie_apps 每一项都必须包含 stage 和 name")

            print(f"[INFO] 准备捕获 USM 应用 Cookie: {stage} / {app_name}")
            app_handle = self.enter_usm_app(app_config)
            self.opened_app_handles[stage] = app_handle
            self.capture_cookies(stage)
            if self.usm_window_handle and self.usm_window_handle in self.driver.window_handles:
                self.driver.switch_to.window(self.usm_window_handle)

    def switch_to_captured_app(self, stage_name):
        app_handle = self.opened_app_handles.get(stage_name)
        if app_handle and app_handle in self.driver.window_handles:
            self.driver.switch_to.window(app_handle)
            return True
        return False

    def get_preferred_jsessionid_domains(self):
        domains = []
        for raw_domain in [urlparse(self.driver.current_url).hostname or ""]:
            domain = raw_domain.lstrip('.')
            if domain and domain not in domains:
                domains.append(domain)
        return domains
        
    def get_jsessionid(self):
        """获取JSESSIONID"""
        print("[INFO] 正在获取JSESSIONID...")

        jsessionid_stage = self.get_jsessionid_stage()
        if not self.switch_to_captured_app(jsessionid_stage):
            raise RuntimeError(
                f"未找到 {jsessionid_stage} 已打开页面，无法获取 JSESSIONID。"
                "请确认 usm_cookie_apps 中包含该 stage。"
            )
        current_url = self.driver.current_url
        print(f"[INFO] 当前URL: {current_url}")
        
        cookies = self.driver.get_cookies()
        jsessionid = None
        cookie_info = None
        preferred_domains = self.get_preferred_jsessionid_domains()
        
        for cookie in cookies:
            if cookie['name'] == 'JSESSIONID':
                # domain 兼容带点前缀的情况，如 '.bass.ha.cmcc'
                cookie_domain = cookie.get('domain', '').lstrip('.')
                if cookie_domain in preferred_domains:
                    jsessionid = cookie['value']
                    cookie_info = cookie
                    break
                # 如果没有找到指定域的，记录第一个找到的作为备选
                elif jsessionid is None:
                    jsessionid = cookie['value']
                    cookie_info = cookie
        
        if jsessionid:
            print(f"[SUCCESS] 成功获取JSESSIONID: {jsessionid[:20]}...")
            print(f"[INFO] Domain: {cookie_info.get('domain')}")
            print(f"[INFO] Path: {cookie_info.get('path')}")
            # 警告：若最终获取的不是目标域，说明导航或登录流程有问题
            actual_domain = cookie_info.get('domain', '').lstrip('.')
            if actual_domain not in preferred_domains:
                print(f"[WARN] 获取的JSESSIONID来自 {actual_domain}，优先域为 {preferred_domains}！")
            return jsessionid, cookie_info
        else:
            print("[ERROR] 未能获取JSESSIONID")
            print("[DEBUG] 当前所有cookies:")
            for c in cookies:
                print(f"  - {c['name']}: domain={c.get('domain')}, path={c.get('path')}")
            return None, None
            
    def login_and_capture_cookies(self):
        """执行登录流程并捕获配置中的阶段 Cookie。"""
        if not self.driver:
            self.init_driver()

        self.login_ngboss()
        self.ensure_ngboss_main_loaded()
        self.capture_cookies("ngboss_main")

        self.click_app_login()

        self.access_usm_console()
        self.capture_cookies("usm_console")

        self.capture_usm_apps_cookies()

        result = {
            "cookie_dump": str(self.cookie_recorder.output_path) if self.cookie_recorder else None,
            "opened_app_handles": dict(self.opened_app_handles),
        }

        if self.get_jsessionid_stage():
            jsessionid, cookie_info = self.get_jsessionid()
            result["jsessionid"] = jsessionid
            result["cookie_info"] = cookie_info

        return result

    def close(self):
        """关闭浏览器。"""
        if self.driver:
            print("[INFO] 正在关闭浏览器...")
            self.driver.quit()
            self.driver = None
            print("[INFO] 浏览器已关闭")
        
    def run(self):
        """执行完整流程"""
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
