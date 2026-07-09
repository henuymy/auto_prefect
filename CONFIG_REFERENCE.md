# 配置文件参数说明

本文档用于解释 `config/` 目录下各类 JSON 配置文件的用途和参数含义，帮助理解项目的配置结构、运行逻辑以及常见修改点。

## 1. 配置分层总览

项目当前有三层配置：

1. `config/modules/*.json`
作用：模块级基础配置，定义登录、下载、比对、模板更新、发送、提交等通用规则。多个任务可以复用。

2. `config/reports/*.json`
作用：报表级业务配置，定义“某一类通报”使用什么模板、请求什么下载参数、怎么比对、发送哪些通报页和文本。

3. `config/tasks/*.json`
作用：流程级编排配置，定义本次 Flow 用哪个报表配置、每个步骤是否启用、运行产物写到哪里、`same` 时是否等待重试等。

可以把它理解为：

- `modules`：通用能力
- `reports`：某个业务通报
- `tasks`：某次流程如何串起来跑

## 2. 配置文件清单

### 2.1 `config/modules`

- `autologin.json`
- `login_config.json`
- `report_downloader.json`
- `report_compare.json`
- `template_updater.json`
- `wecom_sender.json`
- `template_commit.json`

### 2.2 `config/reports`

- `日通报.json`
- `test.json`

### 2.3 `config/tasks`

- `local.json`
- `local_force_login.json`
- `test.json`
- `日通报.json`

## 3. `config/modules` 说明

### 3.1 `autologin.json`

用途：控制 Cookie 是否可复用、何时重新登录、登录命令怎么执行。

字段说明：

- `cookie_dump_path`
作用：当前系统使用的 Cookie 导出文件路径。
典型值：`runtime/cookies/cookie_dump.json`

- `legacy_cookie_dump_path`
作用：兼容旧目录的 Cookie 文件路径。若当前路径不存在，可从旧路径同步过来。
典型值：`null`

- `required_stages`
作用：要求 Cookie 文件中必须存在的 stage 列表。只有这些 stage 都可用，才认为 Cookie 可复用。
典型值：`["report_analysis"]`

- `min_ttl_seconds`
作用：Cookie 最小剩余有效时长。若小于该值，会判定为过期，需要重新登录。

- `max_age_seconds`
作用：整个 Cookie dump 的最大允许生成时间。超过该时间也会强制重新登录。

- `allow_login`
作用：Cookie 无效时是否允许自动登录。
典型值：`true`

- `login_command`
作用：触发自动登录的命令。
当前示例：调用 `services.login_service.run_login('config/modules/login_config.json')`

### 3.2 `login_config.json`

用途：Selenium 自动登录与验证码拉取配置。

字段说明：

- `login_url`
作用：NGBOSS 登录页地址。

- `usm_host`
作用：USM 主机名，用于识别是否已进入目标系统。

- `credentials.username`
作用：登录用户名。
说明：敏感信息，建议后续迁移到本地私有配置或环境变量。

- `credentials.password`
作用：登录密码。
说明：敏感信息。

- `otp_mode`
作用：验证码获取方式。
当前支持：`gotify`

- `otp_config.gotify_url`
作用：Gotify 服务地址。

- `otp_config.client_token`
作用：Gotify 客户端 token。
说明：敏感信息。

- `otp_config.title_prefix`
作用：用于筛选短信标题前缀。

- `otp_config.allowed_senders`
作用：允许的发送方列表。

- `otp_config.required_keywords`
作用：验证码消息中必须包含的关键字。

- `otp_config.code_regex`
作用：从消息文本中提取验证码的正则表达式。

- `otp_config.preferred_code_lengths`
作用：优先接受的验证码长度，例如 6 位。

- `otp_config.timeout_seconds`
作用：等待验证码超时时间。

- `otp_config.poll_interval_seconds`
作用：轮询 Gotify 的间隔。

- `otp_config.max_request_count`
作用：验证码重新请求的最大次数。

- `otp_config.retry_interval_seconds`
作用：一次等待失败后，重新触发短信前的等待秒数。

