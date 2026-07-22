# 登录失败诊断安全脱敏实现计划

> **供代理执行：** 必须使用 `superpowers:subagent-driven-development`（推荐）或 `superpowers:executing-plans` 逐任务实施。步骤使用复选框语法，便于跟踪。

**目标：** 在保留登录命令失败的安全、可操作原因的同时，确保所有认证材料不会出现在 Prefect 异常或告警中。

**架构：** 原始子进程输出只在内存中存在。`services.session_manager` 对已知失败族进行分类，只对敏感字段的值和全部 URL 进行遮蔽，再由错误类别和有长度限制的安全摘要组成现有登录命令异常。不修改运行时状态、启动行为或前端。

**技术栈：** Python 3.13、`re`、`subprocess`、pytest。

## 全局约束

- 不记录登录命令。
- 不保留用户名、密码、验证码、Cookie 值、Token 值、Storage 值、Authorization 值、完整 URL 或 URL 查询参数。
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

将“检测到敏感词就拒绝整段消息”的逻辑替换为按值脱敏。新增以下预编译正则：

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

在 `summarize_login_failure` 中先规范化文本；将完整 `http://` 或 `https://` URL 替换为 `<redacted-url>`；再将 `用户名`、`username`、`password`、`验证码`、`otp`、`cookie`、`token`、`storage`、`authorization` 或 `secret` 后面的字段值替换为 `<redacted>`。同时处理 `key=value`、`key: value`、`Authorization: Bearer value` 和 `Cookie: value` 格式。脱敏后应用既有 500 字符上限。

在 `run_login_command` 中保持“优先 stderr、stderr 为空时使用 stdout”的选择逻辑，并按下面形式组合错误：

```python
message = (
    f"登录命令执行失败，退出码={completed.returncode}，"
    f"错误类别={classify_login_failure(raw_diagnostic)}"
)
if diagnostic:
    message += f"，诊断={diagnostic}"
raise RuntimeError(message)
```

- [ ] **步骤 4：运行聚焦测试，确认通过**

运行：

```powershell
pytest tests/test_session_manager.py -k "classify_login_failure or login_command" -v
```

预期：通过。安全原因和错误类别可见，任何密钥或 URL 主机都不会出现。

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
