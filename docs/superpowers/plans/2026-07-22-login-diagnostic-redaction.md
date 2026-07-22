# 登录失败诊断安全脱敏实现计划

> **供代理执行：** 必须使用 `superpowers:subagent-driven-development`（推荐）或 `superpowers:executing-plans` 逐任务实施。步骤使用复选框语法，便于跟踪。

**目标：** 在保留登录命令失败的安全、可操作原因的同时，确保认证材料和完整登录命令不会出现在 Prefect 异常、告警或已持久化的结果中。

**架构：** 原始子进程输出只在内存中存在。`services.session_manager` 对已知失败族进行分类，并由类别生成固定安全摘要；任何子进程原文都不会进入异常。不修改运行时状态、启动行为或前端。

**技术栈：** Python 3.13、`re`、`subprocess`、pytest。

## 全局约束

- 不记录登录命令。
- 不在成功任务结果中返回登录命令。
- Runtime 会话文件仍存放在 `C:\\AutoNotifyRuntime\\session`，用于同一运行的下载请求。
- `stage_data` 不得写入 Prefect Task 持久化结果或 Keeper Flow 返回值。
- 不保留用户名、密码、验证码、Cookie、Token、Storage、Authorization、完整 URL 或 URL 查询参数的原文或值。
- 诊断长度不得超过 `MAX_LOGIN_ERROR_SUMMARY_LENGTH`（500 个字符）。
- 只能使用 `otp_timeout`、`login_page_timeout`、`browser_error`、`session_capture_failed` 和 `unknown` 五种类别。
- 保持既有 `RuntimeError` 与 `SessionLoginError` 行为不变。

---

### 任务 1：安全地分类并脱敏登录命令诊断

**文件：**
- 修改：`services/session_manager.py:664-795`
- 修改：`tests/test_session_manager.py:1149-1205`

**接口：**
- 产出：`classify_login_failure(error: object) -> str`，返回五种全局错误类别之一。
- 产出：`summarize_login_failure(error: object) -> str`，返回规范化、已脱敏且不超过 500 字符的字符串。
- 使用：`run_login_command(command, cwd=PROJECT_DIR, timeout_seconds=None, env=None) -> dict`；子进程非零退出时抛出包含 `退出码=<code>` 与 `错误类别=<category>` 的 `RuntimeError`。

- [ ] **步骤 1：编写失败测试**

在 `tests/test_session_manager.py` 现有登录命令诊断测试旁加入以下测试：

```python
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
                "等待 Gotify 验证码超时; username=alice; password=raw-password; "
                "token=raw-token; Authorization: Bearer raw-auth; "
                "Cookie: sid=raw-cookie; "
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
    assert "等待 Gotify 验证码超时" in message
    for secret in ("alice", "raw-password", "raw-token", "raw-auth", "raw-cookie", "raw-ticket"):
        assert secret not in message
    assert "internal.example" not in message
```

- [ ] **步骤 2：运行新增测试，确认其失败**

运行：

```powershell
pytest tests/test_session_manager.py -k "classify_login_failure or redacts_values_and_keeps_safe_reason" -v
```

预期：失败。原因是 `classify_login_failure` 尚不存在，且现有脱敏器会替换整段诊断。

- [ ] **步骤 3：实现最小安全格式化辅助函数**

将“检测到敏感词就拒绝整段消息”的逻辑替换为稳定类别和固定安全摘要。新增以下预编译正则：

```python
LOGIN_FAILURE_CATEGORIES = (
    ("otp_timeout", re.compile(r"(?is)(gotify|验证码|短信).{0,120}(timeout|超时)|(timeout|超时).{0,120}(gotify|验证码|短信)")),
    ("login_page_timeout", re.compile(r"(?is)(loginName|登录页|登录框).{0,120}(timeout|超时|未找到)|(timeout|超时).{0,120}(loginName|登录页|登录框)")),
    ("browser_error", re.compile(r"(?i)(webdriver|selenium|msedge|edge browser|driver.*启动|浏览器.*启动)")),
    ("session_capture_failed", re.compile(r"(?is)(cookie|storage).{0,120}(写入失败|未就绪|capture.*fail|ready.*fail)")),
)

def classify_login_failure(error: object) -> str:
    text = " ".join(str(error).split())
    for category, pattern in LOGIN_FAILURE_CATEGORIES:
        if pattern.search(text):
            return category
    return "unknown"
```

新增 `LOGIN_FAILURE_SUMMARIES`，为四种已知类别提供固定安全摘要。`summarize_login_failure` 只能从该映射返回摘要，未知类别返回 `unknown`；它不得拼接、清洗或截取任何原始子进程输出。保留既有 500 字符上限。

将 `subprocess.run(...)` 包在 `try` 中；捕获 `subprocess.TimeoutExpired` 时抛出下列异常且使用 `from None`，避免超时对象携带的登录命令进入异常链：

```python
raise RuntimeError("登录命令执行失败，错误类别=unknown") from None
```

在 `run_login_command` 中保持“优先 stderr、stderr 为空时使用 stdout”的选择逻辑，并按下面形式组合错误：

```python
message = (
    f"登录命令执行失败，退出码={completed.returncode}，"
    f"错误类别={classify_login_failure(raw_diagnostic)}"
)
if category != "unknown" and diagnostic:
    message += f"，诊断={diagnostic}"
raise RuntimeError(message)
```

- [ ] **步骤 4：运行聚焦测试，确认通过**

运行：

```powershell
pytest tests/test_session_manager.py -k "classify_login_failure or login_command" -v
```

