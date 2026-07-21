# 统一本地运行配置 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 `config/runtime.local.json` 成为唯一活跃的本地 JSON 配置，并安全迁移、删除原有模块、驾驶舱和任务侧车配置。

**Architecture:** 受版本控制的配置仍是基础来源。通用加载器仅对仓库内的 `config/dashboard`、`config/modules` 和 `config/tasks` 解析中央运行配置中的对应覆盖片段并深度合并，绝不再读取同级 `*.local.json`。独立迁移命令以预览、原子写入、冲突失败和显式删除四个阶段完成每台机器的本地数据迁移。

**Tech Stack:** Python 3.13、标准库 `json`/`argparse`/`pathlib`、PowerShell 7、pytest。

## Global Constraints

- 唯一本地 JSON 入口为 `config/runtime.local.json`；基础配置文件不得写入真实凭据。
- 中央文件顺序固定为 `runtime`、`prefect`、`dashboard`、`monitor`、`module_overrides`、`task_overrides`。
- 不读取、不创建或不自动删除 `config/**/*.local.json`；删除只由迁移命令的显式参数完成。
- 迁移预览、冲突消息和测试输出不得显示密码、Token、Webhook、Cookie 或完整私有 JSON。
- 开发机和生产机分别迁移各自的本地文件，不得复制彼此的 `runtime.local.json`。
- 不修改、迁移或删除已废弃的 `*.local.ps1`；它们不属于本次 JSON 范围。

---

### Task 1: 中央覆盖加载器与运行配置结构

**Files:**
- Modify: `utils/config_loader.py:1-33`
- Modify: `config/runtime.local.example.json`
- Modify: `scripts/lib/runtime_config.ps1:1-130`
- Create: `tests/test_runtime_local_overrides.py`
- Modify: `tests/test_development_environment_contract.py:121-161,235-285`

**Interfaces:**
- Consumes: `config/runtime.local.json`、受版本控制的 `config/dashboard/*.json`、`config/modules/*.json`、`config/tasks/*.json`。
- Produces: `load_json_with_runtime_override(path, *, project_root=None, runtime_config_path=None) -> tuple[dict, Path]`，供所有配置消费者替代旧加载器。
- Produces: `validate_runtime_local_overrides(payload, *, project_root) -> None`，供加载器和迁移工具共同验证覆盖分区与目标路径。

- [ ] **Step 1: 写入加载器的失败测试**

在 `tests/test_runtime_local_overrides.py` 建立临时项目根目录和 JSON 写入助手；覆盖驾驶舱、模块、任务三种映射，同级旧文件不再生效，以及错误映射失败：

```python
def test_managed_config_uses_runtime_override_and_ignores_sidecar(tmp_path):
    project_root = tmp_path / "project"
    module = project_root / "config" / "modules" / "tencent_docs.json"
    write_json(module, {"credentials": {"client_id": ""}, "retry": {"attempts": 3}})
    write_json(module.with_name("tencent_docs.local.json"), {"credentials": {"client_id": "legacy"}})
    runtime = project_root / "config" / "runtime.local.json"
    write_json(runtime, {"module_overrides": {"tencent_docs": {"credentials": {"client_id": "central"}}}})

    payload, resolved = load_json_with_runtime_override(module, project_root=project_root)

    assert resolved == module.resolve()
    assert payload == {"credentials": {"client_id": "central"}, "retry": {"attempts": 3}}


def test_unknown_override_target_is_rejected(tmp_path):
    project_root = tmp_path / "project"
    write_json(project_root / "config" / "runtime.local.json", {"module_overrides": {"missing": {}}})

    with pytest.raises(ValueError, match="module_overrides.missing"):
        validate_runtime_local_overrides(
            read_json(project_root / "config" / "runtime.local.json"),
            project_root=project_root,
        )
```

同时增加断言：不在受管 `config` 目录内的临时 JSON 不读取中央覆盖；`dashboard.session_overrides`、`task_overrides.<任务名>` 仅接受对象；键中含 `/`、`\\`、`.` 或映射目标缺失时失败。

- [ ] **Step 2: 运行测试，确认当前实现失败**

Run:

```powershell
python -m pytest -p no:cacheprovider tests/test_runtime_local_overrides.py -q
```

