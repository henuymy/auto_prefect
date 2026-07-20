# Prefect 监控实时 Webhook 实施计划
> **归档状态：** 本文记录当时的需求、设计或实施计划；当前有效的运行契约以 [运行监控中心设计](../../run-monitoring-center-design.md) 和 [Webhook 运维手册](../../operations/prefect-monitor-webhook.md) 为准。
>


> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**目标：** 接收 Prefect 通报任务状态事件，原子写入 MySQL，并在提交后通过 WebSocket 向监控页面推送增量；端到端 P95 延迟不超过 5 秒。

**架构：** Prefect Automation 调用 POST /api/monitor/events/prefect。后端验证密钥、以 Prefect 事件 ID 去重、只在首次见到 Flow Run 时通过官方 REST API 补全真实 Deployment 身份，然后在一个 MySQL 事务中保存运行状态与事件。事务成功后，单 FastAPI 进程的 WebSocket Hub 发布 run.updated；REST 快照用于首次连接和重连，对账任务只负责补漏。

**技术栈：** Python 3.13、Prefect 3.7、FastAPI、SQLAlchemy、Alembic、MySQL、React、TypeScript、pytest、Vitest。

## 全局约束

- 运行时代码只使用 Prefect 官方 HTTP API，禁止访问 Prefect PostgreSQL 内部表。
- 通报身份只接受 Flow 为 auto-notify-flow 且 Deployment 名称以 notify- 开头的 Flow Run；目标 ID 继续使用 report-<deployment UUID>。
- MySQL 是唯一持久化监控数据源；首期不安装 Redis。
- 密钥只存在于被忽略的 config/runtime.local.json 和进程环境变量中，不能进入日志、测试快照或提交文件。
- MySQL 提交成功前不得 WebSocket 广播。
- 重复事件或比已保存状态更旧的事件，不能覆盖当前运行状态。
- 非通报事件应被确认接收，但不得出现在通报运行记录、时间线或待执行队列。
- 将现有 15 秒全量同步替换为每 5 分钟重叠窗口对账；每日做一次 30 天历史校验。

## 文件职责

| 文件 | 职责 |
| --- | --- |
| config/runtime.local.example.json | Webhook 密钥的无真实值配置入口。 |
| scripts/lib/runtime_config.ps1 | 将密钥导出为后端环境变量。 |
| models/monitor.py | 事件幂等键、状态发生时间的 ORM 字段。 |
| migrations/dashboard_v2/versions/20260719_0004_monitor_event_idempotency.py | 生产 MySQL 表结构迁移。 |
| backend/services/mysql_monitor_store.py | 单事务写入运行状态与事件。 |
| backend/services/prefect_monitor_event_service.py | Prefect 事件校验、身份补全、状态处理。 |
| backend/services/monitor_snapshot_service.py | REST 快照与增量消息的统一构建。 |
| backend/services/monitor_stream.py | 单进程 WebSocket 连接、线程安全广播和 `MonitorRealtimeStatus` 健康状态。 |
| backend/routers/monitor.py | Webhook、REST 快照和 WebSocket 接口。 |
| backend/services/monitor_sync_service.py | 5 分钟补漏与对账。 |
| frontend/src/monitor/types.ts、apiMonitorService.ts、MonitorCenter.tsx | run.updated 类型与局部状态合并。 |
| docs/operations/prefect-monitor-webhook.md | Prefect Automation 配置和验收手册。 |

---

### Task 1: 添加密钥配置与事件幂等字段

**文件：**

- 修改：config/runtime.local.example.json
- 修改：scripts/lib/runtime_config.ps1
- 修改：models/monitor.py
- 创建：migrations/dashboard_v2/versions/20260719_0004_monitor_event_idempotency.py
- 修改：tests/test_monitor_event_service.py
- 修改：tests/test_monitor_migration.py
- 修改：tests/test_development_environment_contract.py

**接口：**

- 输入：monitor.prefect_webhook_secret。
- 输出：PREFECT_MONITOR_WEBHOOK_SECRET、MonitorRun.state_occurred_at、MonitorEvent.source_event_id。