- `otp_config.message_ttl_seconds`
作用：消息允许保留多久，超过后视为无效。

- `otp_config.trigger_grace_seconds`
作用：触发短信后允许的缓冲时间。

- `otp_config.require_new_message`
作用：是否要求必须是新的验证码消息。

- `otp_config.fetch_limit`
作用：每次从 Gotify 拉取的消息数。

- `otp_config.delete_after_success`
作用：成功读取验证码后是否删除 Gotify 消息。

- `usm_cookie_apps`
作用：登录完成后要进入哪些应用并抓取 Cookie。
每项字段：
`stage`：Cookie 分组名，供下载配置引用。
`name`：界面上应用显示名称。

- `cookie_dump.enabled`
作用：是否启用 Cookie 导出。

- `cookie_dump.output_file`
作用：Cookie dump 输出路径。

- `browser.window_width`
作用：浏览器窗口宽度。

- `browser.window_height`
作用：浏览器窗口高度。

- `browser.implicit_wait`
作用：浏览器隐式等待秒数。

### 3.3 `report_downloader.json`

用途：下载模块的通用 HTTP 配置。

字段说明：

- `cookie_dump_path`
作用：下载时读取的 Cookie dump 路径。

- `output_dir`
作用：下载文件输出目录。

- `manifest_path`
作用：下载结果清单输出路径。

- `request_timeout_seconds`
作用：单次下载请求超时时间。

- `verify_ssl`
作用：是否校验证书。
内网环境常设为 `false`。

- `trust_env`
作用：是否继承系统代理环境变量。

- `proxies`
作用：统一代理配置。
为空对象表示不用代理。

- `report_defaults`
作用：报表下载默认参数模板。实际下载时会与 `config/reports/*.json` 中的 `download` 合并。

`report_defaults` 子字段说明：

- `enabled`
作用：该下载项默认是否启用。

- `method`
作用：HTTP 请求方法。常见为 `POST`。

- `headers`
作用：固定请求头。

- `headers_from_cookies`
作用：把 Cookie 中的指定值映射到请求头。
示例：`"ssr-token": "ssr-token"` 表示请求头 `ssr-token` 的值来自同名 Cookie。

- `csrf_headers_from_cookies`
作用：动态从 Cookie 里读取“header 名”和“header 值”。
示例：`"ssr-header": "ssr-token"` 表示：
请求头名来自 Cookie `ssr-header`
请求头值来自 Cookie `ssr-token`

- `allow_redirects`
作用：是否允许请求自动跟随跳转。

### 3.4 `report_compare.json`

用途：比对模块的默认输出和显示配置。

字段说明：

- `output_path`
作用：比对结果 JSON 输出路径。

- `sample_limit`
作用：最多保留多少条差异样本，便于排查。

- `visible`
作用：是否显示 Excel 窗口。

### 3.5 `template_updater.json`

用途：根据比对结果生成新的临时模板文件。

字段说明：

- `compare_result_path`
作用：比对结果文件路径。

- `compare_config_path`
作用：比对配置文件路径，用于补充解析源报表路径和模板路径。

- `source_report_path`
作用：源报表文件路径。
说明：可为空；为空时会尝试从比对配置里读取。

- `template_path`
作用：正式模板路径。
说明：可为空；为空时会尝试从比对配置里读取。

- `output_dir`
作用：生成的新模板输出目录。

- `manifest_path`
作用：模板更新清单输出路径。

- `write_sheets`
作用：控制写哪些工作表。
当前支持：
`changed`：只覆盖发生变化的 sheet
`all_compared`：覆盖所有参与比对的 sheet

- `copy_mode`
作用：预留字段，当前代码未实际使用。

- `visible`
作用：是否显示 Excel 窗口。

### 3.6 `wecom_sender.json`

用途：截图、文本提取、企业微信发送的默认配置。

字段说明：

- `capture_defaults`
作用：截图默认参数。

`capture_defaults.mode`
作用：截图模式。
当前常见值：
`used_range`
`explicit_range`
`current_region`

`capture_defaults.shrink_empty_edges`
作用：是否自动裁掉外围空白区域。

