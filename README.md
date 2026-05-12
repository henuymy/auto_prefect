# 自动化通报 Prefect 项目

这是一个基于 Prefect 的自动化通报项目，当前主流程聚焦 **日通报**：

```text
自动登录/复用 Cookie
→ 下载报表
→ 比对报表和正式模板
→ same：等待重试或结束
→ invalid：失败停止
→ changed：生成临时模板
→ 企业微信发送图片/文本
→ 发送成功后提交正式模板
```

## 当前保留的配置

当前只保留日通报这一套业务配置：

```text
config/reports/日通报.json
config/tasks/日通报.json
```

部署入口也只保留一个：

```text
auto-notify-flow/notify-daily
```

对应文件：

```text
prefect.yaml
flows/notify_single_flow.py
```

## 目录结构

```text
.
├── admin/                    # Streamlit 配置管理页面
├── config/
│   ├── modules/              # 通用模块配置
│   ├── reports/              # 报表业务配置，目前保留日通报
│   └── tasks/                # Flow 任务配置，目前保留日通报
├── flows/                    # Prefect flow 编排
├── services/                 # 业务逻辑
├── tasks/                    # Prefect task 包装
├── scripts/                  # Prefect 启停脚本
├── runtime/                  # 运行产物，不提交
├── tests/                    # 单元测试
├── CONFIG_REFERENCE.md       # 配置字段说明
└── SSO_EXPORT_FLOW.md        # SSO 到导出链路说明
```

## 环境准备

推荐使用 Conda 环境：

```powershell
conda create -n auto-notify python=3.11 -y
conda activate auto-notify
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple --trusted-host pypi.tuna.tsinghua.edu.cn
```

如果你想“一次配好，后面直接跑”，推荐直接执行：

```powershell
pwsh -File scripts\setup_windows_env.ps1
```

这个脚本会：

```text
创建或复用 auto-notify conda 环境
配置 conda 清华镜像
安装 requirements.txt 依赖
创建 runtime 目录
检查 Edge / Excel COM
把 autologin.json 的 login_command 固定到当前 conda 环境
执行一次基础 smoke test
```

常用参数：

```powershell
pwsh -File scripts\setup_windows_env.ps1 -EnvName auto-notify
pwsh -File scripts\setup_windows_env.ps1 -SkipCondaCreate
pwsh -File scripts\setup_windows_env.ps1 -SkipSmokeTest
pwsh -File scripts\setup_windows_env.ps1 -SkipCondaMirror
```

默认会使用清华 `pip` 镜像：

```text
https://pypi.tuna.tsinghua.edu.cn/simple
```

如果你想覆盖成别的源，也可以：

```powershell
pwsh -File scripts\setup_windows_env.ps1 -PipIndexUrl "https://mirrors.aliyun.com/pypi/simple/" -PipTrustedHost "mirrors.aliyun.com"
```

`setup_windows_env.ps1` 默认也会把 `conda` 切到清华镜像。

如果你想手动执行，命令如下：

```powershell
conda config --add channels https://mirrors.tuna.tsinghua.edu.cn/anaconda/pkgs/main
conda config --add channels https://mirrors.tuna.tsinghua.edu.cn/anaconda/pkgs/free
conda config --add channels https://mirrors.tuna.tsinghua.edu.cn/anaconda/pkgs/r
conda config --add channels https://mirrors.tuna.tsinghua.edu.cn/anaconda/cloud/conda-forge
conda config --set show_channel_urls yes
```

如果使用 Excel COM、Selenium、企业微信截图发送等能力，请确保：

```text
Microsoft Excel 已安装
Microsoft Edge 已安装
Edge WebDriver 可用
```

## Prefect 数据库

Prefect 用到的数据库是它自己的元数据库，只存这些内容：

```text
deployment
flow run / task run 状态
worker 心跳
调度记录
日志元数据
```

它不会改你的业务报表数据，但如果你本地调试也一直连远程 PostgreSQL，测试 run 和日志会一起写进去，库会变脏。

因此建议分两种模式：

1. 本地调试：SQLite，本机隔离，不污染远程调度库
2. 正式调度：PostgreSQL，稳定支撑 server + scheduler + worker

注意：

```text
SQLite 不适合长期调度。
Prefect Server + Scheduler + Worker 会并发写元数据库，Windows 本地 SQLite 容易出现 database is locked。
```

正式调度请使用 PostgreSQL。

当前已验证可用的 PostgreSQL 连接格式：

```text
postgresql+asyncpg://user_rEkhna:password_YSDPae@60.205.108.31:5432/prefect
```

说明：

```text
host: 60.205.108.31
port: 5432
database: prefect
user: user_rEkhna
driver: asyncpg
```