- [ ] **步骤 1：添加失败测试。**

~~~python
def test_monitor_models_store_event_identity_and_state_time() -> None:
    assert MonitorRun.__table__.c.state_occurred_at.nullable is True
    assert MonitorEvent.__table__.c.source_event_id.type.length == 64
    assert MonitorEvent.__table__.c.source_event_id.nullable is True


def test_runtime_config_exports_monitor_webhook_secret() -> None:
    source = (ROOT / "scripts" / "lib" / "runtime_config.ps1").read_text(encoding="utf-8")
    assert 'monitor.prefect_webhook_secret' in source
    assert 'PREFECT_MONITOR_WEBHOOK_SECRET' in source
~~~

- [ ] **步骤 2：确认测试先失败。**

运行：python -m pytest -p no:cacheprovider tests/test_monitor_event_service.py tests/test_development_environment_contract.py -q

预期：失败，提示新的模型字段或环境变量尚未定义。

- [ ] **步骤 3：补充配置、模型与迁移。**

在示例 JSON 中添加：

~~~json
"monitor": {
  "prefect_webhook_secret": "replace-with-a-long-random-local-secret"
}
~~~

在 Import-RuntimeConfig 中添加：

~~~powershell
$env:PREFECT_MONITOR_WEBHOOK_SECRET = Get-RuntimeConfigValue -Config $config -Path "monitor.prefect_webhook_secret"
~~~

在模型中声明：

~~~python
class MonitorRun(DashboardV2Base):
    state_occurred_at: Mapped[datetime | None] = mapped_column(DATETIME_MS)

class MonitorEvent(DashboardV2Base):
    source_event_id: Mapped[str | None] = mapped_column(String(64), unique=True)
~~~

迁移必须先回填状态发生时间，再添加 source_event_id 和唯一索引：

~~~python
revision = "20260719_0004"
down_revision = "20260719_0003"

def upgrade() -> None:
    op.execute("ALTER TABLE monitor_runs ADD COLUMN state_occurred_at DATETIME(3) NULL AFTER finished_at")
    op.execute("""
        UPDATE monitor_runs
        SET state_occurred_at = COALESCE(finished_at, started_at, scheduled_at, updated_at)
        WHERE state_occurred_at IS NULL
    """)
    op.execute("ALTER TABLE monitor_events ADD COLUMN source_event_id VARCHAR(64) NULL AFTER id")
    op.execute("CREATE UNIQUE INDEX uq_monitor_events_source_event_id ON monitor_events (source_event_id)")
~~~

- [ ] **步骤 4：验证。**

运行：python -m pytest -p no:cacheprovider tests/test_monitor_event_service.py tests/test_monitor_migration.py tests/test_development_environment_contract.py -q

预期：通过，迁移依赖 20260719_0003，且不包含真实密钥。

- [ ] **步骤 5：请求提交前确认。**

展示本任务 diff 与测试结果。未经用户明确授权，不暂存、不提交。

### Task 2: 原子处理 Prefect Flow Run 事件

**文件：**

- 修改：backend/services/mysql_monitor_store.py
- 修改：backend/services/monitor_event_service.py
- 创建：backend/services/prefect_monitor_event_service.py
- 修改：backend/services/prefect_monitor_adapter.py
- 修改：tests/test_monitor_event_service.py
- 修改：tests/test_prefect_monitor_adapter.py

**接口：**

- 输入：Prefect Event，包含 id、occurred、event、resource 与 related。
- 输出：ProcessedPrefectEvent(accepted, duplicate, run)。
- 存储：apply_prefect_event(event_id, occurred_at, normalized_run, message) -> tuple[run, changed]。

- [ ] **步骤 1：写幂等和乱序失败测试。**

