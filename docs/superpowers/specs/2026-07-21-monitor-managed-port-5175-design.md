# 监控界面受托管端口 5175 设计

**目标：** 运行栈启动时，将监控界面作为受托管 Web 服务启动在 5175 端口。

**架构：** 现有 Web 生命周期增加第三个 Vite 进程 `web-monitor`。`scripts/run.ps1` 将 `MonitorPort` 参数传给 `scripts/lib/start_web.ps1`；后者执行 `npm run monitor` 并登记该进程。停止和状态脚本使用相同的端口及进程名。Vite 的 `monitor` 模式在 `/` 提供 `monitor.html`，同时保留 `/monitor.html` 访问路径。

**范围：**

- 配置中心继续使用 5173，数据驾驶舱继续使用 5174。
- 新增默认端口为 5175 的 `monitor` npm 脚本。
- 在生命周期脚本中新增 `MonitorPort = 5175`，并在强制重启时透传。
- 与现有受托管 Web 进程一起启动、停止、登记并报告 `web-monitor`。
- 更新环境契约测试和 README 启动说明。

**不在范围内：**

- 不修改后端端口、API 代理、FRP 映射或部署拓扑。
- 不修改监控 API 行为或界面组件。

**验证：**

1. 环境契约测试覆盖监控命令、生命周期登记、默认端口和状态检查 URL。
2. 前端类型检查验证 Vite 配置。
3. 相关 Python 测试模块验证生命周期脚本契约。