## 启动 Prefect 服务

有两种启动方式，本质上是同一套环境：

1. 快捷启动
   - 用 `scripts\prefect_start.ps1`
   - 一条命令自动拉起 `server + worker`
   - 适合日常使用
2. 手动启动
   - 先用 `scripts\prefect_env_debug.ps1` 或 `scripts\prefect_env_prod.ps1` 写入环境变量
   - 再自己执行 `python -m prefect ...`
   - 适合排查问题、手工控制每一步

建议：

```text
平时正式启动服务：优先用 prefect_start.ps1
需要排查问题：再用 prefect_env_*.ps1 + python -m prefect ...
```

### 1. 本地调试模式

适合：

```text
本地点 UI 看配置
手动试跑
不想把测试记录写进远程 PostgreSQL
```

先停止旧服务：

```powershell
pwsh -File scripts\prefect_stop.ps1
```

再启动本地调试：

```powershell
pwsh -File scripts\prefect_start.ps1 -Mode both -Detached -UseSqliteDebug
```

说明：

```text
本地调试模式使用 runtime/prefect_home/prefect.db
会禁用 Prefect 后台 services，适合手动测试，不适合长期调度
```

### 2. 正式调度模式

适合：

```text
真正定时跑日通报
需要 scheduler + worker
需要长期稳定运行
```

先停止旧服务和残留锁文件：

```powershell
pwsh -File scripts\prefect_stop.ps1
```

启动 Server + Worker：

```powershell
pwsh -File scripts\prefect_start.ps1 -Mode both -Detached
```

启动后打开：

```text
http://127.0.0.1:4200
```

说明：

```text
scripts\prefect_start.ps1 默认已经写了 PostgreSQL 连接串
所以通常不需要再手动传 -DatabaseUrl
只有以后数据库地址、账号或库名变了，才需要显式覆盖
```

## 不用脚本时，如何手动启动

如果你想自己开终端、逐条执行命令，可以按下面做。

### 1. 本地调试模式（SQLite）

适合：

```text
本地临时调试
手动点 UI
不想污染远程 PostgreSQL 元数据
```

终端 1：启动 Server

```powershell
conda activate auto-notify
. .\scripts\prefect_env_debug.ps1
python -m prefect server start --no-services --workers 1
```

终端 2：启动 Worker

```powershell
conda activate auto-notify
. .\scripts\prefect_env_debug.ps1
python -m prefect worker start --pool default-agent-pool --type process
```

### 2. 正式调度模式（PostgreSQL）

适合：

```text
要启用 scheduler
要长期稳定调度
要避免 SQLite 锁库
```

终端 1：启动 Server

```powershell
conda activate auto-notify
. .\scripts\prefect_env_prod.ps1
python -m prefect server start --workers 1
```

终端 2：启动 Worker

```powershell
conda activate auto-notify
. .\scripts\prefect_env_prod.ps1
python -m prefect worker start --pool default-agent-pool --type process
```

启动后打开：

```text
http://127.0.0.1:4200
```

## 部署 Flow

服务启动后，优先使用“单条部署”：

```powershell
$env:PREFECT_API_URL = "http://127.0.0.1:4200/api"
$env:PREFECT_API_DATABASE_CONNECTION_URL = "postgresql+asyncpg://user_rEkhna:password_YSDPae@60.205.108.31:5432/prefect"
$env:PREFECT_SERVER_DATABASE_CONNECTION_URL = $env:PREFECT_API_DATABASE_CONNECTION_URL
$env:PYTHONUTF8 = "1"
C:\Users\yuyu\.conda\envs\auto-notify\python.exe -m prefect deploy --name notify-daily
```

部署成功后，在 Web UI 的 Deployments 中运行：

```text
auto-notify-flow/notify-daily
```

如果不用脚本，也可以直接在终端手动部署：

```powershell
conda activate auto-notify
. .\scripts\prefect_env_prod.ps1
$env:PYTHONUTF8 = "1"
python -m prefect deploy --name notify-daily
```

如果你当前启动的是本地 SQLite 调试模式，把上面的数据库连接串替换成：

```powershell
. .\scripts\prefect_env_debug.ps1
$env:PYTHONUTF8 = "1"
python -m prefect deploy --name notify-daily
```

如果后面 `prefect.yaml` 又加回多个 deployment，再使用：

```powershell
python -m prefect deploy --all
```

## Streamlit 配置页面

启动配置管理页面：

```powershell
streamlit run admin/app.py
```

页面可维护：

```text
下载 URL
Cookie Stage
请求 data JSON
比对参数
发送 items
等待重试 wait_for_change
```

