# 模块配置

此目录中的 `runtime/...` 是逻辑路径，由 `resolve_runtime_path()` 映射到
`C:\AutoNotifyRuntime`。不允许使用绝对路径、`../` 路径或旧的未分类
`runtime/<目录>/...` 路径。

仅允许以下目录结构：

- `runtime/session/...`：Cookie、会话健康状态、浏览器 Profile 和锁。
- `runtime/modules/<模块名>/output/...`：模块独立运行产物。
- `runtime/flow/<任务名>/output|backup|debug|tmp/...`：通报 Flow 的产物、备份、诊断与中间文件。

模块单独运行时，会使用其自身配置的 `runtime/...` 输出路径。通知 Flow
会为生成的配置、模板、清单、发送包和备份覆盖为任务专属的四类路径。
旧的 `runtime/cookies`、`runtime/browser_session`、`runtime/flow/<任务>/templates`
等路径不再读取或兼容。

保持 `*.local.json` 最小化。它们只能包含凭据、Webhook URL 或其他机器专属
覆盖项。不要将基础数组或模块默认值复制到本机文件中，因为本机覆盖会进行深度合并，
并可能掩盖后续基础配置的变更。

`autologin.json` 的 `login_command` 使用 `{python_executable}`。会话管理器会将
其展开为当前 Worker 使用的 Python 解释器。
