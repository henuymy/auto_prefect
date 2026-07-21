# 统一失败告警设计

## 目标

任意运行环境中的通报、驾驶舱和 Session Keeper 最终故障都应发送企业微信告警；Session Keeper
恢复后应发送一次恢复通知。

## 范围

- 移除业务运行失败告警对 `AUTO_NOTIFY_ENVIRONMENT=development` 的限制。
- 将 Session Keeper 的 `SessionInfrastructureError` 和 `SessionLoginError` 接入共享会话告警。
- 保持正常通报消息发送逻辑不变。

## 告警策略

| 场景 | 行为 |
| --- | --- |
| 通报或驾驶舱最终失败 | 任意环境发送一次失败告警；同一 Flow Run 重复调用抑制。 |
| Session Keeper 基础设施或登录失败 | 首次故障发送一次共享会话故障告警；持续同一共享会话故障抑制。 |
| Session Keeper 成功探活 | 若存在活动共享会话故障，发送一次恢复通知并清除活动状态。 |
| 告警投递异常 | 只记录告警投递失败，不改变业务或 Keeper 的原始成功/失败结果。 |

## 实现边界

业务失败告警继续使用独立的按 Flow Run 去重状态。Session Keeper 使用现有共享会话告警的
`incident_state_path`，以 `authentication:shared-session` 或 `infrastructure:shared-session` 区分
故障类别。告警内容沿用既有脱敏规则，不记录命令、账号、密码、验证码、Cookie、Token 或 Webhook。

## 验证

通过单元测试覆盖：生产环境业务失败仍会发送告警；Keeper 的基础设施和登录失败均会调用告警；
连续同类失败被抑制；故障后健康结果发送恢复通知；告警投递异常不会遮蔽原始异常。
