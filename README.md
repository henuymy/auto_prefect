# 自动化通报项目

本项目当前包含自动化通报系统设计文档，以及一个用于登录内网系统并获取目标域 `JSESSIONID` 的 Selenium 原型。

## 当前目录

```text
autologin/
├── SKILL.md              # Skill 入口说明
├── agents/
│   └── openai.yaml       # Skill UI 元数据
├── references/
│   └── config.example.json
├── scripts/
│   ├── autologin.py      # 登录命令行入口
│   ├── login_flow.py     # Selenium 登录流程
│   ├── cookie_recorder.py # 登录过程中写入三阶段 Cookie JSON
│   ├── otp_provider_gotify.py
│   └── requirements.txt
├── config.json           # 本地真实配置，已忽略
└── runtime/              # 本地运行产物，已忽略
auto_notify/
├── app.py                # 主应用入口
└── handlers/
    └── login_handler.py  # 登录模块适配层
```

## 运行登录原型

1. 进入 `autologin` 目录。
2. 参考 `references/config.example.json` 创建 `config.json`，填写真实账号、密码和系统地址。
3. 安装依赖。
4. 执行登录脚本。

```powershell
cd autologin
pip install -r scripts/requirements.txt
python scripts/autologin.py
```

脚本会打开 Edge 浏览器，登录后把目标域的 `JSESSIONID` 保存到 `autologin/runtime/jsessionid.txt`，并在同一次登录链路中把三阶段 Cookie 写入 `autologin/runtime/cookie_dump.json`。

也可以从项目根目录通过主应用入口执行当前已接入的登录模块：

```powershell
python -m auto_notify.app login
```

## 报表与模板原数据比对

下载报表后，可以使用独立的 `report-compare` skill 将新报表中所有工作表与正式模板中的对应工作表进行比对。默认按同名工作表匹配，如果名字不同，在 `report-compare/config.json` 的 `sheet_mappings` 里配置映射。比对结果会返回：

- `same`：数据一致
- `changed`：数据有变化，可以继续写入临时模板并发送
- `invalid`：字段不一致、数据为空或主键异常，应停止后续流程

示例：

```powershell
python report-compare\scripts\compare_reports.py --config report-compare\config.json
```

也可以临时通过命令行传入文件路径：

```powershell
python report-compare\scripts\compare_reports.py `
  --new-report "report-downloader/runtime/downloads/xxx.xls" `
  --template "C:/path/to/template.xlsx" `
  --header-row 1
```

如果有不参与比对的字段，可以重复传入 `--ignore-column`：

```powershell
python report-compare\scripts\compare_reports.py `
  --new-report "report-downloader/runtime/downloads/xxx.xls" `
  --template "C:/path/to/template.xlsx" `
  --ignore-column "更新时间"
```

如果需要按主键比对，而不是按行顺序比对，可以重复传入 `--key-column`。

## 比对通过后更新临时模板

`template-updater` 会读取 `report-compare` 的比对结果。只有结果为 `changed` 时才复制正式模板并写入下载报表中的变化页；`same` 会跳过，`invalid` 会停止。

```powershell
python template-updater\scripts\update_template.py
```

输出的临时模板副本会保存到 `template-updater/runtime/templates`，正式模板不会被直接覆盖。

## 发送成功后提交正式模板

`template-commit` 会读取 `template-updater` 的更新清单和 `excel-sender-wx` 的发送结果。只有企业微信真实发送成功后，才会备份正式模板并用临时模板副本替换它。

```powershell
python template-commit\scripts\commit_template.py
```

如果发送结果是 dry-run 或存在失败项，脚本会拒绝提交。

## Gotify 验证码

默认通过 SmsForwarder + Gotify 自动获取短信验证码。请在 `autologin/config.json` 中填写 `otp_config`：

```json
{
  "otp_mode": "gotify",
  "otp_config": {
    "gotify_url": "https://gotify.example.com",
    "client_token": "your_client_token_for_fetch_side",
    "title_prefix": "短信发送方或转发标题",
    "allowed_senders": ["短信发送方或转发标题"],
    "required_keywords": ["动态密钥"],
    "code_regex": "(?<!\\d)(\\d{4,8})(?!\\d)",
    "preferred_code_lengths": [6],
    "timeout_seconds": 180,
    "poll_interval_seconds": 3,
    "max_request_count": 3,
    "retry_interval_seconds": 5,
    "message_ttl_seconds": 600,
    "trigger_grace_seconds": 120,
    "require_new_message": true,
    "delete_after_success": true
  }
}
```

`title_prefix` 和 `allowed_senders` 要按 Gotify 实际收到的消息填写。如果 Gotify 消息标题就是短信发送方号码，就把这两个字段都填成该号码。`required_keywords` 建议填写短信里的稳定关键词，例如“动态密钥”；若短信正文没有固定关键词，可以设为空列表，并用 `preferred_code_lengths` 优先选择常见的 6 位验证码。`require_new_message` 会在请求短信前记录 Gotify 已有消息，后续只接受新消息，避免误用历史动态密钥。如果一轮等待超时，会按 `max_request_count` 重新触发短信发送。登录成功后，脚本会按 `delete_after_success` 配置删除已消费的 Gotify 消息。

如果需要临时回到手工输入验证码，把 `otp_mode` 改成 `manual`。

## 后续工程化方向

设计文档建议后续拆分为 `config_manager`、`scheduler`、`login_handler`、`report_handler`、`compare_handler`、`template_handler`、`notify_handler`、`commit_handler` 和 `state_manager` 等模块。下一步建议先把当前登录原型收敛为 `login_handler`，再接入 Gotify 验证码提供器。
