# 统一本地运行配置设计

## 目标

将本机或生产机的私有 JSON 配置收敛为唯一的
`config/runtime.local.json`。完成迁移后，程序不再读取
`config/dashboard/*.local.json`、`config/modules/*.local.json` 或
`config/tasks/*.local.json`；这些旧文件在每台机器完成迁移和验证后删除。

受版本控制的基础配置继续保留在原位置。私有字段只覆盖对应的基础配置，不能写回
普通 JSON 文件，也不能提交到 Git。

不处理已废弃且未被运行栈读取的 `*.local.ps1`；它们不属于本次 JSON 收敛范围，可由
各机器在确认不再需要后自行删除。

## 唯一文件结构

`runtime.local.json` 固定按以下顺序组织：

```json
{
  "runtime": {
    "root": "C:\\AutoNotifyRuntime",
    "work_pools": {}
  },
  "prefect": {
    "postgres": {},
    "api_url": ""
  },
  "dashboard": {
    "mysql": {},
    "session_overrides": {}
  },
  "monitor": {
    "prefect_webhook_secret": ""
  },
  "module_overrides": {
    "login_config": {},
    "tencent_docs": {},
    "wecom_sender": {}
  },
  "task_overrides": {}
}
```

- `runtime`、`prefect`、`dashboard.mysql` 与 `monitor` 是运行栈直接读取的基础设置。
- `dashboard.session_overrides` 深度合并到 `config/dashboard/session.json`。
- `module_overrides.<文件名>` 深度合并到 `config/modules/<文件名>.json`。键使用不含
  `.json` 的文件名，例如 `login_config`、`tencent_docs`、`wecom_sender`。
- `task_overrides.<文件名>` 深度合并到 `config/tasks/<文件名>.json`。任务名使用不含
  `.json` 的文件名；没有本地差异时保持空对象。
- 所有覆盖值只包含与基础配置不同的字段。基础配置仍提供非敏感默认值与字段说明。

运行配置模板需要列出当前使用的模块覆盖字段，但只能使用空值或占位符，不能包含真实
账号、密码、Token、Webhook 或 Cookie。

## 加载与校验

通用 JSON 加载器改为“基础文件 + 中央运行配置覆盖”模式：

1. 读取请求的受版本控制基础 JSON。
2. 仅当路径属于仓库的 `config/dashboard`、`config/modules` 或 `config/tasks` 时，读取
   `config/runtime.local.json` 中对应的覆盖片段并执行现有的深度合并。
3. 不再探测或读取同级 `*.local.json` 文件。
4. 对不属于上述受管目录的 JSON 保持基础文件原样读取，避免把临时测试夹具或运行产物
   误解释为项目配置。

路径映射必须拒绝父目录、绝对路径和未知目录。模块或任务覆盖的键不是有效文件名、对应
基础文件不存在或覆盖值不是对象时，加载时应给出明确错误，不能静默忽略。

`Import-RuntimeConfig` 继续校验现有必填运行字段，并新增覆盖分区的对象类型校验。运行
配置缺失时，报错仍指向唯一的 `config/runtime.local.json`。

## 一次性迁移

提供无网络副作用的迁移命令，将旧文件内容映射至中央文件：

- 默认仅输出将要导入的文件、目标分区和字段冲突，不写入或删除文件。
- `--apply` 使用原子写入更新 `runtime.local.json`，不删除旧文件。
- 已有中央字段与旧文件字段值不同则视为冲突并失败；不得自动选择、覆盖或泄露字段值。
- `--remove-legacy` 只能在成功写入、重新加载并验证全部映射后执行；仅删除已成功迁移的
  `config/dashboard/*.local.json`、`config/modules/*.local.json`、`config/tasks/*.local.json`。

迁移工具不输出凭据值、文件完整内容或环境变量。开发机与生产机各自独立执行迁移，不能
复制对方的 `runtime.local.json`。

## 文档与测试

更新 README、PROJECT_GUIDE 与运行配置模板，说明唯一入口、字段顺序、迁移步骤和删除
旧文件的条件；移除对模块级 `*.local.json` 的运行时说明。

测试覆盖：

- 运行配置模板包含全部中央分区且不含真实密钥。
- 驾驶舱、三个当前模块和动态任务覆盖均能正确深度合并。
- 同级旧 `*.local.json` 即使存在也不会被读取。
- 无效映射、错误类型和缺失基础文件会失败并给出可定位原因。
- 迁移预览不写入文件；冲突不写入；成功迁移后才允许删除旧文件。
- 现有启动脚本、运行时路径和模块服务测试继续使用统一后的加载器通过。

## 完成标准

每台机器只保留 `config/runtime.local.json` 作为活跃本地 JSON 配置；删除旧侧车文件后，
启动、模块凭据读取、驾驶舱锁覆盖、任务覆盖和监控 Webhook 均按原行为工作。Git 状态中
不得出现任何私有配置文件。
