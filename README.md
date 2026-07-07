# 自动化通报 Prefect 项目

这是一个基于 Prefect 的自动化通报项目，当前主流程支持多套通报配置，例如 **爱家V网**、**爱家亲情网每日盯控**、**爱家亲情网营业厅每日盯控**：

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

## 当前配置

业务配置主要分两类：

```text
config/reports/*.json   # 报表业务配置，React 配置中心主要编辑这里
config/tasks/*.json     # Prefect flow 入参配置，发布/测试时由后端生成或更新
```

当前已有示例配置：

```text
config/reports/爱家V网.json
config/reports/爱家亲情网每日盯控.json
config/reports/爱家亲情网营业厅每日盯控.json
```

主执行入口：

```text
flows/notify_single_flow.py
```

## 目录结构

```text
.
├── admin/                    # Streamlit 配置管理页面
├── admin_react/              # React 配置中心前端
├── backend/                  # FastAPI 配置中心后端
├── config/
│   ├── modules/              # 通用模块配置
│   ├── reports/              # 报表业务配置，React 配置中心正式保存到这里
│   └── tasks/                # Flow 任务配置，发布到调度后写入这里
├── flows/                    # Prefect flow 编排
├── services/                 # 业务逻辑
├── tasks/                    # Prefect task 包装
├── scripts/                  # Prefect 启停脚本
├── runtime/                  # 运行产物，不提交
├── tests/                    # 单元测试
├── CONFIG_REFERENCE.md       # 配置字段说明
└── SSO_EXPORT_FLOW.md        # SSO 到导出链路说明
```

## React 配置中心

React 配置中心用于可视化编辑 `config/reports/*.json`，并通过后端完成草稿保存、正式保存、Runtime 浏览清理、配置校验、安全测试、真实试跑和发布到 Prefect 调度。

启动前先安装前端依赖：

```powershell
cd admin_react
npm install
cd ..
```

一键启动后端和前端：

```powershell
pwsh -File scripts\admin_react_start.ps1 -Mode both
```

也可以分开启动：

```powershell
python -m uvicorn backend.app:app --reload --port 8000
```

```powershell
cd admin_react
npm run dev
```

访问地址：

```text
http://127.0.0.1:5173
```

按钮含义：

```text
保存草稿   -> 写入 runtime/drafts/，不影响正式配置
保存配置   -> 写入 config/reports/*.json，成为正式配置
校验配置   -> 后端按当前 schema 和执行层要求检查必要字段
安全测试   -> 使用当前页面配置生成 dry-run 任务，不真实下载、不发送、不提交模板
真实试跑   -> 使用当前页面配置真实下载、比对、截图并发送企业微信，但不提交正式模板
发布到调度 -> 写入 config/tasks/*.json，并执行 prefect deploy 发布 deployment
查看运行日志 -> 查看保存、校验、测试、发布、清理 runtime 等后台操作记录
```

注意：

```text
发布到调度前，需要 Prefect Server 可访问，并且 Worker 所在 work pool 为 default-agent-pool。
Runtime 文件管理会保护 runtime/prefect_home、runtime/cookies、runtime/locks，避免误删关键运行数据。
真实试跑会真实发送企业微信，请确认 webhook 和发送内容无误后再执行。
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
真正定时跑自动通报配置
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

## 发布到 Prefect 调度

推荐直接在 React 配置中心点击：

```text
发布到调度
```

后端会先保存当前配置，再生成对应任务配置：

```text
config/tasks/<配置名称>.json
```

然后执行类似命令：

```powershell
python -m prefect deploy flows/notify_single_flow.py:auto_notify_flow --name notify-<配置名称> --pool default-agent-pool --param config_path=config/tasks/<配置名称>.json
```

如果配置里启用了定时部署，并填写了 Cron：

```json
{
  "deployment": {
    "enabled": true,
    "cron": "0 9-18 * * *",
    "timezone": "Asia/Shanghai"
  }
}
```

发布时会把 Cron 和时区一起传给 Prefect。

如果你想手动发布，也可以先写入 Prefect 环境变量：

```powershell
conda activate auto-notify
. .\scripts\prefect_env_prod.ps1
$env:PYTHONUTF8 = "1"
python -m prefect deploy flows/notify_single_flow.py:auto_notify_flow --name notify-爱家V网 --pool default-agent-pool --param config_path=config/tasks/爱家V网.json
```

说明：

```text
prefect.yaml 仍可作为兼容入口，但当前推荐以 React 配置中心发布为主。
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

NiceGUI 是曾经尝试过的独立后台页面，当前不作为主线维护。日常建议使用 React 配置中心。

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

## 新版报表配置结构

React 配置中心保存的是 `config/reports/*.json`，核心结构如下：

```json
{
  "name": "爱家V网",
  "template_path": "templates/爱家亲情网报表.xlsx",
  "downloads": [],
  "compare_sources": [],
  "send": {
    "webhook_url": "",
    "workbook_name": "爱家V网",
    "items": []
  },
  "template_update": {
    "update_condition": "any_changed",
    "write_sheets": "all_compared",
    "send_when_same": true
  },
  "wait_for_change": {
    "enabled": false,
    "poll_interval_seconds": 300,
    "max_wait_minutes": 180
  },
  "deployment": {
    "enabled": false,
    "cron": "",
    "timezone": "Asia/Shanghai"
  }
}
```