`capture_defaults.appearance`
作用：Excel `CopyPicture` 外观参数。
常见值：`printer`、`screen`

`capture_defaults.format`
作用：图片格式。
常见值：`picture`、`bitmap`

`capture_defaults.export_scale`
作用：导出图片缩放倍数。

`capture_defaults.optimize_png`
作用：是否压缩 PNG。

`capture_defaults.png_colors`
作用：PNG 量化颜色数，用于减小体积。

- `workbooks`
作用：要处理的工作簿列表。

`workbooks[].name`
作用：工作簿显示名称。

`workbooks[].file`
作用：工作簿文件路径。

`workbooks[].excel_open.update_links`
作用：打开 Excel 时是否更新外部链接。

`workbooks[].excel_open.refresh_before_capture`
作用：截图前是否刷新查询。

`workbooks[].excel_open.refresh_timeout_seconds`
作用：刷新超时时间。

`workbooks[].reports`
作用：工作簿内的逻辑发送分组。

`workbooks[].reports[].name`
作用：该分组名称。

`workbooks[].reports[].items`
作用：真正发送的图片/文本条目，按数组顺序发送。

`items[].type`
作用：条目类型。
支持：
`image`
`text`

`items[].sheet`
作用：对应 Excel 工作表名称。

`items[].capture`
作用：图片条目的截图参数，可覆盖 `capture_defaults`。

`items[].text`
作用：文本条目的读取参数。
常见值：
`{"mode": "used_range"}`

- `wecom.mode`
作用：发送模式。
当前使用：`webhook`

- `wecom.webhook_url`
作用：企业微信机器人 webhook。
说明：敏感信息。

- `output.runtime_dir`
作用：发送模块运行目录。

- `output.image_dir`
作用：截图图片目录。

- `output.package_file`
作用：消息包 JSON 路径。

- `output.preview_image_file`
作用：预览拼图路径。

- `output.send_result_file`
作用：发送结果文件路径。

- `output.intermediate_dir`
作用：截图与打包过程的中间产物目录，默认相对于 `runtime_dir`。

- `output.keep_intermediate_files`
作用：是否保留截图与打包过程的中间产物，便于排查图片在哪一步异常。
建议：默认关闭，仅在排障时临时开启。

- `output.cleanup_after_send`
作用：发送成功后是否清理中间产物。

### 3.7 `template_commit.json`

用途：发送成功后，把临时模板提交为正式模板。

字段说明：

- `update_manifest_path`
作用：模板更新清单路径。

- `send_result_path`
作用：发送结果清单路径。

- `backup_dir`
作用：覆盖正式模板前的备份目录。

- `manifest_path`
作用：提交结果清单路径。

- `require_send_success`
作用：是否要求所有发送项都成功，才允许提交模板。

- `allow_dry_run_commit`
作用：dry-run 发送后是否允许提交模板。
通常为 `false`。

- `overwrite`
作用：是否真的覆盖正式模板。
若设为 `false`，只做校验不落地。

## 4. `config/reports` 说明

报表配置描述“某一类通报”。

### 4.1 通用结构

示例结构：

```json
{
  "name": "日通报",
  "template_path": "templates/xxx.xlsx",
  "download": { ... },
  "compare": { ... },
  "send": { ... }
}
```

字段说明：

- `name`
作用：报表名称。

- `template_path`
作用：该报表对应的正式模板路径。

### 4.2 `download`

用途：描述这个报表该如何下载。

字段说明：

- `name`
作用：本次下载项名称。
说明：`tasks.steps.compare.download_report_name` 会用它来匹配具体下载结果。

- `stage`
作用：使用哪组 Cookie。
必须与 `login_config.json -> usm_cookie_apps[].stage` 一致。

- `url`
作用：实际下载接口地址。

- `data`
作用：下载请求体。
支持普通固定值，也支持动态占位符。

当前支持的占位符：