Expected: FAIL，原因是 `load_json_with_runtime_override` 和 `validate_runtime_local_overrides` 尚不存在。

- [ ] **Step 3: 实现受管路径映射与中央合并**

在 `utils/config_loader.py` 保留 `deep_merge()`，删除 `local_override_path()` 与同级文件读取。实现以下接口；读取中央文件时只返回覆盖对象，且不输出其值：

```python
PROJECT_ROOT = Path(__file__).resolve().parents[1]


def load_json_with_runtime_override(
    path: str | Path,
    *,
    project_root: Path | None = None,
    runtime_config_path: Path | None = None,
) -> tuple[dict, Path]:
    resolved = Path(path).resolve()
    payload = _read_json_object(resolved)
    root = (project_root or PROJECT_ROOT).resolve()
    runtime_path = (runtime_config_path or root / "config" / "runtime.local.json").resolve()
    override = runtime_override_for(resolved, project_root=root, runtime_config_path=runtime_path)
    return deep_merge(payload, override), resolved


def runtime_override_for(
    config_path: Path,
    *,
    project_root: Path,
    runtime_config_path: Path,
) -> dict:
    try:
        relative = config_path.resolve().relative_to((project_root / "config").resolve())
    except ValueError:
        return {}
    if relative == Path("dashboard/session.json"):
        section, key = "dashboard", "session_overrides"
    elif len(relative.parts) == 2 and relative.suffix == ".json":
        section = {"modules": "module_overrides", "tasks": "task_overrides"}.get(relative.parts[0])
        key = relative.stem
    else:
        return {}
    if section is None:
        return {}
    if not runtime_config_path.exists():
        return {}
    runtime_payload = _read_json_object(runtime_config_path)
    validate_runtime_local_overrides(runtime_payload, project_root=project_root)
    return _object_value(runtime_payload.get(section, {}), key)
```

`validate_runtime_local_overrides()` 必须允许没有任何覆盖的中央文件，但当某个覆盖分区存在时验证其类型、键格式和目标基础文件。对受管以外的路径捕获 `ValueError` 并返回 `{}`；对受管路径内的无效映射抛出包含分区键名但不包含配置值的 `ValueError`。

更新 `config/runtime.local.example.json`：按全局约定重排现有基础字段，并新增 `dashboard.session_overrides`、三个当前模块的 `module_overrides` 占位结构及空的 `task_overrides`。占位符使用 `<username>`、`<password>`、`<token>`、`<webhook-url>`，不得复制本机配置。

在 `scripts/lib/runtime_config.ps1` 增加对象校验函数，并在导出环境变量前校验可选覆盖分区存在时均为对象：

```powershell
function Assert-OptionalRuntimeConfigObject {
    param([object]$Config, [string]$Path)
    $value = $Config
    foreach ($segment in $Path.Split('.')) {
        $property = $value.PSObject.Properties[$segment]
        if ($null -eq $property) {
            return
        }
        $value = $property.Value
    }
    if ($value -isnot [pscustomobject]) {
        throw "运行配置必须为对象: $Path"
    }
}

Assert-OptionalRuntimeConfigObject -Config $config -Path "dashboard.session_overrides"
Assert-OptionalRuntimeConfigObject -Config $config -Path "module_overrides"
Assert-OptionalRuntimeConfigObject -Config $config -Path "task_overrides"
```

将实际访问改为先检查属性是否存在；不存在时视为 `{}`，以便迁移工具可在首次导入前运行。迁移完成后的模板与本机文件均应保留三个空覆盖分区，保证文件结构稳定。

- [ ] **Step 4: 运行聚焦测试，确认通过**

Run:

```powershell
python -m pytest -p no:cacheprovider tests/test_runtime_local_overrides.py tests/test_development_environment_contract.py -q
```

Expected: PASS，中央文件能覆盖三类受管配置，侧车文件被忽略，模板和 PowerShell 校验契约通过。

- [ ] **Step 5: 提交加载器与模板变更**

```powershell
git add utils/config_loader.py config/runtime.local.example.json scripts/lib/runtime_config.ps1 tests/test_runtime_local_overrides.py tests/test_development_environment_contract.py
git commit -m "refactor: centralize local JSON overrides"
```

