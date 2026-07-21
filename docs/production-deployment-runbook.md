# 生产部署问题与处置手册

本文汇总本项目部署到生产环境时已经遇到的典型问题、原因和处置原则。生产凭据、Webhook、Cookie 和数据库连接信息只保存在被 Git 忽略的本地配置中，不应写入本文或提交到版本库。

## 上线前检查

1. 为生产机器创建 `config/runtime.local.json`，确认其中使用生产 PostgreSQL、MySQL、Prefect API、运行目录和三个 Work Pool。
2. 确认 `config/runtime.local.json`、`config/modules/*.local.json`、`config/tasks/*.local.json` 仍被 Git 忽略。它们可能包含数据库密码、访问令牌、Webhook 和 Cookie。
3. 安装锁定的 Python 依赖与前端 npm 依赖，并确认 Python 基础自检成功。
4. 确认驾驶舱 V2 数据库迁移和未来日期分区均已就绪，再启动 Worker。
5. 发布或更新 Deployment 前先记录远端 Deployment 清单，尤其是动态 Notify Deployment。

## 常见问题

| 现象 | 原因 | 处置 |
| --- | --- | --- |
| Python 导入 `prefect`、`asyncpg` 或 `pymysql` 失败 | 当前解释器未安装项目锁定依赖 | 使用可访问的 PyPI 源安装 `requirements-dev.lock`，并确认基础自检输出 `smoke_ok`。 |
| 包下载返回 `HTTP 403` | 配置的 PyPI 镜像拒绝下载 | 使用官方 PyPI 或企业内可访问的镜像；不要把镜像故障误判为项目依赖错误。 |
| 配置中心或驾驶舱无法打开 | 前端依赖未安装，5173 或 5174 端口没有监听 | 安装 `frontend` 的锁定 npm 依赖后重启 Web 服务；再检查两个端口。 |
| `/api/health` 返回 503 | MySQL 可连接，但驾驶舱 V2 结构或日分区未就绪 | 读取健康检查 JSON 的失败项；完成 V2 迁移和分区维护后再验收。 |
| Worker 加载 Flow 时出现旧目录 `WinError 3` | 远端 Deployment 保存了以前机器或 Worktree 的工作目录 | 在生产目录重新发布对应 Deployment，确认新的代码加载路径不再指向旧目录。 |
| `prefect deploy` 读取中文 YAML 失败 | Windows 默认 GBK 无法正确读取包含中文的 `prefect.yaml` | 以 UTF-8 模式执行发布，并在发布前确认已加载生产 Prefect API 配置。 |
| 启动时显示 `blocker`，Worker 未启动 | Prefect 中还有 `RUNNING`、`CANCELLING` 或 `PAUSED` 的受管 Run | 先确认任务是否仍真实运行；等待其结束或在 Prefect UI 取消已残留的 Run。只有确认可中断时才允许强制重启。 |
| 启动时取消大量 `SCHEDULED/PENDING` Run | 启动保护清理了已过期的排队任务 | 这是避免积压任务集中补跑的机制；上线前评估是否有必须保留的任务，不能把运行中的 Run 当作过期队列处理。 |
| 分区维护提示缺少 `DASHBOARD_MYSQL_*` | 直接运行维护 Flow 时未加载本机运行配置 | 在启动维护前加载 `runtime.local.json` 对应环境变量，避免把配置缺失误判为 MySQL 故障。 |
| 分区维护报 MySQL `1205 Lock wait timeout exceeded` | 驾驶舱采集或其他事务占用了 `collection_run` 的行锁 | 优先等待采集结束后重试；反复发生时，在确认没有必须保留的业务 Run 后停止 Worker、执行维护、再重新启动。不要直接终止未知数据库连接或仅调大超时。 |

## Deployment 管理原则

- `prefect deploy --all` 只会创建或更新 `prefect.yaml` 中声明的 Deployment。
- 删除远端 Deployment 前必须导出或记录清单。未写入 `prefect.yaml` 的动态 Notify Deployment 被删除后不会自动恢复。
- 生产目录、机器账户或 Worktree 变化后，应重新发布受影响的 Deployment，避免 Worker 使用已失效的保存路径。
- `scripts/run.ps1` 负责启动和队列保护，不负责自动发布或删除 Deployment。

## 推荐上线顺序

1. 准备并核对生产本地配置，确保凭据不进入 Git。
2. 安装 Python 和前端依赖，完成基础自检。
3. 运行数据库迁移与驾驶舱分区维护，确认健康检查通过。
4. 检查远端 Deployment 和活跃 Flow Run，处理旧路径、残留 Run 与不应恢复的动态 Deployment。
5. 在生产目录发布当前声明的 Deployment。
6. 启动 Prefect Server、三个 Worker、后端、配置中心和驾驶舱。
7. 使用 Prefect UI、`/api/live`、`/api/health` 以及 5173/5174 端口完成验收。

## 启动与恢复命令

在项目根目录执行标准启动：

```powershell
pwsh -File scripts/run.ps1
```

查看进程、端口、数据库和 Work Pool 状态：

```powershell
pwsh -File scripts/status.ps1
```

停止本项目已登记的服务：

```powershell
pwsh -File scripts/stop.ps1
```

仅当 Prefect 中的 `RUNNING`、`CANCELLING` 或 `PAUSED` Run 已确认可以中断时，才使用强制重启：

```powershell
pwsh -File scripts/run.ps1 -ForceRestart
```

`-ForceRestart` 会将本项目受管的运行中 Run 标记为取消，再启动替代 Worker。它用于清理已确认残留的 Run，不应用于绕过仍在实际执行的业务任务，也不能解决旧 Notify Pool 中保留排队 Run 导致的启动拒绝。

## 生产安全边界

- 禁止提交 `*.local.json`、Cookie、证书、数据库密码、Webhook 或访问令牌；不要使用 `git add -f` 绕过忽略规则。
- 不要在未核对清单的情况下批量删除远端 Deployment。
- 不要在未知任务是否真实运行时使用强制重启或中断数据库连接。
- 数据库结构变更必须通过 Alembic 迁移；分区维护只能在确认目标数据库无误后执行。
