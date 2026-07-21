# Prefect Deployment 单一声明 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将远端已有的 19 个 Prefect Deployment 固化到 `prefect.yaml`，再删除远端旧对象。

**Architecture:** `prefect.yaml` 保存系统任务与通报任务的完整声明，通报任务仅引用相对的任务配置路径。一个纯本地测试锁定 19 个名称、入口、Pool、Cron、时区和启用状态；通过测试后才调用 Prefect 官方客户端删除远端对象并读取 API 验证清空。

**Tech Stack:** Prefect 3.x、YAML、pytest、Python asyncio。

## Global Constraints

- 仅操作当前 `config/runtime.local.json` 指向的 Prefect API，不输出其凭据或连接串。
- Deployment 数量固定为 19；不修改数据库、Flow Run 历史、Work Pool、Automation 或本地运行配置。
- 所有 `config_path` 必须相对于仓库根目录，禁止桌面目录或任何绝对路径。
- 保留远端 Cron、`Asia/Shanghai` 时区及启用状态；所有通知 Cron、Session Keeper 和驾驶舱采集 Cron 均保持暂停。

---

### Task 1: 固化并锁定 YAML Deployment 清单

**Files:**
- Modify: `prefect.yaml`
- Create: `tests/test_prefect_deployment_manifest.py`

**Interfaces:**
- Consumes: `prefect.yaml` 的 Prefect 3 Deployment 声明。
- Produces: 19 个可由 `python -X utf8 -m prefect deploy --all` 发布的静态 Deployment 定义。

- [ ] **Step 1: 编写清单测试**

```python
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_NAMES = {
    "dashboard-collection", "dashboard-daily-acc", "dashboard-indicator-sync",
    "dashboard-monthly", "dashboard-v2-partition-maintenance", "session-keeper",
    "notify-PK赛通报", "notify-升档市公司通报", "notify-家客和存量通报",
    "notify-日常进度", "notify-流量市公司通报", "notify-爱家亲情网区公司通报",
    "notify-爱家亲情网市公司通报", "notify-爱家亲情网每日盯控",
    "notify-爱家亲情网每日盯控-网格-渠道经理", "notify-爱家亲情网营业厅每日盯控",
    "notify-离网市公司通报", "notify-网格区公司PK赛通报", "notify-降档市公司通报",
}

def test_prefect_manifest_declares_all_deployments_with_relative_task_paths():
    payload = yaml.safe_load((ROOT / "prefect.yaml").read_text(encoding="utf-8"))
    deployments = {item["name"]: item for item in payload["deployments"]}
    assert set(deployments) == EXPECTED_NAMES
    for name, deployment in deployments.items():
        if name.startswith("notify-"):
            assert deployment["entrypoint"] == "flows/notify_single_flow.py:auto_notify_flow"
            assert deployment["work_pool"]["name"] == "windows-notify-pool"
            assert not Path(deployment["parameters"]["config_path"]).is_absolute()


def test_prefect_manifest_preserves_remote_schedule_state():
    payload = yaml.safe_load((ROOT / "prefect.yaml").read_text(encoding="utf-8"))
    deployments = {item["name"]: item for item in payload["deployments"]}
    assert deployments["dashboard-collection"]["schedules"] == [{
        "cron": "*/5 * * * *", "timezone": "Asia/Shanghai", "active": False,
    }]
    assert deployments["session-keeper"]["schedules"] == [{
        "cron": "*/10 * * * *", "timezone": "Asia/Shanghai", "active": False,
    }]
    assert deployments["dashboard-indicator-sync"]["schedules"][0]["active"] is True
    assert deployments["dashboard-v2-partition-maintenance"]["schedules"][0]["active"] is True
    assert deployments["notify-PK赛通报"]["schedules"][0]["cron"] == "0 9-13 * * *"
    assert deployments["notify-日常进度"]["schedules"][-1]["cron"] == "0 19 * * *"
    assert deployments["notify-网格区公司PK赛通报"]["schedules"] == [{
        "cron": "23 9-19 * * *", "timezone": "Asia/Shanghai", "active": False,
    }]
    for name in ("notify-升档市公司通报", "notify-流量市公司通报", "notify-离网市公司通报", "notify-降档市公司通报"):
        assert "schedules" not in deployments[name]
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest -q tests/test_prefect_deployment_manifest.py`

Expected: FAIL，因为现有 YAML 仅声明 6 个 Deployment。

- [ ] **Step 3: 更新 YAML**

将 13 个 `notify-*` Deployment 加入 `prefect.yaml`；每项使用 `flows/notify_single_flow.py:auto_notify_flow`、`windows-notify-pool` 以及 `config/tasks/<名称>.json`。将远端暂停的 Cron 显式写为 `active: false`，并将 `dashboard-collection` 和 `session-keeper` 同步为暂停。

- [ ] **Step 4: 运行清单测试与 YAML 解析**

Run: `python -m pytest -q tests/test_prefect_deployment_manifest.py; python -c "from pathlib import Path; import yaml; yaml.safe_load(Path('prefect.yaml').read_text(encoding='utf-8')); print('prefect_yaml_ok')"`

Expected: 测试通过并输出 `prefect_yaml_ok`。

- [ ] **Step 5: 提交本地声明**

```powershell
git add prefect.yaml tests/test_prefect_deployment_manifest.py
git commit -m "feat: declare all Prefect deployments"
```

### Task 2: 删除旧远端 Deployment 并验证清空

**Files:**
- No repository file changes.

**Interfaces:**
- Consumes: `config/runtime.local.json` 导出的 `PREFECT_API_URL` 与 Prefect 官方客户端。
- Produces: 当前 API 中零个 Deployment，供新目录的 YAML 发布重新创建。

- [ ] **Step 1: 删除前核对数量**

Run a Python async script that calls `client.read_deployments(limit=200)` and aborts unless the count is exactly `19`.

Expected: 输出 `pre_delete_count=19`。

- [ ] **Step 2: 逐项删除**

```python
async with get_client() as client:
    deployments = await client.read_deployments(limit=200)
    for deployment in deployments:
        await client.delete_deployment(deployment.id)
```

Expected: 每个名称输出一条 `DELETED`；任何删除失败立即返回非零状态。

- [ ] **Step 3: 读取 API 验证清空**

Run a Python async script that prints `post_delete_count=<n>` and returns nonzero unless `n == 0`.

Expected: `post_delete_count=0`。

- [ ] **Step 4: 记录操作结果**

Run: `git status --short`

Expected: 仅显示本次 YAML、测试和计划相关改动；远端删除不会产生本地凭据或运行文件变更。
