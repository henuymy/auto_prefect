# 第一批质量与截图配置修复设计

## 目标

恢复 Python 质量门禁，并让自动通报的截图分辨率配置可预测、可验证。此次变更不改变企业微信发送、Excel 工作簿处理或生产运行路径。

## 范围

1. 修复当前 Ruff 报出的五项错误，确保既有 CI 的静态检查通过。
2. 在 GitHub Actions 中安装 Node 20，并执行前端的锁定安装、类型检查与生产构建。
3. 将截图清晰度的唯一配置项固定为 `capture_defaults.pdf_dpi`；本机覆盖配置移除未被当前渲染器读取的 `appearance`、`format` 和 `export_scale`。
4. 为 `pdf_dpi` 的默认值、边界截断和无效旧字段不影响输出补充自动化测试。
5. 将驾驶舱查询测试中的日期写入改为显式、兼容的时间表示，消除当前 SQLite 日期适配弃用告警。

## 非目标

- 不迁移或轮换企业微信 Webhook；密钥引用、API 脱敏和密钥扫描作为独立后续工作。
- 不新增 Windows 自托管 Runner，也不在本批接入真实 Edge、Excel COM 或企业微信的集成测试。
- 不拒绝旧截图字段；运行时继续忽略它们，以保持已有任务配置兼容。

## 设计

### 质量门禁

保留单一 GitHub Actions job。该 job 在 Python 依赖与锁文件检查后执行 Ruff 和 Pytest；随后设置 Node 20，以 `npm ci` 安装 `frontend/package-lock.json` 固定的依赖，并运行 `npm run typecheck` 与 `npm run build`。任何一项失败均阻止合并。

### 截图配置

PDF 转 PNG 时继续以 `pdf_dpi` 计算渲染矩阵，并限制在 96 至 600。模块默认配置保持 300 DPI；本机配置覆盖为 600 DPI 并保留 PNG 优化。`appearance`、`format` 与 `export_scale` 不再由本机配置写入，但历史任务中出现它们时不会报错，也不会改变结果。

### 测试与告警

新增或调整截图服务单测，覆盖：默认 300 DPI、600 DPI、低于 96 时提升至 96、高于 600 时降至 600，以及 `export_scale` 的值不会影响渲染矩阵。驾驶舱查询测试把 Python `datetime` 适配替换为与 SQLite/SQLAlchemy 兼容的写入方式，测试应无该弃用告警。

## 验收标准

- `ruff check backend infrastructure models services tasks flows tests migrations` 成功。
- `python -m pytest -q` 成功，且不再出现 SQLite 默认 datetime adapter 弃用告警。
- 前端 `npm ci`、`npm run typecheck`、`npm run build` 成功，并在 CI 中执行。
- 同一 PDF 在相同 `pdf_dpi` 下，不因 `export_scale` 的存在或值变化而改变渲染矩阵；600 DPI 继续输出比 300 DPI 更高的像素尺寸。