### Task 2: 迁移全部配置消费者与诊断文案

**Files:**
- Modify: `backend/services/starter_template.py`
- Modify: `flows/notify_single_flow.py`
- Modify: `flows/session_keeper_flow.py`
- Modify: `services/business_run_alert_service.py`
- Modify: `services/dashboard_v2_trigger.py`
- Modify: `services/login_service.py`
- Modify: `services/session_business_failure_service.py`
- Modify: `services/tencent_sheet_service.py:58-80`
- Modify: `services/method_service.py:659-680`
- Modify: `tests/test_dashboard_v2_trigger.py:166-184`
- Modify: `tests/test_tencent_sheet_service.py:139-160`
- Modify: `tests/test_tencent_smartbook_service.py`
- Modify: `tests/test_login_service.py:88-101`

**Interfaces:**
- Consumes: Task 1 的 `load_json_with_runtime_override()`。
- Produces: 所有仓库配置消费者仅使用中央覆盖加载器；面向用户的错误或 dry-run 信息仅指向 `config/runtime.local.json` 的明确分区。

- [ ] **Step 1: 将消费者测试改为中央覆盖语义**

将驾驶舱测试从同级 `session.local.json` 改为临时项目根下的 `runtime.local.json`：

```python
def make_project_with_dashboard_config(tmp_path):
    project_root = tmp_path / "project"
    write_json(project_root / "config/dashboard/session.json", {"schema_version": 2})
    return project_root


def test_load_dashboard_config_merges_runtime_session_overrides(tmp_path, monkeypatch):
    project_root = make_project_with_dashboard_config(tmp_path)
    write_json(
        project_root / "config" / "runtime.local.json",
        {"dashboard": {"session_overrides": {"collection_database_lock_name": "dashboard_dev"}}},
    )
    monkeypatch.setattr(config_loader, "PROJECT_ROOT", project_root)

    config, _ = dashboard_v2_trigger.load_dashboard_config(project_root / "config/dashboard/session.json")

    assert config["collection_database_lock_name"] == "dashboard_dev"
```

对于 `tests/test_tencent_sheet_service.py` 和 `tests/test_tencent_smartbook_service.py` 的临时目录夹具，将测试凭据直接写入临时基础 JSON；这些文件不在仓库受管路径内，不应再模拟侧车覆盖。保留服务级测试只验证下载行为。为 `utils.config_loader` 的中央覆盖测试承担合并行为断言。

将 `tests/test_login_service.py` 的 monkeypatch 目标改为 `load_json_with_runtime_override`，继续断言内存配置路径不触发磁盘读取。

- [ ] **Step 2: 运行测试，确认旧导入或旧侧车断言失败**

Run:

```powershell
python -m pytest -p no:cacheprovider tests/test_dashboard_v2_trigger.py tests/test_tencent_sheet_service.py tests/test_tencent_smartbook_service.py tests/test_login_service.py -q
```

Expected: FAIL，当前服务仍导入 `load_json_with_local_override`，且旧测试仍假设同级 `.local.json` 生效。

- [ ] **Step 3: 替换所有运行时导入与文案**

在每个列出的消费者中替换：

```python
from utils.config_loader import load_json_with_runtime_override

payload, resolved = load_json_with_runtime_override(config_path)
```

不得保留 `load_json_with_local_override` 兼容别名，以确保搜索结果能证明运行时不再读取侧车文件。

将腾讯凭据缺失错误及 dry-run 来源统一改为中央位置：

```python
raise ValueError(
    f"腾讯文档凭据缺少字段: {missing}，请检查 "
    "config/runtime.local.json 的 module_overrides.tencent_docs"
)

payload["request_summary"] = {
    "source": source,
    "sheet_count": len(report.get("sheets") or []),
    "credential_source": "config/runtime.local.json:module_overrides.tencent_docs",
}
```

- [ ] **Step 4: 运行消费者回归测试，确认通过**

Run:

```powershell
python -m pytest -p no:cacheprovider tests/test_runtime_local_overrides.py tests/test_dashboard_v2_trigger.py tests/test_tencent_sheet_service.py tests/test_tencent_smartbook_service.py tests/test_login_service.py tests/test_business_run_alert_service.py -q
```

