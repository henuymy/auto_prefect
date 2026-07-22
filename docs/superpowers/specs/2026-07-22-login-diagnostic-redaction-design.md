# 登录失败诊断脱敏优化设计

## 目标

在 Prefect 的登录失败异常中保留可操作的非敏感原因，同时确保账号、密码、验证码、Cookie、Token、Authorization 值和带查询参数的 URL 不会写入日志、异常或告警。

## 范围

仅修改 `services/session_manager.py` 的登录命令失败摘要，以及对应的单元测试。不修改 Prefect 启动流程、会话重试次数、运行监控页或运行时文件。

## 设计

登录子进程返回非零退出码时，先从 stderr（为空时 stdout）读取诊断。诊断处理分为两步：

1. 基于稳定模式生成错误类别：`otp_timeout`、`login_page_timeout`、`browser_error`、`session_capture_failed` 或 `unknown`。
2. 将敏感字段的值、认证请求头和 URL 查询参数替换为 `<redacted>`，保留其余规范化文本，最长 500 字符。

异常格式包含退出码、错误类别和安全摘要。若处理后没有可安全展示的摘要，则只保留错误类别，不回退到原始输出。

## 安全约束

- 不记录登录命令。
- 不保留用户名、密码、验证码、Cookie、Token、Storage、Authorization 的值。
- 不保留完整 URL 或查询参数。
- 未被明确安全处理的诊断不应绕过现有长度限制。

## 测试

- 含密码、Token、Cookie、Authorization 和 URL 查询参数的输出会掩盖值，但保留安全故障原因和类别。
- 验证码超时、登录页超时、WebDriver 错误和会话捕获失败映射到预期类别。
- 无法分类的输出使用 `unknown`。
- 既有纯文本、无敏感信息的诊断继续可见。