预期：通过。安全原因和错误类别可见，任何密钥、URL 主机或子进程原文都不会出现。

- [ ] **步骤 5：运行会话回归测试**

运行：

```powershell
pytest tests/test_session_manager.py tests/test_session_keeper_flow.py -v
```

预期：通过。既有登录重试、失败告警和 Keeper 单次尝试行为保持不变。

- [ ] **步骤 6：提交实现**

```powershell
git add services/session_manager.py tests/test_session_manager.py
git commit -m "fix: preserve safe login failure diagnostics"
```

---

### 任务 2：移除成功登录结果中的命令文本

**文件：**
- 修改：`services/session_manager.py:811-1249`
- 修改：`tests/test_session_manager.py`

- [x] **步骤 1：编写并运行失败测试**

为 `run_login_command` 和 `prepare_session` 的成功路径加入断言：结果只含 `returncode=0`，不含 `command` 或密码文本。实现前两条测试均因命令字段泄露失败。

- [x] **步骤 2：最小化成功结果**

`run_login_command` 不再返回输入命令；`prepare_session` 丢弃子进程返回对象，仅写入固定的成功退出码。

- [x] **步骤 3：运行聚焦回归**

运行：

```powershell
python -m pytest tests\test_session_manager.py -k "success_result_does_not_include_login_command or success_result_does_not_include_command" -q
```

结果：2 passed。

---

### 任务 3：隔离 Prefect 会话认证结果

**文件：**
- 修改：`tasks/session_tasks.py:15-26`
- 修改：`flows/session_keeper_flow.py:45-59`
- 修改：`tests/test_session_keeper_flow.py`
- 修改：`tests/test_notify_flow_session.py`

**接口：**
- `prepare_session_task` 保持相同入参和内存返回值，但必须以 `persist_result=False` 声明。
- `session_keeper_flow` 仍记录健康确认，但返回值不得有 `stage_data`、Cookie 或 Storage 字段。
- `prepare_notify_session` 仍将 `stage_data` 原样交给同一运行的下载步骤。

- [x] **步骤 1：编写失败测试**

在 `tests/test_session_keeper_flow.py` 添加：

```python
from tasks import session_tasks


def test_keeper_task_disables_result_persistence():
    assert session_tasks.prepare_session_task.persist_result is False


def test_keeper_flow_omits_stage_data_from_return_value(monkeypatch):
    secret = "raw-session-token"

    class Logger:
        def info(self, *_args):
            pass

    monkeypatch.setattr(keeper_module, "get_run_logger", Logger)
    monkeypatch.setattr(
        keeper_module,
        "run_with_business_session_reporting",
        lambda operation, **_kwargs: operation(),
    )
    monkeypatch.setattr(
        keeper_module,
        "run_session_keeper",
        lambda *_args: {
            "status": "reused",
            "stages": ["city_ops"],
            "stage_data": {"city_ops": {"session_storage": {"uapToken": secret}}},
        },
    )

    result = keeper_module.session_keeper_flow.fn()

    assert result == {"status": "reused", "stages": ["city_ops"]}
    assert secret not in repr(result)
```

在 `tests/test_notify_flow_session.py` 添加内存兼容断言：

```python
def test_notify_session_keeps_stage_data_available_in_memory(monkeypatch):
    expected = {"status": "reused", "stage_data": {"city_ops": {"cookies": [{"value": "sid"}]}}}
    monkeypatch.setattr("flows.notify_single_flow.prepare_session_task", lambda *_args, **_kwargs: expected)
    monkeypatch.setattr("flows.notify_single_flow.run_notify_session_preparation", lambda operation: operation())

    assert prepare_notify_session({}, force_refresh=False) is expected
```

- [x] **步骤 2：运行失败测试**

运行：

```powershell
python -m pytest tests\test_session_keeper_flow.py tests\test_notify_flow_session.py -k "persistence or omits_stage_data or keeps_stage_data" -q
```

预期：前两项失败，分别因为 Task 未关闭结果持久化且 Keeper Flow 原样返回 `stage_data`；通知内存兼容断言通过。

- [x] **步骤 3：最小化实现**

将 Task 装饰器改为：

```python
@task(persist_result=False)
```

在 `flows/session_keeper_flow.py` 新增受控投影函数，只保留 `status`、`stage_health_path`、`stages`、`validation`、`login`、`close`、`login_attempt_count` 和 `lock` 中存在的键；随后由 `session_keeper_flow` 返回该投影，而不是原始 Broker 结果。

- [x] **步骤 4：运行聚焦测试**

重复步骤 2 的命令。

预期：全部通过；通知 Flow 的内存数据保持不变，Keeper 和 Task 结果不再形成 Prefect 中可持久化的认证材料副本。

- [x] **步骤 5：运行会话回归与静态检查**

```powershell
python -m pytest tests\test_session_manager.py tests\test_session_broker.py tests\test_session_keeper_flow.py tests\test_notify_flow_session.py -q
python -m ruff check services\session_manager.py tasks\session_tasks.py flows\session_keeper_flow.py tests\test_session_manager.py tests\test_session_keeper_flow.py tests\test_notify_flow_session.py
git diff --check
```

- [x] **步骤 6：提交实现**

```powershell
git add services/session_manager.py tasks/session_tasks.py flows/session_keeper_flow.py tests/test_session_manager.py tests/test_session_keeper_flow.py tests/test_notify_flow_session.py docs/superpowers
git commit -m "fix: keep session credentials out of Prefect results"
```