Expected: PASS，运行时消费者统一使用中央加载器，临时夹具不依赖侧车文件，错误提示不再建议创建模块级本地文件。

- [ ] **Step 5: 提交消费者改造**

```powershell
git add backend/services/starter_template.py flows/notify_single_flow.py flows/session_keeper_flow.py services/business_run_alert_service.py services/dashboard_v2_trigger.py services/login_service.py services/session_business_failure_service.py services/tencent_sheet_service.py services/method_service.py tests/test_dashboard_v2_trigger.py tests/test_tencent_sheet_service.py tests/test_tencent_smartbook_service.py tests/test_login_service.py
git commit -m "refactor: load local overrides from runtime config"
```

### Task 3: 安全迁移与删除旧侧车文件的命令

**Files:**
- Create: `scripts/migrate_local_json_to_runtime.py`
- Create: `tests/test_migrate_local_json_to_runtime.py`
- Modify: `utils/config_loader.py`

**Interfaces:**
- Consumes: Task 1 的 `deep_merge()` 与 `validate_runtime_local_overrides()`。
- Produces: `python scripts/migrate_local_json_to_runtime.py [--project-root PATH] [--apply] [--remove-legacy]`。
- Produces: 仅输出相对源路径、目标分区和冲突路径的迁移结果；不输出私有字段值。

- [ ] **Step 1: 写入迁移命令的失败测试**

使用临时项目构造 `session.local.json`、`login_config.local.json` 和一个中文任务 `*.local.json`，并通过子进程调用脚本：

```python
def test_preview_lists_mappings_without_writing_or_deleting(tmp_path):
    project_root = make_project_with_legacy_local_files(tmp_path)

    result = run_migration(project_root)

    assert result.returncode == 0
    assert "PREVIEW config/modules/login_config.local.json -> module_overrides.login_config" in result.stdout
    assert "legacy-secret" not in result.stdout
    assert (project_root / "config/modules/login_config.local.json").exists()
    assert read_json(project_root / "config/runtime.local.json")["module_overrides"] == {}


def test_conflicting_value_refuses_apply_and_keeps_files(tmp_path):
    project_root = make_project_with_conflicting_runtime_and_legacy_value(tmp_path)

    result = run_migration(project_root, "--apply")

    assert result.returncode == 2
    assert "CONFLICT module_overrides.login_config.credentials.username" in result.stdout
    assert "legacy-user" not in result.stdout
    assert (project_root / "config/modules/login_config.local.json").exists()
```

补充成功 `--apply`、成功 `--apply --remove-legacy`、`--remove-legacy` 未配合 `--apply` 失败、嵌套对象无冲突合并、无效 JSON 失败且不写入的案例。

- [ ] **Step 2: 运行测试，确认命令不存在**

Run:

```powershell
python -m pytest -p no:cacheprovider tests/test_migrate_local_json_to_runtime.py -q
```

Expected: FAIL，缺少迁移脚本。

- [ ] **Step 3: 实现预览、原子写入、冲突失败和显式删除**

脚本使用 `argparse`，默认执行预览；仅接受以下参数：

```python
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
PROJECT_ROOT = REPO_ROOT

parser.add_argument("--project-root", type=Path, default=PROJECT_ROOT)
parser.add_argument("--apply", action="store_true")
parser.add_argument("--remove-legacy", action="store_true")
args = parser.parse_args()
if args.remove_legacy and not args.apply:
    parser.error("--remove-legacy 只能与 --apply 一起使用")
```

按以下固定映射枚举已有文件：

```python
LEGACY_MAPPINGS = (
    (Path("config/dashboard/session.local.json"), ("dashboard", "session_overrides")),
)

def legacy_sources(project_root: Path) -> list[tuple[Path, tuple[str, str]]]:
    sources = [(project_root / path, target) for path, target in LEGACY_MAPPINGS]
    sources.extend(
        (path, ("module_overrides", path.stem.removesuffix(".local")))
        for path in sorted((project_root / "config/modules").glob("*.local.json"))
    )
    sources.extend(
        (path, ("task_overrides", path.stem.removesuffix(".local")))
        for path in sorted((project_root / "config/tasks").glob("*.local.json"))
    )
    return [(path, target) for path, target in sources if path.is_file()]
```

