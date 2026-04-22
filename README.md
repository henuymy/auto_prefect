# 自动化通报 Prefect 项目

这是一个 Prefect 风格的自动化通报项目。当前阶段先完成标准目录整理，现有可运行脚本暂时放在 `legacy_modules/`，后续再逐步迁移到 `services/`。

## 目录结构

```text
.
├── pyproject.toml
├── prefect.yaml
├── README.md
├── ARCHITECTURE.md
├── requirements.txt
├── config/
│   ├── app_settings.yaml
│   ├── login_profiles.yaml
│   ├── method_registry.json
│   ├── modules/
│   │   ├── autologin.json
│   │   ├── excel_sender_wx.json
│   │   ├── report_compare.json
│   │   ├── report_downloader.json
│   │   ├── template_updater.json
│   │   └── template_commit.json
│   └── tasks/
│       ├── example.json
│       └── local.json
├── flows/
│   ├── notify_single_flow.py
│   ├── notify_multi_method_flow.py
│   ├── manual_rerun_flow.py
│   └── __init__.py
├── tasks/
├── services/
├── models/
├── infrastructure/
├── utils/
├── deployments/
├── runtime/
├── tests/
├── docs/
└── legacy_modules/
```

## 分层原则

```text
flows         只负责编排和分支
tasks         只做 Prefect task 包装
services      放业务逻辑，不依赖 Prefect
models        放输入输出数据结构
infrastructure 放 HTTP、Excel、企业微信、Gotify 等底层客户端
utils         放通用工具
runtime       放运行产物，不提交
legacy_modules 临时保留旧脚本，后续迁移
```

详细约束见 `ARCHITECTURE.md`。

## 环境配置

**创建 Conda 环境（推荐）**

```bash
conda create -n auto-notify python=3.11 -y
conda activate auto-notify
```

**安装依赖**

```bash
pip install -r requirements.txt pillow -i https://pypi.tuna.tsinghua.edu.cn/simple
```

## 使用 Prefect Web UI 运行

**第一步：启动 Prefect Server（终端1，保持运行）**

```bash
"C:\Users\yuyu\.conda\envs\auto-notify\python.exe" -m prefect server start
```

**第二步：启动 Worker（终端2，保持运行）**

```bash
set PREFECT_API_URL=http://127.0.0.1:4200/api
"C:\Users\yuyu\.conda\envs\auto-notify\python.exe" -m prefect worker start --pool default-agent-pool
```

**第三步：部署 Flow（只需执行一次）**

```bash
cd "C:\Users\yuyu\Desktop\项目\自动通报"
set PREFECT_API_URL=http://127.0.0.1:4200/api
"C:\Users\yuyu\.conda\envs\auto-notify\python.exe" -m prefect deploy --all
```

**第四步：在 Web UI 中运行**

打开浏览器访问 http://127.0.0.1:4200/

在 Deployments 页面可以看到：
- `auto-notify-flow/notify-single` — 普通登录模式
- `auto-notify-force-login-flow/notify-single-force-login` — 强制重新登录模式（推荐）

点击对应 deployment，然后点 **Quick Run** 即可触发运行。


##streamlit Web UI 
streamlit run admin/app.py



## 命令行直接运行

本地真实配置文件：

```text
config/tasks/local.json
```

如果不存在，会回退读取：

```text
config/tasks/example.json
```

运行：

```powershell
python flows\notify_single_flow.py
```

指定某个通报配置运行：

```powershell
python -c "from flows.notify_single_flow import auto_notify_flow; auto_notify_flow('config/tasks/vnet_daily.json')"
```

后续多个通报建议按“一个通报一个配置”新增：

```text
config/tasks/vnet_daily.json
config/tasks/market_daily.json
config/tasks/zhengqi_daily.json
```

当前 Flow 仍调用 `legacy_modules/` 中的旧脚本，顺序为：

```text
可选登录
→ 下载报表
→ 比对报表和正式模板
→ same：结束
→ invalid：失败停止
→ changed：生成临时模板
→ 自动生成本次发送配置
→ 发送企业微信
→ 发送成功后提交正式模板
```

## 后续迁移方向

1. `autologin/session` 已迁移出会话复用入口到 `services/session_manager.py`，旧 Selenium 登录脚本暂时作为刷新 Cookie 的后备入口。
2. `report-downloader` 已迁移到 `services/method_service.py`，旧脚本暂时保留备用。
3. `report-compare` 已迁移到 `services/compare_service.py`，旧脚本暂时保留备用。
4. `template-updater` 已迁移到 `services/template_service.py`，旧脚本暂时保留备用。
5. `excel-sender-wx` 已拆到 `services/screenshot_service.py` 和 `services/notify_service.py`，旧脚本暂时保留备用。
6. `template-commit` 已迁移到 `services/commit_service.py`，旧脚本暂时保留备用。
