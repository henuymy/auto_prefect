# Prefect 单 Windows 三 Work Pool 部署设计

## 目标

在一台专用 Windows 主机上稳定运行自动通报、驾驶舱和共享会话维护能力。系统使用一个长期保持登录的专用 Windows 账号，由人工执行统一脚本启动，不配置 Windows 任务计划程序或开机自动启动。

部署需要支持最多六个通报 Flow 同时运行或等待，同时保证 Session Keeper 不被业务任务阻塞，并保证 Excel COM 和自动登录始终串行执行。

## 部署边界

- 一台 Windows 主机或 Windows 虚拟机。
- 一套 Prefect Server 和一套 PostgreSQL 元数据库。
- 三个 Prefect Process Worker，分别连接三个 Work Pool。
- Microsoft Edge 和桌面版 Microsoft Excel 安装在同一主机。
- 三个 Worker 运行在同一个专用 Windows 用户的交互式桌面会话中。
- 不在本阶段拆分 Linux 控制节点或增加第二台 Windows Worker。
- 主机重启或用户重新登录后，由运维人员重新执行启动脚本。

## Prefect 拓扑

| Work Pool | Worker 并发上限 | 运行内容 |
| --- | ---: | --- |
| `windows-session-pool` | 1 | Session Keeper |
| `windows-dashboard-pool` | 4 | 驾驶舱采集、同步和维护 Flow |
| `windows-notify-pool` | 6 | 完整通报 Flow |

所有 Work Pool 共用同一 Prefect Server。Work Pool 只隔离执行容量，不创建额外控制面或数据库。

通报并发上限为六。第七个及后续通报 Flow 保留在 Prefect 队列中，直到已有 Flow 结束并释放槽位。处于等待、下载、发送或等待 Excel 锁状态的 Flow 均占用一个通报并发槽。

## Deployment 分配

- `session-keeper` 部署到 `windows-session-pool`。
- 驾驶舱采集、指标同步和分区维护 Deployment 部署到 `windows-dashboard-pool`。
- 所有通报 Deployment 部署到 `windows-notify-pool`。

Session Keeper 使用 `*/15 * * * *` 和 `Asia/Shanghai` 时区。系统启动完成后主动触发一次 Session Keeper，不等待下一个十五分钟整刻。

## 统一运行数据目录

源代码与运行时共享数据分离。所有 Worker 使用固定的绝对运行目录：

```text
C:\AutoNotifyRuntime\
├── locks\
│   ├── login.lock
│   └── excel_com.lock
├── session\
│   ├── cookies.json
│   └── session_state.json
├── browser\
│   └── edge-profile\
├── prefect\
│   └── prefect_home\
├── logs\
└── temp\
```

固定目录防止代码升级、Git 分支切换或 Worktree 产生多套 Cookie、锁文件和 Edge Profile。任务配置、报表模板和最终业务输出仍按项目现有配置管理。

运行目录仅允许专用 Windows 用户和管理员访问。Cookie、浏览器 Profile、日志和临时文件不得提交 Git。

## Session 管理

Session Keeper、驾驶舱 Flow 和通报 Flow 共用 Session Manager、Cookie、Edge Profile、会话状态和登录锁。

Session Keeper 每十五分钟执行三阶段探活。业务 Flow 启动时读取 `session_state.json`：

- 状态为健康、Cookie 摘要一致且验证时间不超过三分钟时，直接复用会话。
- 状态超过三分钟时，执行轻量探活，不直接登录。
- 出现明确认证失败时，申请全局登录锁。
- 网络、VPN、DNS、TLS、连接或请求超时不触发登录。

获得登录锁后必须重新读取 Cookie 并再次探活。若其他 Flow 已完成刷新，当前 Flow 直接复用；否则执行登录。第一次登录失败后等待六十秒，仅重试一次。累计两次失败后停止本次 Flow 并告警。

登录成功后必须重新验证 `report_analysis`、`smart_ops` 和 `city_ops`。全部通过后，使用临时文件和原子替换发布 Cookie 与会话状态。

每个业务 Flow 最多主动刷新一次。刷新后仅重试失败的业务步骤一次，不能重跑已完成步骤或进入无限刷新循环。

## Excel 并发控制

最多六个通报 Flow 可以并行执行下载、等待、接口调用和发送，但所有 Excel COM 操作必须获得 `excel_com.lock`，同一时刻仅允许一个 Flow 操作 Excel。

锁文件记录主机、PID、进程启动时间、唯一持有者标识、获锁时间和当前阶段。只有确认持有进程不存在时才能清理遗留锁，不能单纯按文件年龄删除。

Flow 等待 Excel 锁期间继续占用通报并发槽。系统记录锁等待时间；如果长期出现全部六个槽都在等待 Excel，应调整任务时间分布或优化 Excel 阶段，而不是继续提高并发上限。

## 手动启动流程

`scripts/run.ps1` 是唯一启动入口，按以下顺序执行：

1. 加载并校验本机运行配置。
2. 验证 Prefect PostgreSQL、业务 MySQL、运行目录和必要端口。
3. 启动 Prefect Server并等待健康检查通过。
4. 暂停本项目管理的 Deployment，防止队列清理期间产生新 Run。
5. 检查并处理未完成的 Flow Run。
6. 创建或检查三个 Process Work Pool。
7. 同步 `prefect.yaml` 中的 Deployment。
8. 恢复本次启动临时暂停的 Deployment。
9. 启动三个 Worker，并应用 `1 / 4 / 6` 并发上限。
10. 主动触发一次 Session Keeper。
11. 启动管理端。