- `${today}` -> 当前日期，例如 `2026-04-22`
- `${today_yyyymmdd}` -> 当前日期，例如 `20260422`
- `${yesterday}` -> 前一天日期，例如 `2026-04-21`
- `${yesterday_yyyymmdd}` -> 前一天日期，例如 `20260421`
- `${day_before_yesterday}` -> 前天日期，例如 `2026-04-20`
- `${day_before_yesterday_yyyymmdd}` -> 前天日期，例如 `20260420`
- `${date:yesterday-1M|yyyyMMdd}` -> 上月同期，例如 `20260321`
- `${date:yesterday-1y|yyyyMMdd}` -> 去年同期，例如 `20250421`
- `${date:today-7d|yyyy-MM-dd}` -> 7 天前，例如 `2026-04-15`
- `${hour}` -> 当前小时，不补零，例如 `15`
- `${hour2}` -> 当前小时，两位格式，例如 `08`

通用日期格式为 `${date:<基准><偏移>|<格式>}`。基准支持 `today`、`yesterday`、`day_before_yesterday`；偏移支持 `d` 天、`M` 月、`y` 年；格式支持 `yyyyMMdd`、`yyyy-MM-dd`。月/年偏移遇到目标月份没有同一天时，取目标月最后一天。

示例：

```json
"data": {
  "queryDate": "${today}",
  "hour": "${hour}"
}
```

### 4.3 `compare`

用途：描述比对规则。

字段说明：

- `sheet_mappings`
作用：指定新报表与模板中哪些工作表要一一比对。
为空数组时，默认按同名 sheet 自动匹配。

单项可写为字符串：

```json
"sheet_mappings": ["日通报"]
```

也可写为对象：

```json
{
  "name": "日通报",
  "new_sheet_name": "新表sheet名",
  "template_sheet_name": "模板sheet名",
  "header_row": 1,
  "ignore_columns": [],
  "key_columns": []
}
```

- `header_row`
作用：表头所在行号，从 1 开始。

- `ignore_columns`
作用：比对时忽略的列名列表。

- `key_columns`
作用：按主键比对时使用的列名列表。
为空时按整行位置比对。

### 4.4 `send`

用途：描述发送内容。

字段说明：

- `workbook_name`
作用：发送时显示的工作簿名称。

- `items`
作用：发送项数组，按顺序发送。
支持多个通报页、多个通报文本混排。

常见示例：

```json
"items": [
  { "type": "image", "sheet": "通报" },
  { "type": "text", "sheet": "通报语句", "text": { "mode": "used_range" } }
]
```

`items[].type`
作用：条目类型，支持 `image` 和 `text`。

`items[].sheet`
作用：要读取的 Excel sheet 名称。

`items[].text.mode`
作用：文本读取模式。
常见：`used_range`

## 5. `config/tasks` 说明

任务配置决定一次 flow 如何运行。

### 5.1 通用结构

示例结构：

```json
{
  "flow_name": "auto-notify-test",
  "runtime_dir": "runtime/flow/test",
  "report_config_path": "config/reports/test.json",
  "wait_for_change": { ... },
  "steps": { ... }
}
```

字段说明：

- `flow_name`
作用：逻辑上的流程名称。

- `runtime_dir`
作用：该任务的运行目录，生成的配置和产物通常放在这里。

- `report_config_path`
作用：当前任务引用的报表配置路径。

### 5.2 `wait_for_change`

用途：当比对结果是 `same` 时，是否等待并重试。

字段说明：

- `enabled`
作用：是否启用 `same -> sleep -> 重试`。

- `poll_interval_seconds`
作用：每次重试前等待多少秒。

- `max_wait_minutes`
作用：最多等待多久，超过后返回 `timeout_no_change`。

### 5.3 `steps`

作用：控制每一步是否启用、用哪个模块配置、生成文件写到哪里。

#### `steps.login`

- `enabled`
作用：是否启用会话准备/自动登录。

- `config_path`
作用：引用 `config/modules/autologin.json`

- `force_refresh`
作用：是否强制重新登录，不复用现有 Cookie。

#### `steps.download`

- `enabled`
作用：是否启用下载步骤。

- `config_path`
作用：引用 `config/modules/report_downloader.json`

