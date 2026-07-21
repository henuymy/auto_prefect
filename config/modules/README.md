# 模块配置

此目录中的运行路径相对运行根目录，由 `resolve_runtime_relative_path()` 映射到
`C:\AutoNotifyRuntime`。不允许使用绝对路径、`../` 路径或旧的 `runtime/` 前缀。

仅允许以下目录结构：

- `session/...`：Cookie、会话健康状态、浏览器 Profile 和锁。
- `modules/<模块名>/output/...`：模块独立运行产物。
- `flow/<任务名>/output|backup|debug|tmp/...`：通报 Flow 的产物、备份、诊断与中间文件。

模块单独运行时，会使用其自身配置的根相对输出路径。通知 Flow
会为生成的配置、模板、清单、发送包和备份覆盖为任务专属的四类路径。
旧的 `cookies`、`browser_session`、`flow/<任务>/templates`
等路径不再读取或兼容。

模块基础配置只包含可提交的默认值。凭据、Webhook URL 和其他机器专属字段统一放在
`config/runtime.local.json` 的 `module_overrides.<模块文件名>` 中，例如
`module_overrides.login_config`。覆盖仅写入与基础配置不同的字段，因为深度合并时复制
基础数组或默认值会掩盖后续基础配置的变更。

`autologin.json` 的 `login_command` 使用 `{python_executable}`。会话管理器会将
其展开为当前 Worker 使用的 Python 解释器。

`autologin.json` 的 `stage_probes.<stage>` 默认是一个探活对象。需要同一 stage
执行多个探活时，可改为含 `probes` 数组的对象；所有启用的探活均成功才视为该
stage 健康。每个探活对象可使用 `headers_from_cookies` 或
`headers_from_session_storage` 动态引用当前 stage 的认证材料，禁止写入固定
Cookie、Token 或账号密码。
