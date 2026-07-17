# 云电脑生产、本地开发迁移设计

## 目标

将云电脑设为唯一生产运行节点，本地仅用于持续开发和测试。两端必须使用隔离的
Prefect PostgreSQL、驾驶舱 MySQL、运行目录和凭据，避免重复调度、重复通知和数据竞争。

生产 Prefect 保留当前的 Deployment、调度和运行历史；驾驶舱数据库不保留历史数据。
现有数据库服务器继续承载所有数据库，云电脑负责运行 Prefect Server、三个 Worker、
后端和前端。

## 固定架构

| 边界 | 云电脑（生产） | 本地（开发） |
| --- | --- | --- |
| Git 版本 | 固定发布标签或指定提交 | 日常开发分支 |
| Prefect PostgreSQL | `prefect_prod` | `prefect_dev` |
| 驾驶舱 MySQL | `dashboard_v2_prod` | `dashboard_v2_dev` |
| Prefect API | 云电脑本机 `http://127.0.0.1:4200/api` | 本机 `http://127.0.0.1:4200/api` |
| 运行目录 | 云电脑本地 `C:\AutoNotifyRuntime` | 本机本地 `C:\AutoNotifyRuntime` |
| 企业微信、登录和文档凭据 | 仅生产凭据 | 开发凭据，或禁用真实发送 |

两个 Prefect 环境是不同数据库，因此可使用相同的三 Pool 名称：
`windows-session-pool`、`windows-dashboard-pool`、`windows-notify-pool`。
不得让两台机器连接同一个 Prefect 数据库或同一个运行目录。

## 前置条件

1. 记录准备发布到生产的 Git 提交并创建发布标签，例如 `prod-2026-07-17-001`。云电脑只检出
   此标签或明确的提交，不跟随本地开发分支的后续提交。
2. 确认云电脑可访问现有数据库服务器的 PostgreSQL `5432` 和 MySQL `3306`；生产数据库账号
   仅允许云电脑的 IP 访问。本地开发账号不得拥有生产库权限。
3. 确认源 Prefect 库、云电脑和本地使用同一锁定版本的 Prefect 依赖。
4. 在切换窗口前处理所有运行中的 Flow：等待其完成，或由运维人员明确取消。不得在存在未知
   运行中业务任务时执行数据库快照。
5. 凭据文件只通过受控渠道交付云电脑，绝不提交 Git，也不复制浏览器 Cookie、Edge Profile 或
   `C:\AutoNotifyRuntime\session`。本地不得配置生产企业微信 Webhook、生产接收人、生产登录
   凭据或生产腾讯文档凭据；如没有隔离开发凭据，本地不得启动 Notify Worker 或执行可能发送通知的 Flow。

## 数据库建库

以下 SQL 由数据库管理员在现有数据库服务器执行。尖括号中的值必须替换为实际的受控账号和密码。

### PostgreSQL

```sql
CREATE ROLE auto_notify_prod LOGIN PASSWORD '<生产数据库密码>';
CREATE DATABASE prefect_prod OWNER auto_notify_prod;

CREATE ROLE auto_notify_dev LOGIN PASSWORD '<开发数据库密码>';
CREATE DATABASE prefect_dev OWNER auto_notify_dev;
```

### MySQL

```sql
CREATE DATABASE dashboard_v2_prod CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER 'auto_notify_prod'@'<云电脑IP>' IDENTIFIED BY '<生产数据库密码>';
GRANT ALL PRIVILEGES ON dashboard_v2_prod.* TO 'auto_notify_prod'@'<云电脑IP>';

CREATE DATABASE dashboard_v2_dev CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER 'auto_notify_dev'@'<本机IP或网段>' IDENTIFIED BY '<开发数据库密码>';
GRANT ALL PRIVILEGES ON dashboard_v2_dev.* TO 'auto_notify_dev'@'<本机IP或网段>';
FLUSH PRIVILEGES;
```

生产 PostgreSQL 仅恢复 Prefect 源库。数据库管理员必须先确认 PostgreSQL 实例已安装
`pg_trgm`；生产库的扩展随源库恢复，开发库则由首次 Prefect 数据库迁移创建或由数据库管理员
预先启用。驾驶舱生产库保持空库，后续由 Alembic 创建结构。

## Prefect 备份与恢复

在源运行节点停止后，使用具有只读导出权限的账号执行以下命令。导出文件应保存到加密存储，
校验成功后再恢复。恢复前，数据库管理员必须确认目标主机和数据库名正确，且 `prefect_prod`
为本次新建的空库；绝不可将恢复命令指向源库或已有生产库。目标是新建空库，因此恢复命令
不使用 `--clean`，并在遇到首个错误时停止。