启动脚本必须可重复执行。已经健康运行的 Server、Worker 和管理端不能被重复启动。若存在无法安全接管的运行中任务，启动流程停止并提示人工处理。

`scripts/status.ps1` 是统一状态入口，`scripts/stop.ps1` 是统一停止入口。停止脚本仅停止本项目拥有的进程，不按进程名称批量终止无关 Python、Edge 或 Excel 进程。

## 启动队列清理

启动脚本在 Worker 启动前处理停机期间形成的积压：

- Session Keeper 的所有过期 `SCHEDULED` 或 `PENDING` Run 均取消，启动后重新触发一次检查。
- 高频驾驶舱采集的过期 Run 均取消，只保留未来调度。
- 日累计、月累计和维护任务取消重复积压，按业务定义最多保留一个仍有执行价值的 Run。
- 定时通报的预计开始时间超过当前时间十分钟时取消，并记录“因过期跳过”。
- 十分钟内的定时通报保留并允许执行。
- 手工触发的通报不应用十分钟过期规则。
- 已经进入 `RUNNING`、`CANCELLING` 或 `PAUSED` 的任务不自动删除。存在此类任务时拒绝启动替代 Worker，并提示人工处理。
- 已从 `prefect.yaml` 移除但仍存在于 Prefect 的旧 Deployment 先暂停并报告，不在启动过程中直接删除。

该策略避免主机恢复后集中补发过期通报，也避免 Session Keeper 和驾驶舱高频采集补跑无意义的历史批次。

## 故障处理

- 认证失败：进入全局锁内刷新，最多登录两次，间隔六十秒。
- 网络或基础设施故障：不触发登录，由 Prefect 标记失败并告警。
- Excel COM 异常：清理当前 Flow 创建的 Excel 实例，仅重试当前 Excel 步骤一次。
- Worker 意外退出：Worker 监督进程等待三十秒后重启对应 Worker。
- Prefect Server 退出：不继续启动新任务，由运维人员重新执行 `run.ps1`。
- 数据库连接失败、磁盘空间不足或锁等待超时：停止相关任务并告警，不进行无限重试。
- 单个 Flow 失败不得影响其他 Work Pool 的正常执行。

## 状态与告警

`status.ps1` 至少展示：

- Prefect Server/API、PostgreSQL 和 MySQL 状态。
- 三个 Work Pool、在线 Worker、并发上限、运行数和排队数。
- 最近一次 Session Keeper 结果、Cookie 更新时间和会话新鲜度。
- 登录锁和 Excel 锁的持有者、持有时间与等待情况。
- 超过十分钟的异常排队任务。
- 管理端端口和磁盘剩余空间。

告警使用现有企业微信机器人。相同故障周期仅发送一次失败告警，持续失败不重复刷屏；恢复后发送一次恢复通知并清除抑制状态。

日志、Flow 参数、状态文件和告警不得包含用户名、密码、Cookie、Token、Storage 原值或企业微信 Webhook。

## 配置

`config/runtime.local.json` 是本机运行配置的唯一入口，至少包含：

- Prefect PostgreSQL URL 和 API 地址。
- 三个 Work Pool 名称和 `1 / 4 / 6` 并发上限。
- `C:\AutoNotifyRuntime` 路径。
- MySQL连接配置。
- 管理端端口。
- 定时通报过期阈值 `600` 秒。
- Session 状态新鲜度 `180` 秒。
- 登录锁和 Excel 锁等待超时。

账号、密码、OTP访问配置和 Webhook 等秘密保存在被 Git 忽略的本机配置中，不进入 `prefect.yaml` 或 Deployment 参数。

## 验收标准

- `run.ps1` 能完整启动一套 Prefect Server、三个 Worker 和管理端。
- 重复执行启动脚本不会产生重复进程。
- Session Keeper 不受六个通报 Flow 占满 Notify Pool 的影响。
- 同时提交七个通报时，六个运行，第七个在 Prefect 队列等待。
- 多个 Flow 同时进入 Excel 阶段时，只有一个操作 Excel。
- 多个 Flow 同时发现 Session 失效时，只发生一次登录。
- 超过十分钟的定时通报在启动时取消，手工通报不受影响。
- 过期 Session Keeper 和高频驾驶舱任务不补跑。
- 网络故障不会触发反复登录。
- `status.ps1` 能显示三个 Pool、Worker、锁、积压和磁盘状态。
- `stop.ps1` 能停止本项目运行栈且不误杀无关进程。
- 日志、状态、Prefect 参数和告警中不出现认证秘密。

## 上线顺序

1. 建立并限制 `C:\AutoNotifyRuntime` 的访问权限。
2. 将 Cookie、Edge Profile、锁、日志和临时目录迁移到统一运行目录。
3. 创建三个 Work Pool 并调整 Deployment 分配。
4. 扩展启动、状态和停止脚本。
5. 加入启动队列清理和过期通报取消规则。
6. 在隔离的 Prefect 测试数据库中完成自动化和受控验收。
7. 首次上线时将 Notify 并发设为二，验证 Session、Excel 锁、队列和停止流程。
8. 验收通过后将 Notify 并发调整为最终值六。

## 非目标

- 不配置 Windows 任务计划程序或开机自动启动。
- 不提供跨主机 Session、Cookie、Edge Profile 或文件锁共享。
- 不增加第二套 Prefect Server。
- 不取消 Excel COM 的全局串行约束。
- 不在本方案中拆分现有完整通报 Flow。