## NiceGUI 配置页面

新版配置页面与 `admin/app.py` 完全隔离，适合用卡片和弹窗维护复杂下载配置：

```powershell
python -m admin_nicegui.app
```

启动后打开：

```text
http://127.0.0.1:8080
```

说明：

```text
admin/app.py          旧 Streamlit 后台，继续保留
admin_nicegui/app.py  新 NiceGUI 后台，独立实现，不引用 admin/app.py
```

NiceGUI 下载项可以混用多种响应处理方式：

```text
file                    接口直接返回 Excel/文件
json_to_excel           JSON 响应转 Excel
json_drilldown_to_excel 地市作战级联下钻后转 Excel
```

## Runtime 运行产物

`runtime/` 是每次运行过程中生成的中间产物和结果文件目录，默认不需要手动编辑。

常见目录和文件：

```text
runtime/cookies/cookie_dump.json
```

保存自动登录后抓取到的 Cookie。下载报表时会根据配置里的 `Cookie Stage` 从这里取对应 Cookie，例如：

```text
report_analysis
smart_ops
data_market
```

```text
runtime/report_downloader/downloads/
runtime/report_downloader/download_manifest.json
```

保存下载下来的原始报表文件，以及本次下载清单。`download_manifest.json` 会记录每个报表的下载路径、URL、状态码、文件大小等信息。

```text
runtime/flow/<报表名称>/
```

保存某个报表 flow 的本次运行产物。例如 `爱家V网` 会生成：

```text
runtime/flow/爱家V网/
```

常见文件：

```text
runtime/flow/<报表名称>/compare_config.json
runtime/flow/<报表名称>/template_updater_config.json
runtime/flow/<报表名称>/update_manifest.json
runtime/flow/<报表名称>/wecom/send_result.json
runtime/flow/<报表名称>/commit_manifest.json
```

说明：

```text
compare_config.json             Flow 临时生成的比对配置
template_updater_config.json    Flow 临时生成的模板更新配置
update_manifest.json            模板更新结果，记录 updated/skipped、更新了哪些 sheet、新模板路径
wecom/send_result.json          企业微信发送结果
commit_manifest.json            正式模板提交结果，提交成功后才会生成或更新
```

更新后的临时模板会保存在：

```text
runtime/flow/<报表名称>/templates/
```

例如：

```text
runtime/flow/爱家V网/templates/爱家亲情网报表_updated_20260427_205924.xlsx
```

如果流程最后一步 `commit_template` 失败，但 `update_manifest.json` 已经是 `status: updated`，说明：

```text
下载成功
比对成功
临时模板已生成
正式模板还没有被覆盖提交
```

这时通常可以根据 `update_manifest.json` 里的 `output_path` 找到新模板，确认后再补跑提交模板。

如果企业微信已经发送成功，但提交模板失败，不建议直接完整重跑，避免重复发送。可以只补跑提交模板：

```powershell
python -c "from services.commit_service import commit_template; commit_template({'update_manifest_path':'runtime/flow/爱家V网/update_manifest.json','send_result_path':'runtime/flow/爱家V网/wecom/send_result.json','backup_dir':'runtime/flow/爱家V网/backups','manifest_path':'runtime/flow/爱家V网/commit_manifest.json','overwrite':True})"
```

注意：提交正式模板时，如果 `templates/<模板文件>.xlsx` 正被 Excel/WPS 打开，Windows 会拒绝覆盖，可能出现：

```text
PermissionError: [Errno 13] Permission denied
```

关闭占用模板的 Excel/WPS 后，再补跑提交即可。

## 动态占位符

下载请求 `data` 支持动态占位符：

```text
${today}              当前日期，YYYY-MM-DD
${yesterday}          前一天日期，YYYY-MM-DD
${yesterday_yyyymmdd} 前一天日期，YYYYMMDD
${hour}               当前小时，0-23
${hour2}              当前小时，00-23
```

示例：

```json
{
  "queryDate": "${yesterday}",
  "versionName": "${yesterday_yyyymmdd}"
}
```

如果不写占位符，参数会原样发送。

## 单条命令运行

不走 Prefect Server/Worker，直接跑日通报：

```powershell
C:\Users\yuyu\.conda\envs\auto-notify\python.exe -c "from flows.notify_single_flow import auto_notify_flow; print(auto_notify_flow('config/tasks/日通报.json'))"
```

这个方式适合排查业务问题，但不提供 UI 调度能力。

## 相关文档

```text
CONFIG_REFERENCE.md   配置字段说明
SSO_EXPORT_FLOW.md    SSO 登录到报表导出的链路说明
ARCHITECTURE.md       分层设计约束
```