```powershell
# 从当前 Prefect 源库导出
pg_dump -Fc --no-owner --no-acl -h <数据库服务器> -U <源库用户> -d <当前Prefect库> -f prefect-prod.dump

# 恢复到新建的生产库
pg_restore --exit-on-error --no-owner --no-acl -h <数据库服务器> -U auto_notify_prod -d prefect_prod prefect-prod.dump
```

恢复后，云电脑使用生产连接执行：

```powershell
. .\scripts\lib\runtime_config.ps1
Import-ProjectRuntimeConfig | Out-Null
python -m prefect server database upgrade -y
alembic -c alembic_dashboard_v2.ini upgrade head
```

前一条命令升级恢复后的 Prefect 元数据库；后一条命令初始化空的生产驾驶舱数据库。
由于 Prefect 数据已恢复，切换时不得执行 `prefect deploy --all`，以免无意覆盖当前服务端
Deployment 配置。

## 两端配置

### 云电脑：`config/runtime.local.json`

```json
{
  "prefect": {
    "postgres": {
      "url": "postgresql+asyncpg://auto_notify_prod:<URL编码后的密码>@<数据库服务器>:5432/prefect_prod"
    },
    "api_url": "http://127.0.0.1:4200/api"
  },
  "dashboard": {
    "mysql": {
      "host": "<数据库服务器>",
      "port": 3306,
      "database": "dashboard_v2_prod",
      "user": "auto_notify_prod",
      "password": "<生产数据库密码>"
    }
  },
  "runtime": {
    "root": "C:\\AutoNotifyRuntime",
    "work_pools": {
      "session": {"name": "windows-session-pool", "limit": 1},
      "dashboard": {"name": "windows-dashboard-pool", "limit": 4},
      "notify": {"name": "windows-notify-pool", "limit": 6}
    }
  }
}
```

同时在云电脑按需创建被 Git 忽略的 `config/modules/login_config.local.json`、
`config/modules/wecom_sender.local.json` 和 `config/modules/tencent_docs.local.json`。它们使用
生产凭据。

### 本地：`config/runtime.local.json`

本地配置与云电脑结构相同，但 Prefect URL 必须指向 `prefect_dev`，驾驶舱必须指向
`dashboard_v2_dev`，并使用开发凭据。不得复制生产 `runtime.local.json` 或生产模块凭据到本机。
本地 `wecom_sender.local.json` 不得包含生产 Webhook 或接收人；没有隔离凭据时，不得通过
`scripts/run.ps1` 启动完整运行栈或触发 Notify Flow。

## 切换清单

1. 在云电脑检出已记录的发布标签，安装锁定依赖；创建生产 `runtime.local.json` 和必要的模块凭据。
2. 在云电脑执行 `pwsh -File scripts/setup_windows_env.ps1`，但尚不执行 `scripts/run.ps1`。
3. 在维护窗口确认源机器没有运行中的 Flow，执行 `pwsh -File scripts/stop.ps1`，并确认旧机器的
   Prefect Server、三个 Worker、后端和前端均已停止。
4. 导出源 Prefect 库，恢复至 `prefect_prod`；执行 Prefect 和驾驶舱数据库迁移。
5. 在云电脑执行 `pwsh -File scripts/run.ps1`，然后执行 `pwsh -File scripts/status.ps1`。
6. 在云电脑 Prefect UI 验证：现有 Deployment 和调度存在、三个 Worker 在线、首次 Session Keeper
   Run 被提交一次、没有历史过期 Run 被补跑。
7. 验证生产驾驶舱使用空库成功初始化；仅在受控范围内验证一条不会发送真实消息的业务链路。
8. 将本地 `runtime.local.json` 切换到开发数据库，核验本地模块凭据不含任何生产 Webhook、
   接收人或业务账号，并撤销本机到生产 PostgreSQL/MySQL 的网络和账号权限。

## 回滚边界

在云电脑启动前，回滚只需保留源库不变、恢复源机器配置并重新启动源机器。

云电脑启动并产生新的 Prefect Run 后，两个数据库会开始分叉，不能直接同时或交替启动两端。
此时回滚必须先停止云电脑，再从 `prefect_prod` 制作新备份并恢复到指定回退环境；恢复期间仍只允许
一个生产运行节点。`dashboard_v2_prod` 是本次切换新建的空库，不承诺保留其切换后的数据；回滚时
可丢弃该库，并由恢复后的生产环境重新采集驾驶舱数据。

## 验收标准

- 云电脑的 `scripts/status.ps1` 显示 Prefect API、PostgreSQL、MySQL、三个 Pool 和三个 Worker 正常。
- Prefect UI 中的 Deployment 数量、名称和调度与切换前快照一致。
- 旧本地机器没有生产 Server、Worker 或生产数据库连接。
- 云电脑和本地分别只能访问其对应的数据库与本机运行目录。
- 本地开发运行不会出现在生产 Prefect UI，也不会写入生产驾驶舱库；本地模块配置不含生产通知或
  业务凭据，且在无隔离凭据时不会启动 Notify Worker。
