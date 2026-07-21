# 监控界面受托管端口 5175 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**目标：** 将监控页面作为 `web-monitor` 受托管进程启动在 `127.0.0.1:5175`。

**架构：** Vite 增加独立的 `monitor` mode；`run.ps1` 将 `MonitorPort` 传给 Web 启动脚本，后者登记 `web-monitor`。停止和状态脚本引用相同进程名和端口，5173 与 5174 保持不变。

**技术栈：** PowerShell、npm/Vite、Vitest、pytest。

## 全局约束

- 默认监控入口为 `http://127.0.0.1:5175/`，`/monitor.html` 保持可用。
- 不修改后端端口、API 代理、FRP 映射或监控 API。
- `web-monitor` 必须使用现有进程登记机制。

---

### 任务 1：建立端口与进程契约

**文件：**
- 修改：`tests/test_development_environment_contract.py`

**接口：**
- 消费：前端 npm 脚本和 Web 生命周期 PowerShell 文件。
- 产出：5175、`MonitorPort`、`web-monitor` 的失败回归测试。

- [ ] **步骤 1：添加失败断言**

在现有前端端口契约测试中增加：

```python
assert '"monitor": "vite --mode monitor --host 127.0.0.1 --port 5175"' in package
assert "[int]$MonitorPort = 5175" in start_web
assert "npm run monitor -- --host 127.0.0.1 --port $MonitorPort" in start_web
assert '"web-monitor"' in stop
assert '"web-monitor"' in status
assert "http://127.0.0.1:$MonitorPort" in status
```

并断言 `scripts/run.ps1` 声明并向 `stop.ps1`、`start_web.ps1` 透传 `MonitorPort`。

- [ ] **步骤 2：确认测试为红**

```powershell
python -m pytest -p no:cacheprovider tests/test_development_environment_contract.py -q
```

预期：失败，提示缺少监控脚本、端口参数或受托管进程名。

### 任务 2：实现受托管监控入口

**文件：**
- 修改：`frontend/package.json`
- 修改：`frontend/vite.config.ts`
- 修改：`scripts/run.ps1`
- 修改：`scripts/lib/start_web.ps1`
- 修改：`scripts/stop.ps1`
- 修改：`scripts/status.ps1`
- 修改：`README.md`

**接口：**
- 消费：`MonitorPort = 5175`。
- 产出：`npm run monitor` 与 `web-monitor`。

- [ ] **步骤 1：增加 monitor npm 脚本和根路由**

在 `frontend/package.json` 增加：

```json
"monitor": "vite --mode monitor --host 127.0.0.1 --port 5175"
```

在 `frontend/vite.config.ts` 引入 `isMonitor = mode === "monitor"`；仅在 monitor mode 将 `/` 改写为 `/monitor.html`，并拒绝配置中心和驾驶舱 HTML 入口。

- [ ] **步骤 2：纳入生命周期脚本**

在 `start_web.ps1` 增加 `monitor` mode、`MonitorPort`、`Start-Monitor` 和：

```powershell
Start-ManagedWebProcess -Name "web-monitor" `
    -Command $monitorCommand `
    -RegisteredCommand "npm run monitor -- --host 127.0.0.1 --port $MonitorPort"
```

在 `run.ps1` 声明并透传 `MonitorPort`，包括 `-ForceRestart`。在 `stop.ps1` 停止 `web-monitor`；在 `status.ps1` 显示其进程、HTTP 状态和端口。

- [ ] **步骤 3：更新 README**

将手动启动 5175 的说明替换为：`scripts/run.ps1` 自动托管监控服务，访问 `http://127.0.0.1:5175/`；`/monitor.html` 为兼容路径；不新增 FRP 映射。

- [ ] **步骤 4：确认契约测试为绿**

```powershell
python -m pytest -p no:cacheprovider tests/test_development_environment_contract.py -q
```

预期：通过。

### 任务 3：验证与提交

**文件：**
- 修改：任务 1 和任务 2 的文件。

**接口：**
- 消费：`npm run monitor` 与 `scripts/run.ps1 -MonitorPort 5175`。
- 产出：可由运行栈启动、停止和查询的监控入口。

- [ ] **步骤 1：运行前端验证**

```powershell
npm run typecheck --prefix frontend
npm run build --prefix frontend
```

预期：两个命令均以状态码 0 结束。

- [ ] **步骤 2：检查变更**

```powershell
git diff --check
git status --short
```

预期：没有空白错误，且不包含运行时文件或依赖目录。

- [ ] **步骤 3：提交实现**

```powershell
git add frontend/package.json frontend/vite.config.ts scripts/run.ps1 scripts/lib/start_web.ps1 scripts/stop.ps1 scripts/status.ps1 README.md tests/test_development_environment_contract.py
git commit -m "feat: manage monitor frontend on port 5175"
```

预期：单个实现提交。

## 自检

- 每项规格要求均映射到具体文件和验证步骤。
- 5173、5174、后端和 FRP 均不在修改范围内。
- 未使用占位内容；端口、进程名和命令在计划中一致。
