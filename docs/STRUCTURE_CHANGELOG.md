# 项目结构变更日志

此文件记录会影响目录职责、运行入口、配置来源、脚本位置或兼容路径的变更。
业务逻辑的小修复、依赖版本更新和纯文案调整不需要记录。每次结构性修改合并前，在本文件顶部追加一条。

## 记录格式

### YYYY-MM-DD - 标题

- 原因：为什么调整。
- 结构变化：旧路径或入口到新路径或入口的映射。
- 兼容性：保留的旧命令、迁移期行为或破坏性变化。
- 验证：实际执行的测试、检查或构建命令。

## 2026-07-11 - 运行配置与脚本结构收敛

- 原因：本地数据库配置和启动脚本分散，日常操作难以识别唯一入口。
- 结构变化：`config/runtime.local.json` 成为首选私密配置；`scripts/run.ps1`、`scripts/stop.ps1`、`scripts/status.ps1` 成为日常生命周期入口；驾驶舱低频工具移动至 `scripts/tools/dashboard/`；内部运行实现移动至 `scripts/lib/`；开发与诊断脚本移动至 `scripts/dev/`；FRP、计划任务和历史栈脚本移动至 `scripts/legacy/`。
- 兼容性：`scripts/dev/*` 保留为新入口的转发；`scripts/start_public_stack.ps1` 和 `scripts/stop_public_stack.ps1` 保留为历史 FRP 栈命令的转发。JSON 缺失时继续回退到旧 `.local.ps1` 配置。
- 验证：`python -m pytest -p no:cacheprovider -q`、`python -m ruff check .`、`npm run typecheck`、`npm run build` 和 PowerShell 语法检查。
