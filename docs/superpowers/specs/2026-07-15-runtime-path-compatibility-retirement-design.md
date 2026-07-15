# 运行路径兼容层下线设计

## 目标

删除旧 `runtime/...` 路径兼容层，使运行数据的外部表示唯一为相对于
`runtime.root` 的受控路径。例如 `session/...`、`modules/...` 与 `flow/...`。
本次不改变登录、下载、通报、数据库或 Prefect 的业务执行逻辑。

## 范围

迁移两个仅存的运维脚本调用者：

- `scripts/tools/dashboard/import_v2_indicator_config.py` 的 `--bundle`
- `scripts/tools/dashboard/v2_cutover_audit.py` 的 `--bundle` 和 `--approvals`

它们的默认值已是 `modules/dashboard/output/v2_migration`，迁移后会由
`resolve_runtime_relative_path()` 解析到 `runtime.root`。`--config` 仍然是项目内
的 Dashboard 配置，不属于运行目录输入，本次保持原有项目相对路径语义。

## 路径契约

`validate_runtime_relative_path()` 成为唯一的运行目录输入校验器，并直接实现既有
的白名单规则，不再通过 `runtime/...` 格式的兼容校验器转发。允许的布局保持不变：

- `session/...`
- `config/drafts/...` 与 `config/versions/...`
- `logs/...`、`health/...`、`starter_templates/...`、`temp/...`
- `modules/<module>/output/...`
- `flow/<task>/{output,backup,debug,tmp}/...`

空路径、绝对路径、包含 `..` 的路径和以 `runtime/` 开头的历史格式均抛出
`ValueError`。解析结果必须位于 `runtime_root()` 下。

## 实现设计

1. 将白名单检查移入根相对校验函数，保留 `runtime_path()`、
   `resolve_runtime_relative_path()` 与 `is_runtime_relative_path()` 作为唯一公共接口。
2. 两个 Dashboard 脚本直接调用 `resolve_runtime_relative_path()`；为运行目录参数
   提取无副作用的小型解析边界，以便测试默认值、有效根相对值和拒绝情形，不运行
   数据库导入、Prefect 或网络检查。
3. 删除 `validate_runtime_path()` 和 `resolve_runtime_path()`，并删除专为历史
   `runtime/...` 解析保留的测试。
4. 将兼容层审计文档更新为“已下线”，记录零生产调用的复核命令和迁移后的拒绝契约。

## 错误处理与兼容性

本次是有意的破坏性收敛：上述三个运行目录参数不再接受项目相对路径、绝对路径
或 `runtime/...` 前缀。调用方必须传递相对于 `runtime.root` 的白名单路径。
错误信息应说明路径必须是受控的根相对路径，历史前缀则明确提示不得以
`runtime/` 开头。这样不会将输入悄然解析到项目工作区。

## 测试与验证

- 为根相对解析补充允许布局、历史前缀、绝对路径和父目录拒绝测试。
- 为两个脚本的纯解析边界补充默认路径、有效路径与非法路径测试。
- 断言旧公共解析 API 已移除，审计文件不再标记“待迁移”。
- 静态复核 `services`、`scripts` 和 `tests` 中不存在旧 API 调用。
- 运行受影响 pytest 集合、完整 `pytest -p no:cacheprovider -q` 与
  `git diff --check`。不设置外部环境变量，也不执行真实登录、下载、通知、数据库
  导入或 Prefect 调用。
