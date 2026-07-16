# 腾讯智能表格前端与通报集成设计

## 目标

让配置中心能够保存、试运行和发布腾讯文档在线智能表格的导出任务。任务读取
用户指定的智能子表、全部字段和全部记录，生成一个 XLSX 文件，并可进入既有的
模板生成与自动通报流程。

## 范围

- 新增独立下载源 `tencent_smartbook`。
- 前端“数据抓取”新增第三个“智能表格”标签。
- 后端实现 Smartbook 子表、字段和分页记录的读取及 XLSX 导出。
- 接入配置标准化、校验、试运行、新手模板生成、发布调度和通知 Flow。
- 保留 `scripts/dev/test_tencent_smartbook_export.py` 作为人工诊断工具，并使其复用正式服务。

普通 HTTP API 下载和普通腾讯 Sheet 下载的接口、配置和行为不变。

## 配置契约

智能表格与普通腾讯 Sheet 采用相同的外层字段：`name`、`doc_url`、可选
`file_id`、`output_filename` 和 `sheets`。唯一语义差异是 Smartbook 不支持 A1
范围；每个子表总是导出全部字段和记录，因而 Smartbook 的 `sheets[]` 不保存
`range`。

```json
{
  "source": "tencent_smartbook",
  "name": "智能表格日报",
  "doc_url": "https://docs.qq.com/smartsheet/DY1NuemRyUHhpcWZQ?tab=t00i2h",
  "output_filename": "智能表格日报.xlsx",
  "sheets": [
    {
      "sheet_name": "汇总",
      "sheet_id": "t00i2h",
      "output_sheet_name": "汇总"
    }
  ]
}
```

每个 `sheets[]` 项必须指定 `sheet_id` 或 `sheet_name`，可以同时指定；运行时以
`sheet_id` 优先。重复指向同一子表的配置会被拒绝。

## 前端

`ConfigForm` 的“数据抓取”区分三类来源：业务接口、腾讯文档和智能表格。智能表格
卡片沿用普通腾讯 Sheet 卡片的布局、折叠方式和子表行操作：

- 可编辑抓取名称、输出文件名、文档链接和可选 `file_id`。
- 可添加、复制、删除子表行，并编辑子表名称、ID 和导出工作表名。
- 输入含 `tab=` 的 `doc_url` 时，自动填充首个空的 `sheet_id`。
- 不显示普通 Sheet 专用的读取范围输入；文案明确说明会读取全部字段和记录。

前端类型为 Smartbook 子表单独建模，下载源联合类型增加 `tencent_smartbook`。
前端 JSON Schema 与表单验证要求文档链接或 `file_id`，以及至少一个带名称或 ID
的子表。

## 后端与数据流

新增 `services/tencent_smartbook_service.py`。服务复用现有腾讯文档的本地凭据加载、
文件 ID 转换、认证头、重试设置和安全文件名规则；请求 `smartbook/v2` 接口时：

1. 查询文档中的可用子表，按配置顺序匹配指定子表。
2. 分页查询字段，按 API 返回顺序确定 Excel 表头。
3. 分页查询记录；富文本、链接、图片、选项及其他复杂值转换为可读文本。
4. 对每个子表创建一个 Excel 工作表，写入表头和非空 `values` 记录。
5. 返回输出路径、子表 ID、工作表名、读取记录数和有效导出记录数。

空 `values` 记录不会写入 Excel，但会保留在 `fetched_records` 统计中。接口错误只
包含 HTTP 状态码或业务码，不输出 Token、完整响应正文或业务敏感数据。

`services.method_service.download_reports()` 根据 `source` 分派普通 Sheet 和
Smartbook 下载。所有把 `tencent_sheet` 视为无会话来源的判断都扩展为同时涵盖
`tencent_smartbook`，包括配置校验、Flow Session 预检、新手模板和 Prefect 运行器。
两类腾讯文档来源均读取被 Git 忽略的 `config/modules/tencent_docs.local.json`，不将
凭据暴露给前端或写入任务配置。

## 错误处理

- 缺失 `client_id`、`access_token` 或 `open_id`：明确提示检查本地腾讯文档配置。
- 缺失文档链接、`file_id`、子表选择器或子表重复：保存、试运行和发布前拒绝配置。
- 文档 ID 转换失败、无匹配子表、字段／记录响应结构异常或分页偏移重复：当前下载项
  失败，错误通过既有运行日志和 API 错误链路呈现。
- 输出文件被 Excel 占用：沿用普通腾讯 Sheet 的可操作提示。

## 验证

自动化测试覆盖：

1. Smartbook 配置的标准化与前后端校验。
2. 子表按 ID／名称和配置顺序匹配、重复检测。
3. 字段和记录的分页读取、空记录过滤、复杂值文本化和 XLSX 输出。
4. 下载分派、无 Session Stage 预检、试运行与新手模板路径。
5. 前端类型检查与生产构建。

人工验证脚本针对真实文档链接运行，确认指定子表能生成可读工作簿；脚本输出只显示
文件路径和计数，不打印认证信息或记录内容。