~~~python
def test_processor_writes_one_event_for_duplicate_prefect_event() -> None:
    processor = PrefectMonitorEventProcessor(store=MemoryMonitorStore(), fetch_flow_run=lambda _: REPORT_FLOW_RUN)
    first = processor.process(COMPLETED_EVENT)
    duplicate = processor.process(COMPLETED_EVENT)

    assert first.accepted is True
    assert duplicate.duplicate is True
    assert len(processor.store.events[first.run["id"]]) == 1


def test_older_running_event_cannot_replace_completed_state() -> None:
    processor = PrefectMonitorEventProcessor(store=MemoryMonitorStore(), fetch_flow_run=lambda _: REPORT_FLOW_RUN)
    processor.process(COMPLETED_EVENT)
    result = processor.process(OLDER_RUNNING_EVENT)

    assert result.accepted is False
    assert processor.store.find_by_external_id("prefect", "run-1")["status"] == "succeeded"
~~~

- [ ] **步骤 2：确认失败。**

运行：python -m pytest -p no:cacheprovider tests/test_monitor_event_service.py tests/test_prefect_monitor_adapter.py -q

预期：失败，因为 PrefectMonitorEventProcessor 尚未定义。

- [ ] **步骤 3：实现事件处理器。**

处理器仅接受事件名以 prefect.flow-run. 开头的事件，并从 Prefect 资源 ID 中提取 Flow Run ID：

~~~python
def _flow_run_id(event: Mapping[str, Any]) -> str | None:
    resource_id = str((event.get("resource") or {}).get("prefect.resource.id") or "")
    prefix = "prefect.flow-run."
    return resource_id.removeprefix(prefix) if resource_id.startswith(prefix) else None
~~~

process 必须校验 id、occurred、Flow Run ID；首次事件调用 GET /flow_runs/{id} 并复用 PrefectMonitorAdapter 的 STATE_MAP、_target_identity 和 deployments/filter、flows/filter 补全身份。若结果不是 report，返回 ProcessedPrefectEvent(accepted=False, duplicate=False, run=None)。

MySQL 存储方法必须在一个 Session 内按 source_event_id 查重，比较 MonitorRun.state_occurred_at，仅当 received occurred_at 更新时才更新 run、插入 MonitorEvent，并只提交一次。重复和过期事件返回原运行记录与 changed=False。

- [ ] **步骤 4：让 MemoryMonitorStore 与 MySQL 语义一致。**

为内存存储增加 source_event_ids 集合和按运行 ID 保存的 state_occurred_at；重复事件不追加日志，过期事件可标记已见但不得更新 run。

- [ ] **步骤 5：验证。**

运行：python -m pytest -p no:cacheprovider tests/test_monitor_event_service.py tests/test_prefect_monitor_adapter.py -q

预期：通过；相同事件只记录一次，Completed 不被旧 Running 覆盖，session-keeper 与 Dashboard 事件不产生可见通报记录。

- [ ] **步骤 6：请求提交前确认。**

展示本任务 diff 与测试结果。未经用户明确授权，不暂存、不提交。

### Task 3: 使用提交后 WebSocket 增量广播

**文件：**

- 创建：backend/services/monitor_snapshot_service.py
- 创建：backend/services/monitor_stream.py
- 修改：backend/routers/monitor.py
- 修改：backend/app.py
- 修改：tests/test_monitor_router.py
- 创建：tests/test_monitor_stream.py

**接口：**

- build_monitor_snapshot(runs, now=None) -> dict：保留当前 snapshot REST 契约。
- build_run_update(runs, run_id, now=None) -> dict：返回 type=run.updated、runId、run、pendingQueue、summary、updatedAt、connected。
- MonitorStreamHub.connect、disconnect、publish_from_async、publish_from_thread。
- MonitorRealtimeStatus.record_accepted、record_reconciled、record_error、as_dict；生命周期内只有这一份状态实例。

- [ ] **步骤 1：写初始快照和增量消息失败测试。**

~~~python
def test_websocket_sends_snapshot_then_one_incremental_update(client, hub) -> None:
    with client.websocket_connect("/api/monitor/stream") as socket:
        assert socket.receive_json()["type"] == "snapshot"
        hub.publish_from_async("recent-report")
        update = socket.receive_json()

    assert update["type"] == "run.updated"
    assert update["runId"] == "recent-report"
    assert update["run"]["targetId"].startswith("report-")


