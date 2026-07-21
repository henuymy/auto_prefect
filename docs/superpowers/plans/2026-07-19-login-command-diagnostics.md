# Login Command Diagnostics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让自动登录子进程失败时显示脱敏后的实际原因，而不是只有退出码。

**Architecture:** 在 `run_login_command` 已捕获的 `stderr` 和 `stdout` 中选择一条简短诊断，并复用现有 `summarize_login_failure` 完成脱敏与长度限制。异常不包含命令文本，优先使用 `stderr`，为空时回退 `stdout`。

**Tech Stack:** Python、subprocess、pytest。

## Global Constraints

- 不记录登录命令、账号、密码、验证码、Cookie、Token 或其他认证材料。
- 保持现有 `RuntimeError` 异常类型和非零退出码失败语义。
- 诊断文本必须经过 `summarize_login_failure`，并受 `MAX_LOGIN_ERROR_SUMMARY_LENGTH` 限制。

---

### Task 1: 安全地构造登录命令失败诊断

**Files:**
- Modify: `services/session_manager.py:774-794`
- Test: `tests/test_session_manager.py:1149-1160`

**Interfaces:**
- Consumes: `completed.returncode`, `completed.stderr`, `completed.stdout`。
- Produces: `run_login_command(command, cwd=PROJECT_DIR, timeout_seconds=None, env=None) -> dict`；非零退出码抛出包含退出码和安全诊断的 `RuntimeError`。

- [ ] **Step 1: 写失败测试**

在 `tests/test_session_manager.py` 现有的 `run_login_command` 失败测试后加入：

```python
def test_run_login_command_includes_sanitized_stderr_diagnostic(monkeypatch):
    completed = type(
        "Completed",
        (),
        {
            "returncode": 1,
            "stdout": "",
            "stderr": "等待 Gotify 验证码超时：45 秒内未收到可用短信转发。",
        },
    )()
    monkeypatch.setattr(session_manager.subprocess, "run", lambda *_args, **_kwargs: completed)

    with pytest.raises(RuntimeError) as exc_info:
        session_manager.run_login_command("sensitive command")

    assert "退出码=1" in str(exc_info.value)
    assert "等待 Gotify 验证码超时" in str(exc_info.value)
    assert "sensitive command" not in str(exc_info.value)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_session_manager.py::test_run_login_command_includes_sanitized_stderr_diagnostic -v`

Expected: FAIL，因为当前异常只含退出码。

- [ ] **Step 3: 实现最小修改**

将 `run_login_command` 中的非零退出码分支改为：

```python
    if completed.returncode != 0:
        raw_diagnostic = completed.stderr or completed.stdout or ""
        diagnostic = summarize_login_failure(raw_diagnostic)
        message = f"登录命令执行失败，退出码={completed.returncode}"
        if diagnostic:
            message += f"，诊断={diagnostic}"
        raise RuntimeError(message)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_session_manager.py::test_run_login_command_includes_sanitized_stderr_diagnostic -v`

Expected: PASS。

- [ ] **Step 5: 补充敏感诊断测试并确认脱敏**

在同一测试文件加入：

```python
def test_run_login_command_redacts_sensitive_child_diagnostic(monkeypatch):
    completed = type(
        "Completed",
        (),
        {"returncode": 1, "stdout": "", "stderr": "password=raw-password"},
    )()
    monkeypatch.setattr(session_manager.subprocess, "run", lambda *_args, **_kwargs: completed)

    with pytest.raises(RuntimeError) as exc_info:
        session_manager.run_login_command("sensitive command")

    message = str(exc_info.value)
    assert "<redacted login failure detail>" in message
    assert "raw-password" not in message
    assert "sensitive command" not in message
```

Run: `python -m pytest tests/test_session_manager.py -k "run_login_command" -v`

Expected: PASS。

- [ ] **Step 6: 运行相关回归测试**

Run: `python -m pytest tests/test_session_manager.py tests/test_session_alert_service.py -q`

Expected: PASS。

- [ ] **Step 7: 提交**

```bash
git add services/session_manager.py tests/test_session_manager.py
git commit -m "fix: expose sanitized login command diagnostics"
```
