# 登录失败诊断脱敏优化设计

## 目标

在 Prefect 的登录失败异常中保留可操作的非敏感原因，同时确保账号、密码、验证码、Cookie、Token、Authorization 值、带查询参数的 URL 和完整登录命令不会写入日志、异常、告警或已持久化的 Prefect 结果。

## 范围

修改登录命令结果、失败摘要和会话任务的结果边界，以及对应的单元测试。不修改 Prefect 启动流程、会话重试次数、运行监控页、下载接口或 Runtime 会话文件格式。

## 设计

登录子进程返回非零退出码时，先从 stderr（为空时 stdout）读取诊断。诊断处理分为两步：

1. 基于稳定模式生成错误类别：`otp_timeout`、`login_page_timeout`、`browser_error`、`session_capture_failed` 或 `unknown`。
2. 由类别生成固定的安全摘要；绝不将子进程原始输出、字段值、请求头或 URL 写入摘要。

异常格式包含退出码、错误类别和安全摘要。`unknown` 或登录命令超时只保留错误类别，并抑制 `TimeoutExpired` 的异常链，不回退到原始输出。

登录成功时，`run_login_command` 和 `prepare_session` 的结果只返回固定的成功退出码，不回传完整登录命令或未筛选的子进程结果。

Cookie 和 Storage 的唯一持久化位置保持为 `C:\\AutoNotifyRuntime\\session\\stages\\*.json`。`prepare_session_task` 禁用 Prefect 结果持久化，允许同一运行的通知 Flow 在内存中取得 `stage_data`；`session_keeper_flow` 在返回前移除 `stage_data` 和原始 `validation`，只对外暴露会话健康状态和受控元数据。

2026-07-22 实现状态：Task 结果显式设置 `persist_result=False`；Keeper Flow 在记录健康确认后，将 Broker 结果投影为不含 `stage_data` 和 `validation` 的受控键集。通知 Flow 保持原对象引用，因此同一运行的下载步骤仍能使用内存中的 `stage_data`。

## 安全约束

- 不记录登录命令。
- 不在成功任务结果中返回登录命令。
- 不将 `stage_data` 持久化为 Prefect Task 或 Keeper Flow 结果。
- 不改变 Runtime 会话文件中的 Cookie、Storage 数据，也不改变下载流程对其的内存消费。
- 不保留用户名、密码、验证码、Cookie、Token、Storage、Authorization 的值。
- 不保留完整 URL 或查询参数。
- 未被明确安全处理的诊断不应绕过现有长度限制。

## 测试

- 含密码、Token、Cookie、Authorization、验证码和 URL 查询参数的输出只会生成安全故障原因和类别。
- 验证码超时、登录页超时、WebDriver 错误和会话捕获失败映射到预期类别。
- 无法分类的输出使用 `unknown`。
- `TimeoutExpired` 中的命令文本不会进入异常、日志或告警。
- 成功登录的 `login` 结果只含退出码，不含命令文本。
- 会话 Task 禁用结果持久化；Keeper Flow 结果不包含 Cookie、Storage、`stage_data`、探活 URL 或探活实际值。
- 通知 Flow 在同一运行内仍可取得原始 `stage_data` 并传给下载任务。
- 既有纯文本、无敏感信息的诊断继续可见。