注意：

```text
新配置只使用 downloads[]，不再使用旧字段 download。
新配置只使用 compare_sources[]，不再使用旧字段 compare。
新配置不再保存 env。
新配置不再使用 csrf_headers_from_cookies。
```

每个下载项必须包含：

```text
name
stage
method
url
body_type
response_mode
```

`response_mode` 支持：

```text
file                    接口直接返回 Excel/文件
json_to_excel           JSON 响应转 Excel
json_drilldown_to_excel 地市作战级联下钻后转 Excel
```

`body_type` 支持：

```text
form  application/x-www-form-urlencoded，使用 data= 发送
json  application/json，使用 json= 发送
raw   原始字符串，使用 data=raw_body 发送
```

## 下载认证规则

下载服务统一从 `runtime/cookies/cookie_dump.json` 读取 Cookie 和 Storage。配置里的 `stage` 决定使用哪一套登录会话。

常用 stage：

```text
report_analysis  报表分析系统
smart_ops        智慧运营平台
city_ops         地市作战平台
data_market      数据超市
```

### 报表分析系统

报表分析 Excel 下载一般使用：

```json
{
  "stage": "report_analysis",
  "method": "POST",
  "body_type": "form",
  "response_mode": "file",
  "headers": {
    "Content-Type": "application/x-www-form-urlencoded"
  },
  "headers_from_cookies": {
    "Ssr-token": "ssr-token"
  }
}
```

说明：

```text
Cookie 不建议写死到配置里。
程序会自动把 report_analysis stage 下的 Cookie 拼到 requests.Session。
headers_from_cookies 会从 Cookie 中取 ssr-token，并生成请求头 Ssr-token。
```

如果接口返回 `HTTP 302` 且 Location 是登录/SSO/UAC 地址，下载服务会认为 session 已过期，flow 会强制重新登录后自动重试一次。

### 智慧运营平台

智慧运营常用：

```json
{
  "stage": "smart_ops",
  "body_type": "json",
  "headers_from_session_storage": {
    "User-Info": "zhyyptInfo.accessToken"
  }
}
```

含义：

```text
从 smart_ops 的 sessionStorage.zhyyptInfo.accessToken 取值，写入请求头 User-Info。
```

### 地市作战平台

地市作战常用：

```json
{
  "stage": "city_ops",
  "body_type": "json",
  "headers_from_session_storage": {
    "uapToken": "uapToken"
  }
}
```

含义：

```text
从 city_ops 的 sessionStorage.uapToken 取值，写入请求头 uapToken。
这个值通常本身就是一整段 Cookie 风格字符串，按浏览器原样保存和发送。
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
city_ops
data_market
```

```text
runtime/report_downloader/downloads/
runtime/report_downloader/download_manifest.json
```

保存下载下来的原始报表文件，以及本次下载清单。`download_manifest.json` 会记录每个报表的下载路径、URL、状态码、文件大小等信息。

```text
runtime/drafts/
```

保存 React 配置中心的草稿配置、测试运行临时 report 配置和 task 配置。草稿不会影响正式 `config/reports/*.json`。

```text
runtime/logs/admin_react_runs.jsonl
```

保存 React 配置中心的后台操作记录，例如保存草稿、保存配置、校验配置、安全测试、真实试跑、发布到调度、Runtime 清理等。

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

下载请求 `url`、`headers`、`data`、`raw_body` 支持动态占位符：

```text
${today}              当前日期，YYYY-MM-DD
${today_yyyymmdd}     当前日期，YYYYMMDD
${yesterday}          前一天日期，YYYY-MM-DD
${yesterday_yyyymmdd} 前一天日期，YYYYMMDD
${day_before_yesterday}          前天日期，YYYY-MM-DD
${day_before_yesterday_yyyymmdd} 前天日期，YYYYMMDD
${hour}               当前小时，0-23
${hour2}              当前小时，00-23
${session_storage:xxx} 从当前 stage 的 sessionStorage 读取字段
${local_storage:xxx}   从当前 stage 的 localStorage 读取字段
```

示例：

```json
{
  "queryDate": "${yesterday}",
  "versionName": "${yesterday_yyyymmdd}",
  "beforeYesterday": "${day_before_yesterday_yyyymmdd}"
}
```

如果不写占位符，参数会原样发送。

## 单条命令运行

不走 Prefect Server/Worker，直接跑某个配置：

```powershell
C:\Users\yuyu\.conda\envs\auto-notify\python.exe -c "from flows.notify_single_flow import auto_notify_flow; print(auto_notify_flow('config/tasks/爱家V网.json'))"
```

这个方式适合排查业务问题，但不提供 UI 调度能力。

## 相关文档

```text
CONFIG_REFERENCE.md   配置字段说明
SSO_EXPORT_FLOW.md    SSO 登录到报表导出的链路说明
ARCHITECTURE.md       分层设计约束
```