def test_scheduled_report_is_not_a_history_row_but_updates_queue() -> None:
    update = build_run_update(SCHEDULED_REPORT_RUNS, "scheduled-report", now=NOW)
    assert update["run"] is None
    assert update["pendingQueue"]["total"] == 1
~~~

- [ ] **步骤 2：确认失败。**

运行：python -m pytest -p no:cacheprovider tests/test_monitor_router.py tests/test_monitor_stream.py -q

预期：失败，因为 Hub 和 build_run_update 尚不存在，旧 WebSocket 仍按 5 秒循环发送完整快照。

- [ ] **步骤 3：抽取快照和增量构建器。**

将 report_history_runs 与 _snapshot_payload 移到 monitor_snapshot_service.py。增量必须用同一批 runs 计算汇总与队列：

~~~python
def build_run_update(runs: list[dict[str, Any]], run_id: str, *, now: datetime | None = None) -> dict[str, Any]:
    snapshot = build_monitor_snapshot(runs, now=now)
    changed = next((run for run in snapshot["runs"] if run["id"] == run_id), None)
    return {
        "type": "run.updated",
        "runId": run_id,
        "run": changed,
        "pendingQueue": snapshot["pendingQueue"],
        "summary": snapshot["summary"],
        "updatedAt": snapshot["updatedAt"],
        "connected": True,
    }
~~~

- [ ] **步骤 4：实现 Hub 并移除 5 秒轮询。**

Hub 与 `MonitorRealtimeStatus` 在 lifespan 中各创建一份并绑定 asyncio.get_running_loop()。Webhook 在 MySQL 提交并确认 `accepted=True` 后调用 `status.record_accepted(occurred_at)`；对账完成后调用 `status.record_reconciled(now)`；受控异常调用 `status.record_error(error_category)`。同步线程入口必须使用 run_coroutine_threadsafe：

~~~python
def publish_from_thread(self, run_id: str) -> None:
    if self.loop is not None:
        asyncio.run_coroutine_threadsafe(self.publish_from_async(run_id), self.loop)
~~~

monitor_stream 必须 accept、注册、发送一条 snapshot 后等待 WebSocketDisconnect；删除 asyncio.sleep(5) 循环。send_json 失败的连接从 Hub 移除。Webhook 与对账均只能在数据库成功写入后调用发布方法。

- [ ] **步骤 5：验证。**

运行：python -m pytest -p no:cacheprovider tests/test_monitor_router.py tests/test_monitor_stream.py tests/test_monitor_sync_service.py -q

预期：通过；每个连接只有一条初始快照，显式发布后只收到一条 run.updated。

- [ ] **步骤 6：请求提交前确认。**

展示本任务 diff 与测试结果。未经用户明确授权，不暂存、不提交。

### Task 4: 提供认证 Webhook 和五分钟对账

**文件：**

- 修改：backend/routers/monitor.py
- 修改：backend/services/monitor_sync_service.py
- 修改：backend/services/prefect_monitor_adapter.py
- 修改：backend/services/health_service.py
- 修改：backend/app.py
- 修改：tests/test_monitor_router.py
- 修改：tests/test_monitor_sync_service.py
- 修改：tests/test_health_service.py

**接口：**

- POST /api/monitor/events/prefect 使用 X-Prefect-Monitor-Secret。
- 新事件：202 {"accepted": true, "duplicate": false}。
- 重复或过期事件：202 {"accepted": false, "duplicate": true}。
- 密钥错误：401；事件格式错误：422。
- MonitorSyncLoop 默认 300 秒，并将实际变更的运行 ID 交给 Hub。

- [ ] **步骤 1：写认证和频率失败测试。**

~~~python
def test_prefect_webhook_rejects_invalid_secret(client) -> None:
    response = client.post("/api/monitor/events/prefect", json=COMPLETED_EVENT)
    assert response.status_code == 401