- `dry_run`
作用：若为 `true`，只生成请求摘要和清单，不实际下载。

- `debug`
作用：是否在 dry-run 时输出更多请求信息。

#### `steps.compare`

- `enabled`
作用：是否启用比对。

- `config_path`
作用：引用 `config/modules/report_compare.json`

- `generated_config_path`
作用：运行时生成的完整比对配置路径。

- `download_report_name`
作用：当下载结果有多个文件时，按这个名称匹配要送去比对的那个文件。

#### `steps.update_template`

- `enabled`
作用：是否启用模板更新。

- `config_path`
作用：引用 `config/modules/template_updater.json`

- `generated_config_path`
作用：运行时生成的模板更新配置路径。

- `output_dir`
作用：可选，覆盖模块默认的新模板输出目录。

- `manifest_path`
作用：可选，覆盖模块默认的更新清单路径。

#### `steps.send_wecom`

- `enabled`
作用：是否启用发送。

- `base_config_path`
作用：引用 `config/modules/wecom_sender.json`

- `generated_config_path`
作用：运行时生成的发送配置路径。

- `runtime_dir`
作用：可选，覆盖发送模块运行目录。

- `dry_run`
作用：是否只构建消息包，不真的发企业微信。

- `timeout`
作用：单次发送超时时间。

#### `steps.commit_template`

- `enabled`
作用：是否启用正式模板提交。

- `config_path`
作用：引用 `config/modules/template_commit.json`

- `update_manifest_path`
作用：可选，覆盖模板更新清单路径。

- `send_result_path`
作用：可选，覆盖发送结果路径。

- `backup_dir`
作用：可选，覆盖备份目录。

- `manifest_path`
作用：可选，覆盖提交清单路径。

## 6. 各任务文件区别

- `local.json`
用途：本地默认任务，使用 `日通报.json`，登录时优先复用 Cookie。

- `local_force_login.json`
用途：本地强制登录任务，和 `local.json` 类似，但 `login.force_refresh=true`。

- `test.json`
用途：测试任务，引用 `config/reports/test.json`，便于验证新参数、新接口、新流程。

- `日通报.json`
用途：按“一个业务报表一个 task”方式生成的任务配置。

## 7. 常见修改场景

### 场景 1：换一个新的导出接口

通常改这里：

1. `config/reports/*.json -> download.url`
2. `config/reports/*.json -> download.data`
3. 如需特殊 header/cookie，再改 `config/modules/report_downloader.json`

### 场景 2：比对结果 `same` 时继续等

改这里：

1. `config/tasks/*.json -> wait_for_change.enabled`
2. `config/tasks/*.json -> wait_for_change.poll_interval_seconds`
3. `config/tasks/*.json -> wait_for_change.max_wait_minutes`

### 场景 3：一个通报发多个页面、多个文本

改这里：

1. `config/reports/*.json -> send.items`

### 场景 4：换模板

改这里：

1. `config/reports/*.json -> template_path`

### 场景 5：切换登录方式或 stage

改这里：

1. `config/modules/login_config.json -> usm_cookie_apps`
2. `config/reports/*.json -> download.stage`

## 8. 敏感字段提醒

以下字段涉及账号、密码、token、webhook，建议后续迁移为本地私有配置：

- `config/modules/login_config.json -> credentials.username`
- `config/modules/login_config.json -> credentials.password`
- `config/modules/login_config.json -> otp_config.client_token`
- `config/modules/wecom_sender.json -> wecom.webhook_url`

## 9. 排查建议

如果运行失败，可优先看这几个位置：

1. `runtime/cookies/cookie_dump.json`
看 stage 是否存在、Cookie 是否新鲜。

2. `runtime/report_downloader/download_manifest.json`
看下载是否成功、文件名是否正确。

3. `runtime/report_compare/compare_result.json`
看结果是 `same`、`changed` 还是 `invalid`。

4. `runtime/template_updater/update_manifest.json`
看模板是否成功生成。

5. `runtime/excel_sender_wx/send_result.json`
看企业微信发送结果。

6. `runtime/template_commit/commit_manifest.json`
看正式模板是否已提交。