实现递归 `merge_without_conflicts(existing, incoming, path)`：缺失键写入，相同标量保留，不同标量或对象与标量冲突记录完整键路径并拒绝整个 `--apply`。所有冲突检查完成后才通过临时同目录文件和 `Path.replace()` 原子替换中央 JSON。写入后调用 `validate_runtime_local_overrides()`；只有校验通过且传入 `--remove-legacy` 时才删除源文件。输出格式只能使用路径与目标分区，例如 `APPLIED config/dashboard/session.local.json -> dashboard.session_overrides`。

- [ ] **Step 4: 运行迁移测试，确认通过**

Run:

```powershell
python -m pytest -p no:cacheprovider tests/test_migrate_local_json_to_runtime.py tests/test_runtime_local_overrides.py -q
```

Expected: PASS，预览无写入，冲突不泄露且不改变文件，成功迁移经验证后才删除旧文件。

- [ ] **Step 5: 提交迁移工具**

```powershell
git add scripts/migrate_local_json_to_runtime.py tests/test_migrate_local_json_to_runtime.py utils/config_loader.py
git commit -m "feat: migrate local JSON into runtime config"
```

### Task 4: 更新运行说明、部署手册与配置边界文档

**Files:**
- Modify: `README.md:81-87,102-161,194-220`
- Modify: `PROJECT_GUIDE.md:80-101,216-224,260-265`
- Modify: `config/modules/README.md:15-20`
- Modify: `docs/production-deployment-runbook.md:1-10,66-76`
- Modify: `scripts/README.md`
- Modify: `tests/test_development_environment_contract.py:110-161,1007-1054`

**Interfaces:**
- Consumes: Task 1 的中央结构和 Task 3 的迁移命令。
- Produces: 所有活跃运维文档只指向 `config/runtime.local.json`；用户可按文档完成预览、写入、删除和启动前检查。

- [ ] **Step 1: 写入文档契约的失败断言**

在环境契约测试中断言模板含六个有序顶级分区，README 与生产手册含迁移命令且不再把模块侧车文件描述为凭据来源：

```python
def test_docs_describe_runtime_local_json_as_the_only_active_json_source():
    documents = [
        (ROOT / "README.md").read_text(encoding="utf-8"),
        (ROOT / "docs/production-deployment-runbook.md").read_text(encoding="utf-8"),
    ]

    for source in documents:
        assert "migrate_local_json_to_runtime.py --apply --remove-legacy" in source
        assert "config/modules/tencent_docs.local.json" not in source
        assert "config/runtime.local.json" in source
```

历史变更记录可以保留其当时的文件名；断言只覆盖面向当前操作的章节，避免改写历史事实。

- [ ] **Step 2: 运行文档契约，确认当前文档失败**

Run:

```powershell
python -m pytest -p no:cacheprovider tests/test_development_environment_contract.py -q
```

Expected: FAIL，README、模块说明和生产手册仍引用模块级 `*.local.json`。

- [ ] **Step 3: 按统一入口重写活跃操作说明**

README 增加迁移顺序，使用以下不泄露凭据的命令：

```powershell
python scripts/migrate_local_json_to_runtime.py
python scripts/migrate_local_json_to_runtime.py --apply
python scripts/migrate_local_json_to_runtime.py --apply --remove-legacy
```

将腾讯文档、登录和企业微信凭据说明改为 `module_overrides.tencent_docs`、
`module_overrides.login_config`、`module_overrides.wecom_sender`；将驾驶舱锁说明改为
`dashboard.session_overrides`；将任务本地差异说明改为 `task_overrides.<任务文件名>`。

更新 `config/modules/README.md`，说明模块基础配置不含真实凭据，私有模块字段统一放在
`runtime.local.json.module_overrides`。更新生产手册的上线前检查与推荐顺序，要求先运行
迁移预览、解决冲突、执行 `--apply --remove-legacy`，再启动服务。更新 PROJECT_GUIDE 的
当前配置规范并新增本次变更记录；保留过去版本的历史描述。更新脚本目录说明，列出迁移
脚本为一次性本地配置工具。