def test_prefect_webhook_publishes_only_after_accepted_processing(client, hub) -> None:
    response = client.post(
        "/api/monitor/events/prefect",
        json=COMPLETED_EVENT,
        headers={"X-Prefect-Monitor-Secret": "test-secret"},
    )
    assert response.status_code == 202
    assert response.json() == {"accepted": True, "duplicate": False}
    assert hub.published_run_ids == ["mon_report_run_1"]


def test_monitor_sync_loop_defaults_to_five_minutes() -> None:
    assert MonitorSyncLoop(sync=lambda: [])._interval_seconds == 300
~~~

- [ ] **步骤 2：确认失败。**

运行：python -m pytest -p no:cacheprovider tests/test_monitor_router.py tests/test_monitor_sync_service.py tests/test_health_service.py -q

预期：失败，端点不存在且同步默认值还是 15 秒。

- [ ] **步骤 3：实现认证端点。**

~~~python
@router.post("/events/prefect", status_code=202)
async def receive_prefect_event(
    payload: dict[str, Any],
    x_prefect_monitor_secret: Annotated[str | None, Header()] = None,
) -> dict[str, bool]:
    expected = os.environ.get("PREFECT_MONITOR_WEBHOOK_SECRET", "")
    if not expected or not x_prefect_monitor_secret or not secrets.compare_digest(expected, x_prefect_monitor_secret):
        raise HTTPException(status_code=401, detail="监控事件认证失败")
    try:
        result = await run_in_threadpool(processor.process, payload)
    except InvalidPrefectMonitorEvent as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if result.accepted and result.run:
        await hub.publish_from_async(result.run["id"])
    return {"accepted": result.accepted, "duplicate": result.duplicate}
~~~

端点不能记录请求头。`backend.app.live` 从应用状态中的唯一 `MonitorRealtimeStatus` 返回 `monitorEvents`，其中必须有 `lastAcceptedAt`、`lastReconciledAt`、`lastErrorCategory`；应用刚启动且尚未发生对应动作时这些值为 `null`。

- [ ] **步骤 4：把同步改为补漏。**

sync_prefect_monitor 返回本轮真实变化的运行记录。MonitorSyncLoop 默认等待 300 秒，并用 on_runs_changed 回调逐个交给 publish_from_thread。适配器只查询当前时间前 10 分钟的重叠窗口；每日使用最近 30 天窗口检查历史和长期 Scheduled 项。

- [ ] **步骤 5：验证。**

运行：python -m pytest -p no:cacheprovider tests/test_monitor_router.py tests/test_monitor_sync_service.py tests/test_health_service.py -q

预期：通过；认证失败为 401，重复事件不广播，新事件提交后广播，默认对账为 300 秒。

- [ ] **步骤 6：请求提交前确认。**

展示本任务 diff 与测试结果。未经用户明确授权，不暂存、不提交。

### Task 5: 前端局部合并与 Prefect Automation 验收

**文件：**

- 修改：frontend/src/monitor/types.ts
- 修改：frontend/src/monitor/apiMonitorService.ts
- 修改：frontend/src/monitor/MonitorCenter.tsx
- 修改：frontend/src/monitor/MonitorCenter.test.tsx
- 创建：frontend/src/monitor/apiMonitorService.test.ts
- 创建：docs/operations/prefect-monitor-webhook.md

**接口：**

- MonitorStreamMessage 是 snapshot 与 run.updated 的联合类型。
- run.updated.run 为 MonitorRun | null；null 表示从 30 天历史列表移除，但仍更新 pendingQueue 和 summary。
- 前端不因 run.updated 重新调用 getSnapshot，不关闭用户已打开的抽屉。

- [ ] **步骤 1：写局部合并失败测试。**

