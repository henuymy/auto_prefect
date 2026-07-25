# Session Keeper 动态登录重试与安全诊断设计

## 目标

解决 Session Keeper 在冷启动时首次完整登录出现短暂 WebDriver 异常后立即失败的问题：Keeper 应使用既有的两次完整登录上限，两个尝试之间不执行固定 60 秒睡眠；下一次尝试开始前由专用 Edge Profile 的条件清理协议决定何时可继续。同时，在不泄露认证材料或原始错误文本的前提下，记录子登录失败的阶段、异常类型和白名单原因码。

## 事实依据

2026-07-25 的 `judicious-seriema` Run 已加载 Profile 定向清理改动，并在 1.12 秒内完成清理，随后子登录以 `browser_error` 失败。该 Run 的堆栈和日志确认 `session_keeper_flow.py` 显式传入 `login_attempts=1`，覆盖了 `autologin.json` 的 `login_max_attempts=2`。12 秒后的同一 Deployment Run 成功，说明该故障是瞬时的，不能归因为持久的 Profile 清理失败。

## 范围

- Session Keeper 不再传入 `login_attempts=1`，使用配置的两次完整登录上限。
- `login_retry_delay_seconds` 固定为 `0`；第二次尝试立即开始，其内部 `close_browser_session()` 保持按 Profile 的 0.5 秒条件轮询和现有超时上限。
- `browser_cleanup_failed` 与 `browser_profile_in_use` 保持立即结束，不进行第二次登录。
- 登录子进程在失败时向 stderr 写入受控标记，仅包含阶段、Python 异常类名和白名单原因码。父进程只解析该标记，不记录子进程原始 stderr/stdout。
- 更新 Session Keeper 运维说明和变更记录。

## 非范围

- 不关闭 `retain_after_login`，不删除 Profile，不使用全局 `taskkill /IM msedge.exe`。
- 不改变 Notify Flow 显式单次登录的策略。
- 不记录用户名、密码、验证码、Cookie、Token、Storage、Authorization、完整 URL、查询参数或 Selenium 原始异常文本。
- 不改变浏览器清理失败的安全失败语义。

## 设计

### 动态重试

`run_prepare_session()` 让 `prepare_session()` 自行选择 `login_max_attempts`。配置中的 `login_max_attempts` 继续固定为 `2`，而 `login_retry_delay_seconds` 改为 `0` 并由代码强制校验。首次命令失败后，重试循环不做额外睡眠；第二次 `login_attempt()` 会重新执行现有的专用 Profile 清理。若 Edge 尚未退出，清理代码每 0.5 秒轮询，退出即刻继续，达到 `browser_close_wait_seconds` 上限仍未确认则报告 `browser_cleanup_failed` 并停止。

### 失败阶段与原因码

`AutoLogin` 维护受控阶段名：`init_driver`、`ngboss_login`、`ngboss_main`、`app_login`、`usm_console`、`app_session_capture`、`session_validation`。异常处理只向 stderr 输出一行固定格式：

`AUTO_NOTIFY_LOGIN_DIAGNOSTIC phase=<阶段> exception=<异常类名> reason=<原因码>`

原因码由严格模式映射得到：`profile_in_use`、`devtools_active_port`、`browser_start_failed`、`driver_unreachable`、`webdriver_unclassified`、`timeout` 或 `unexpected_exception`。任意未匹配文本只产生 `webdriver_unclassified` 或 `unexpected_exception`，不回显异常正文。

`run_login_command()` 在子进程非零退出时，优先提取该受控行，验证字段均属于白名单后再附加到既有错误类别和固定安全摘要。不存在或格式非法的标记时维持已有固定摘要。标记中的阶段可定位问题边界，异常类和原因码可区分启动冲突、驱动失联和网页流程失败，但不扩大日志暴露面。

## 测试

- Keeper 不再显式传入 `login_attempts=1`。
- 有效配置的两次尝试在第一次失败后不调用休眠器；第二次尝试会执行。
- 非零重试延迟配置被拒绝，防止重新引入固定等待。
- 清理失败仍只尝试一次。
- 子进程安全标记可被父进程解析；原始敏感文本和伪造标记不能进入异常。
- `AutoLogin` 对 WebDriver、超时和未知异常输出受控原因码及当前阶段。
- 聚焦会话、浏览器和 Keeper 测试以及 Ruff 通过。