- [ ] **Step 4: 运行文档与 PowerShell 契约，确认通过**

Run:

```powershell
python -m pytest -p no:cacheprovider tests/test_development_environment_contract.py -q
pwsh -NoProfile -Command "$tokens=$null; $errors=$null; [System.Management.Automation.Language.Parser]::ParseFile((Resolve-Path 'scripts/lib/runtime_config.ps1'), [ref]$tokens, [ref]$errors) | Out-Null; if ($errors) { $errors | ForEach-Object { $_.ToString() }; exit 1 }"
```

Expected: PASS，文档仅将中央文件说明为活跃本地 JSON 来源，PowerShell 解析无语法错误。

- [ ] **Step 5: 提交文档与契约变更**

```powershell
git add README.md PROJECT_GUIDE.md config/modules/README.md docs/production-deployment-runbook.md scripts/README.md tests/test_development_environment_contract.py
git commit -m "docs: document unified local runtime config"
```

### Task 5: 端到端验证与本机配置迁移

**Files:**
- Modify: 仅由 Task 3 迁移命令更新被忽略的 `config/runtime.local.json`。
- Delete: 仅在成功迁移后由命令删除被忽略的 `config/dashboard/*.local.json`、`config/modules/*.local.json`、`config/tasks/*.local.json`。

**Interfaces:**
- Consumes: 已通过测试的迁移命令与本机私有配置。
- Produces: 本机仅保留活跃的 `config/runtime.local.json`，不向 Git 暂存任何私有文件。

- [ ] **Step 1: 运行完整自动化回归与静态检查**

Run:

```powershell
python -m pytest -p no:cacheprovider -q
python -m ruff check utils services backend flows scripts tests
git diff --check
```

Expected: 全部 pytest 通过，Ruff 无诊断，`git diff --check` 无输出。

- [ ] **Step 2: 预览本机迁移，不写入或删除文件**

Run:

```powershell
python scripts/migrate_local_json_to_runtime.py
```

Expected: 仅显示旧文件相对路径和目标分区；不显示任何密钥值，且原有侧车文件仍存在。

- [ ] **Step 3: 执行本机迁移并删除已验证侧车文件**

Run:

```powershell
python scripts/migrate_local_json_to_runtime.py --apply --remove-legacy
```

Expected: 每个已迁移文件显示 `APPLIED` 与 `REMOVED`；命令退出码为 0。若出现冲突，停止并仅报告冲突路径，手工处理后从预览重新开始，绝不使用覆盖选项绕过冲突。

- [ ] **Step 4: 验证本机只剩中央运行配置并可解析**

Run:

```powershell
Get-ChildItem config -Recurse -Filter *.local.json | Where-Object { $_.FullName -ne (Resolve-Path config/runtime.local.json).Path }
pwsh -NoProfile -Command ". ./scripts/lib/runtime_config.ps1; Import-RuntimeConfig | Out-Null"
```

Expected: 第一条命令无输出；第二条命令退出码为 0 且不显示配置值。

- [ ] **Step 5: 提交代码，不暂存私有配置**

```powershell
git status --short
git add utils/config_loader.py scripts/migrate_local_json_to_runtime.py scripts/lib/runtime_config.ps1 config/runtime.local.example.json backend/services/starter_template.py flows/notify_single_flow.py flows/session_keeper_flow.py services/business_run_alert_service.py services/dashboard_v2_trigger.py services/login_service.py services/session_business_failure_service.py services/tencent_sheet_service.py services/method_service.py README.md PROJECT_GUIDE.md config/modules/README.md docs/production-deployment-runbook.md scripts/README.md tests/test_runtime_local_overrides.py tests/test_migrate_local_json_to_runtime.py tests/test_dashboard_v2_trigger.py tests/test_tencent_sheet_service.py tests/test_tencent_smartbook_service.py tests/test_login_service.py tests/test_development_environment_contract.py
git commit -m "feat: unify local runtime configuration"
```

Expected: 暂存列表不包含 `config/runtime.local.json` 或任何旧 `*.local.json`；若前序任务已分步提交，最后一条提交只包含遗漏的收尾文件。