~~~tsx
it("merges run.updated without requesting a second snapshot", async () => {
  const service = createControlledMonitorService(SNAPSHOT);
  render(<MonitorCenter service={service} />);
  await screen.findByText("通报 · 日常进度");

  service.emit({
    type: "run.updated",
    runId: "run-2",
    run: { ...UPDATED_RUN, status: "succeeded" },
    pendingQueue: EMPTY_QUEUE,
    summary: { succeeded: 2, running: 0, failed: 0, scheduled: 0 },
    updatedAt: "2026-07-19T09:00:01+08:00",
    connected: true,
  });

  expect(await screen.findByRole("button", { name: "成功 2" })).toBeTruthy();
  expect(service.getSnapshot).toHaveBeenCalledTimes(1);
});
~~~

- [ ] **步骤 2：确认失败。**

运行：npm run test --prefix frontend -- MonitorCenter.test.tsx apiMonitorService.test.ts --run

预期：失败，当前 API 服务只将完整 snapshot 交给组件。

- [ ] **步骤 3：实现联合消息与局部合并。**

~~~ts
export interface MonitorRunUpdatedMessage {
  type: "run.updated";
  runId: string;
  run: MonitorRun | null;
  pendingQueue: PendingQueue;
  summary: MonitorSummary;
  updatedAt: string;
  connected: boolean;
}

export type MonitorStreamMessage =
  | ({ type: "snapshot" } & MonitorSnapshot)
  | MonitorRunUpdatedMessage;
~~~

收到 run.updated 时，按 runId 替换或插入 run；run 为 null 时移除该 ID。无论 run 是否为空，都更新待执行队列、最近更新时间和连接状态。保留选中的记录和所有打开的抽屉状态。

- [ ] **步骤 4：编写 Prefect Automation 运维说明。**

文档要求在 Prefect UI 或官方 Automation API 创建 Flow Run 状态变化触发器，过滤 Flow 为 auto-notify-flow、Deployment 名称前缀为 notify-。状态覆盖 Scheduled、Running、Completed、Failed、Crashed、Cancelled。动作调用 https://<受控后端地址>/api/monitor/events/prefect，携带 X-Prefect-Monitor-Secret: <secret>。密钥只从本机配置读取，绝不能记录在文档或截图中。

- [ ] **步骤 5：验证前端。**

运行：

~~~powershell
npm run test --prefix frontend -- MonitorCenter.test.tsx apiMonitorService.test.ts --run
npm run typecheck --prefix frontend
npm run build --prefix frontend
~~~

预期：全部通过，run.updated 不触发第二次快照请求。

- [ ] **步骤 6：做端到端验收。**

启动现有后端与 5175 前端，配置测试 Automation，然后制造通报 Flow Run 的 Scheduled、Running、Completed 状态。确认页面只更新对应记录、待执行数量在 Scheduled -> Running 时下降、Dashboard 与 session-keeper 不出现。记录至少 20 次状态变化的页面可见耗时，计算 P95；仅 P95 不超过 5 秒时通过。

- [ ] **步骤 7：请求提交前确认。**

展示最终 diff、测试结果与不含密钥的验收数据。未经用户明确授权，不暂存、不提交。

### Task 6: 最终核验

**文件：**

- 修改：docs/operations/prefect-monitor-webhook.md（仅补充不含密钥的验收结果）

- [ ] **步骤 1：运行后端完整监控测试集。**

运行：python -m pytest -p no:cacheprovider tests/test_monitor_event_service.py tests/test_prefect_monitor_adapter.py tests/test_monitor_router.py tests/test_monitor_stream.py tests/test_monitor_sync_service.py tests/test_monitor_migration.py tests/test_monitor_api_schema.py tests/test_health_service.py -q

预期：全部通过。

- [ ] **步骤 2：执行静态与运行检查。**

运行：

~~~powershell
git diff --check
Invoke-RestMethod http://127.0.0.1:8000/api/live | ConvertTo-Json -Depth 5
~~~

预期：没有空白错误；live 的 ok 为 true，monitorEvents 包含最后事件接收时间、最后对账时间，且没有错误类别。

- [ ] **步骤 3：请求交付确认。**

说明已验证的延迟、未覆盖的外部网络风险和当前单 FastAPI 进程边界；未经用户明确授权，不暂存、不提交。
