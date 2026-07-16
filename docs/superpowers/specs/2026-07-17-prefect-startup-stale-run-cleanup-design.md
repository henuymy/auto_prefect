# Prefect 启动时清理过期任务

## 目标

服务启动时不得补跑任何已经超过计划开始时间的历史任务。启动完成后只提交一条新的
Session Keeper Run，用于立即检查共享登录会话。

## 范围和术语

- **过期任务**：状态为 `SCHEDULED` 或 `PENDING`，且 `expected_start_time` 早于启动清理时刻的 Run。
- **历史任务**：启动清理开始前已存在的 Run；不区分自动调度和用户在 Prefect 页面手动提交。
- **运行中任务**：`RUNNING`、`CANCELLING` 或 `PAUSED` 状态的 Run。正常启动仍会拒绝继续，以免错误中断业务；仅 `scripts/run.ps1 -ForceRestart` 会取消它们。

## 决策

启动清理暂停受管 Deployment 后，统一取消所有过期任务，不再按照 Session、Dashboard 或 Notify
使用不同的宽限期和保留策略。清理结束并恢复 Deployment 后：

1. 启动 Session Worker 并等待它上线。
2. 通过 `session-keeper-flow/session-keeper` 提交一条新的手动 Run。
3. 启动 Dashboard 和 Notify Worker。

新的 Session Keeper Run 在清理之后才创建，因此不会被取消；过期的 Session Keeper Run 也不会补跑。
Dashboard 单例任务不再保留“最新的一次”过期 Run，手动提交但积压的任务同样取消。

## 实现边界

- 简化 `scripts/lib/prefect_startup_reconcile.py`：根据状态与开始时间选择所有过期 Run，取消原因统一为 `startup_overdue_run`。
- 删除只适用于 Notify 的 `scheduled_notify_grace_seconds` 启动配置读取与示例字段。
- 调整 `scripts/run.ps1` 的 Worker 启动顺序，确保首次提交的是新的 Session Keeper Run。
- 更新启动清理、运行环境和启动脚本的回归测试；更新项目说明中的启动策略。
- 不修改 Deployment 的长期 Cron，也不取消正常未来计划或非受管 Deployment。

## 备选方案

1. 仅取消 Dashboard 单例任务超过十分钟的 Run：仍会允许其他过期任务补跑，不能满足“所有过期任务均不补跑”。
2. 保留按来源的宽限期：配置更细，但启动行为难以预测。
3. 统一取消所有过期任务：规则明确，且每次启动都由新 Session Keeper 建立会话。采用此方案。

## 验证

- 单元测试覆盖自动和手动的过期 Run 都被取消、未来 Run 被保留、运行中 Run 的现有安全语义不变。
- 启动脚本契约测试覆盖清理在 Worker 启动前执行，以及新的 Session Keeper Run 仅提交一次。
- 运行相关聚焦测试、完整 Python 测试和 Ruff 检查。
