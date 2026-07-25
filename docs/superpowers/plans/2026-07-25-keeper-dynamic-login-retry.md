# Session Keeper 动态登录重试 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 Session Keeper 用两次无固定睡眠的完整登录恢复瞬时 WebDriver 失败，并保留安全、可定位的失败阶段诊断。

**Architecture:** Keeper 停止覆盖 `prepare_session()` 的配置重试次数。会话管理器将两次尝试之间的固定延迟限制为零，下一次尝试前复用现有 Profile 条件清理。登录子进程仅输出严格白名单化的诊断标记，父进程验证并格式化该标记，绝不传播原始 stderr/stdout。

**Tech Stack:** Python 3.13, Selenium, subprocess, pytest, Ruff.

## Global Constraints

- 专用 Profile 为 `C:\\AutoNotifyRuntime\\session\\browser-profile`；仅按命令行 Profile 路径关闭关联 Edge 进程。
- 不关闭 `retain_after_login`，不删除 Profile，不使用全局 `taskkill /IM msedge.exe`。
- `browser_cleanup_failed` 必须直接停止本轮，不能再次启动 Edge。
- 不输出账号、密码、验证码、Cookie、Token、Storage、Authorization、完整 URL、查询参数或原始 Selenium 错误。
- Notify Flow 继续传入 `login_attempts=1`。

---

### Task 1: 锁定动态重试与 Keeper 参数行为

**Files:**
- Modify: `tests/test_session_keeper_flow.py`
- Modify: `tests/test_session_manager.py`

**Interfaces:**
- `run_prepare_session(config)` 调用 `prepare_session_task` 时不传 `login_attempts`。
- `run_login_with_retry(login_attempt, max_attempts=2, retry_delay_seconds=0, sleeper=time.sleep)` 在零延迟下连续调用两次尝试。
- `prepare_session(config)` 只接受 `login_retry_delay_seconds=0`。

- [x] **Step 1: Write failing tests**

为 Keeper fake task 记录调用参数，断言不包含 `login_attempts`。为重试函数写一个第一次抛出 `RuntimeError`、第二次返回值的测试，传入零延迟睡眠记录器并断言两次调用、零次休眠。再为 `prepare_session()` 写一项 `login_retry_delay_seconds=60` 抛出 `ValueError` 的测试。

- [x] **Step 2: Verify red**

Run `python -m pytest tests/test_session_keeper_flow.py tests/test_session_manager.py -k "keeper and login_attempts or retry and zero or retry_delay" -q`. Expected: the Keeper still passes a single attempt and configuration still requires 60 seconds.

- [x] **Step 3: Minimal implementation**

移除 Keeper 调用中的 `login_attempts=1`。把 `autologin.json` 的延迟值改为 `0`；会话管理器默认值改为 `0` 且拒绝任何非零配置。仅当延迟大于零时才调用休眠器，保持函数可测试；实际配置不会进入该分支。锁等待下限继续按配置值计算。

- [x] **Step 4: Verify green**

Repeat Step 2. Expected: PASS.

### Task 2: 锁定安全子进程诊断契约

**Files:**
- Modify: `tests/test_login_service.py`
- Modify: `tests/test_session_manager.py`

**Interfaces:**
- `AutoLogin.run()` 的失败 stderr 仅含 `AUTO_NOTIFY_LOGIN_DIAGNOSTIC phase=<白名单> exception=<类名> reason=<白名单>`。
- `run_login_command()` 从有效标记生成安全诊断；无效标记或原始输出只保留既有固定摘要。

- [x] **Step 1: Write failing tests**

为 `AutoLogin` 注入 WebDriverException，断言 stderr 含 `phase=init_driver`、`exception=WebDriverException` 和白名单原因码，且不含原始异常正文。为父进程构造含安全标记和敏感原文的 stderr，断言异常包含安全标记字段但不含敏感值；再构造含伪造字段的标记，断言伪造字段不进入异常。

- [x] **Step 2: Verify red**

Run `python -m pytest tests/test_login_service.py tests/test_session_manager.py -k "diagnostic and (phase or marker)" -q`. Expected: FAIL because the child only prints its raw exception and the parent only emits the fixed category summary.

- [x] **Step 3: Minimal implementation**

在 `login_service.py` 增加固定阶段名、原因码映射和单行 stderr 标记输出；在每个顶层登录阶段调用前更新当前阶段。`session_manager.py` 增加严格标记解析器，只接受定义的阶段、异常类名形式和原因码；将解析结果追加到安全固定摘要，其他输出一律丢弃。

- [x] **Step 4: Verify green**

Repeat Step 2. Expected: PASS.

### Task 3: 更新运维约定并回归验证

**Files:**
- Modify: `README.md`
- Modify: `PROJECT_GUIDE.md`

- [x] **Step 1: Update Chinese operations documentation**

说明 Keeper 使用两次完整登录、两次之间无固定 60 秒等待、再次尝试前仍执行 Profile 条件清理；说明 `browser_cleanup_failed` 不重试；列出安全诊断字段和禁止记录的内容。

- [x] **Step 2: Run regression and static checks**

Run `python -m pytest -p no:cacheprovider tests/test_browser_session.py tests/test_login_service.py tests/test_session_manager.py tests/test_session_keeper_flow.py -q`, then Ruff for those source and test files, then `git diff --check`. Expected: all pass.
