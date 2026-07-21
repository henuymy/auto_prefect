# Unified Failure Alerting Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在任何环境为通报、驾驶舱和 Session Keeper 的最终故障发送脱敏企业微信告警，并在 Keeper 恢复后通知一次。

**Architecture:** 移除业务 Run 告警的环境闸门，保留按 Flow Run 的持久化去重。Session Keeper Flow 使用已有的 `run_with_business_session_reporting` 包装会话准备操作；该包装复用共享会话故障的跨进程去重、异步投递与恢复通知能力。

**Tech Stack:** Python、Prefect、pytest、企业微信 Webhook。

## Global Constraints

- 不记录或发送账号、密码、验证码、Cookie、Token、Webhook 或登录命令。
- 告警投递失败不得掩盖通报、驾驶舱或 Keeper 的原始结果。
- 正常通报消息发送逻辑不变。
- 业务 Run 按 Flow Run 去重；共享会话按故障类别去重并在恢复时清除。

---

### Task 1: 移除业务失败告警的环境限制

**Files:**
- Modify: `services/business_run_alert_service.py:4-8,45-78`
- Modify: `tests/test_business_run_alert_service.py:15-37`

**Interfaces:**
- Produces: `notify_business_run_failure(config: dict, incident: dict, *, sender=send_text) -> dict`；无论 `config["environment"]` 是何值，都发送未重复的告警。

- [ ] **Step 1: 写失败测试**

将生产环境测试改为：

```python
def test_business_run_alert_sends_in_production(tmp_path):
    messages = []
    config = {
        "environment": "production",
        "webhook_url": "https://example.invalid/webhook",
        "incident_state_path": str(tmp_path / "business-runs.json"),
    }

    result = notify_business_run_failure(
        config,
        incident(),
        sender=lambda _url, message, timeout=30: messages.append(message) or {"errcode": 0},
    )

    assert result["sent"] is True
    assert len(messages) == 1
    assert "[业务 Run 失败]" in messages[0]
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_business_run_alert_service.py::test_business_run_alert_sends_in_production -v`

Expected: FAIL，当前返回 `reason="environment"`。

- [ ] **Step 3: 实现最小修改**

删除 `notify_business_run_failure` 顶部按环境返回抑制结果的分支，删除不再需要的 `os` 导入；将消息标题改为：

```python
"[业务 Run 失败]\n"
```

在 `report_current_business_run_failure` 传入配置时删除 `environment` 字段。

- [ ] **Step 4: 运行业务告警测试确认通过**

Run: `python -m pytest tests/test_business_run_alert_service.py -v`

Expected: PASS。

### Task 2: 为 Session Keeper 接入共享会话告警与恢复

**Files:**
- Modify: `flows/session_keeper_flow.py:5-49`
- Modify: `tests/test_session_keeper_flow.py:1-72`
- Test: `tests/test_session_business_failure_service.py`

**Interfaces:**
- Consumes: `run_with_business_session_reporting(operation, *, trigger_source, flow_name, affected_stage, recover_on_success) -> dict`。
- Produces: `session_keeper_flow(...) -> dict`；登录或基础设施故障保持抛出，同时派发一次共享会话失败告警；健康结果派发一次恢复通知。

- [ ] **Step 1: 写失败与恢复委托测试**

在 `tests/test_session_keeper_flow.py` 加入：

```python
def test_keeper_routes_failures_and_recovery_through_shared_session_reporting(monkeypatch):
    calls = []

    def reporting_wrapper(operation, **kwargs):
        calls.append(kwargs)
        return operation()

    monkeypatch.setattr(keeper_module, "run_with_business_session_reporting", reporting_wrapper)
    monkeypatch.setattr(keeper_module, "run_session_keeper", lambda *_args: {"status": "reused"})

    assert keeper_module.session_keeper_flow.fn() == {"status": "reused"}
    assert calls == [{
        "trigger_source": "session-keeper",
        "flow_name": "session-keeper-flow",
        "affected_stage": "共享会话",
        "recover_on_success": True,
    }]
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_session_keeper_flow.py::test_keeper_routes_failures_and_recovery_through_shared_session_reporting -v`

Expected: FAIL，因为模块尚未导入或调用共享会话告警包装器。

- [ ] **Step 3: 实现最小接入**

在 `flows/session_keeper_flow.py` 导入：

```python
from services.session_business_failure_service import run_with_business_session_reporting
```

在 `session_keeper_flow` 中将直接调用替换为：

```python
result = run_with_business_session_reporting(
    lambda: run_session_keeper(config_path),
    trigger_source="session-keeper",
    flow_name="session-keeper-flow",
    affected_stage="共享会话",
    recover_on_success=True,
)
```

- [ ] **Step 4: 运行 Keeper 与共享会话告警测试确认通过**

Run: `python -m pytest tests/test_session_keeper_flow.py tests/test_session_business_failure_service.py -v`

Expected: PASS。

### Task 3: 回归验证

**Files:**
- Verify: `services/business_run_alert_service.py`
- Verify: `flows/session_keeper_flow.py`

- [ ] **Step 1: 运行相关完整回归测试**

Run: `python -m pytest tests/test_business_run_alert_service.py tests/test_session_alert_service.py tests/test_session_business_failure_service.py tests/test_session_keeper_flow.py -q`

Expected: PASS。

- [ ] **Step 2: 提交**

```bash
git add services/business_run_alert_service.py flows/session_keeper_flow.py tests/test_business_run_alert_service.py tests/test_session_keeper_flow.py
git commit -m "feat: alert on all environment failures"
```
