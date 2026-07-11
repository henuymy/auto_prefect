# 开发环境与依赖审计实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将本项目的开发运行基线统一为 Conda `base` 和 NVM Node.js 20 LTS，保留所有实际使用的依赖，并用自动化契约与文档说明依赖职责。

**Architecture:** `pyproject.toml` 继续作为唯一的 Python 直接依赖来源，锁文件保留完整可复现的传递依赖。PowerShell 脚本通过一个共享的 Python 解析函数使用当前激活环境或 Conda `base`，不再查找或要求 `auto-notify`；pytest 临时目录限定在项目 `runtime/`，避免用户临时目录权限影响测试。

**Tech Stack:** Python 3.13、Conda base、PowerShell、pytest、pip-tools、Prefect 3.7、FastAPI、React/Vite、Node.js 20 LTS/NVM。

## Global Constraints

- Python 运行时使用 Conda `base` 的 Python 3.13；不创建 `auto-notify` 环境。
- Node.js 由 NVM 管理，版本固定为 Node.js 20 LTS；前端安装使用 `frontend/package-lock.json`。
- 保留 `pyproject.toml` 中所有当前直接依赖，禁止仅按静态 import 删除运行时或脚本依赖。
- `requirements.lock` 与 `requirements-dev.lock` 均由 Python 3.13 的 pip-tools 生成。
- 测试临时目录必须位于 `runtime/test-tmp`，该目录不纳入版本控制。

---

### Task 1: 建立开发环境契约与测试临时目录

**Files:**
- Create: `tests/test_development_environment_contract.py`
- Modify: `pyproject.toml`

**Interfaces:**
- Consumes: `pyproject.toml`、`scripts/setup_windows_env.ps1`、`scripts/start_web.ps1`、`scripts/prefect_start.ps1`、`scripts/prefect_stop.ps1`、`scripts/dev/env.ps1`、`README.md`
- Produces: 对开发环境约定的回归测试与 pytest `--basetemp` 配置。

- [ ] **Step 1: 写出失败的环境契约测试**

```python
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = [
    ROOT / "scripts" / "setup_windows_env.ps1",
    ROOT / "scripts" / "start_web.ps1",
    ROOT / "scripts" / "prefect_start.ps1",
    ROOT / "scripts" / "prefect_stop.ps1",
    ROOT / "scripts" / "dev" / "env.ps1",
]


def test_runtime_scripts_do_not_require_auto_notify_or_disable_user_site():
    source = "\n".join(path.read_text(encoding="utf-8") for path in SCRIPTS)

    assert "auto-notify" not in source
    assert "PYTHONNOUSERSITE" not in source


def test_readme_explains_dependency_file_roles():
    source = (ROOT / "README.md").read_text(encoding="utf-8")

    for filename in ("pyproject.toml", "requirements.txt", "requirements.lock", "requirements-dev.lock"):
        assert filename in source
```

- [ ] **Step 2: 验证测试按预期失败**

Run: `C:\anaconda3\python.exe -m pytest tests/test_development_environment_contract.py -q --basetemp runtime/test-tmp`

Expected: FAIL，提示脚本中仍包含 `auto-notify` 或 `PYTHONNOUSERSITE`，且 README 尚未说明全部依赖文件。

- [ ] **Step 3: 将 pytest 临时目录固定到项目运行目录**

在 `pyproject.toml` 的 `[tool.pytest.ini_options]` 中加入：

```toml
addopts = "--basetemp=runtime/test-tmp"
```

- [ ] **Step 4: 验证 pytest 不再访问用户临时目录**

Run: `C:\anaconda3\python.exe -m pytest tests/test_config_store.py -q`

Expected: 所有该文件测试通过，且不出现 `pytest-of-yuyu` 的权限错误。

- [ ] **Step 5: 提交环境契约测试**

```powershell
git add pyproject.toml tests/test_development_environment_contract.py
git commit -m "test: 固化开发环境约定"
```

### Task 2: 统一 PowerShell Python 解析与 base 环境使用

**Files:**
- Create: `scripts/python_env.ps1`
- Modify: `scripts/setup_windows_env.ps1`
- Modify: `scripts/start_web.ps1`
- Modify: `scripts/prefect_start.ps1`
- Modify: `scripts/prefect_stop.ps1`
- Modify: `scripts/dev/env.ps1`

**Interfaces:**
- Produces: `Get-ProjectPython` 函数，返回当前激活 Conda 环境的 `python.exe`；未激活时返回 Conda base 的 `python.exe`；两者均不可用时返回 `python`。
- Consumes: `$env:CONDA_PREFIX`、`conda info --base`、`Get-Command python`。

- [ ] **Step 1: 让环境契约测试精确要求共享解析器**

在 `tests/test_development_environment_contract.py` 中追加：

```python
def test_runtime_scripts_use_shared_python_resolver():
    helper = ROOT / "scripts" / "python_env.ps1"
    assert "function Get-ProjectPython" in helper.read_text(encoding="utf-8")

    for relative_path in (
        "scripts/start_web.ps1",
        "scripts/prefect_start.ps1",
        "scripts/prefect_stop.ps1",
        "scripts/dev/env.ps1",
    ):
        assert "python_env.ps1" in (ROOT / relative_path).read_text(encoding="utf-8")
```

- [ ] **Step 2: 验证新增断言失败**

Run: `C:\anaconda3\python.exe -m pytest tests/test_development_environment_contract.py -q`

Expected: FAIL，缺少 `scripts/python_env.ps1` 与脚本引用。

- [ ] **Step 3: 实现共享解析器并替换旧环境查找**

创建 `scripts/python_env.ps1`：

```powershell
function Get-ProjectPython {
    if ($env:CONDA_PREFIX) {
        $activePython = Join-Path $env:CONDA_PREFIX "python.exe"
        if (Test-Path -LiteralPath $activePython) {
            return $activePython
        }
    }

    $conda = Get-Command conda -ErrorAction SilentlyContinue
    if ($conda) {
        $basePath = (& conda info --base 2>$null).Trim()
        $basePython = Join-Path $basePath "python.exe"
        if ($basePath -and (Test-Path -LiteralPath $basePython)) {
            return $basePython
        }
    }

    return "python"
}
```

在四个运行脚本中点源该文件并调用 `Get-ProjectPython`。删除所有 `auto-notify` 环境路径、环境名判断、`PYTHONNOUSERSITE=1` 赋值和依赖它的输出文案。将初始化脚本改为默认解析 base Python，安装 `requirements-dev.lock`，不执行 `conda create`，也不写入受版本控制的 `config/modules/autologin.json`。

- [ ] **Step 4: 验证契约与脚本语法**

Run: `C:\anaconda3\python.exe -m pytest tests/test_development_environment_contract.py -q`

Expected: PASS。

Run: `pwsh -NoProfile -Command "[scriptblock]::Create((Get-Content scripts/python_env.ps1 -Raw)) | Out-Null"`

Expected: exit 0。

- [ ] **Step 5: 提交环境脚本统一改造**

```powershell
git add scripts/python_env.ps1 scripts/setup_windows_env.ps1 scripts/start_web.ps1 scripts/prefect_start.ps1 scripts/prefect_stop.ps1 scripts/dev/env.ps1 tests/test_development_environment_contract.py
git commit -m "refactor: 统一开发环境 Python 解析"
```

### Task 3: 更新开发环境与依赖说明

**Files:**
- Modify: `README.md`

**Interfaces:**
- Consumes: `pyproject.toml`、锁文件头、`scripts/python_env.ps1`、`frontend/package.json`。
- Produces: 可直接执行的 base 安装、NVM Node 20、前端安装、质量检查和依赖文件职责说明。

- [ ] **Step 1: 扩展 README 契约测试**

在 `tests/test_development_environment_contract.py` 中追加：

```python
def test_readme_declares_base_python_and_nvm_node_20():
    source = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "Conda `base`" in source
    assert "Python 3.13" in source
    assert "NVM" in source
    assert "Node.js 20 LTS" in source
```

- [ ] **Step 2: 验证 README 断言失败**

Run: `C:\anaconda3\python.exe -m pytest tests/test_development_environment_contract.py -q`

Expected: FAIL，README 仍声明 Python 3.11 与 `auto-notify` 环境。

- [ ] **Step 3: 改写 README 的环境、安装与依赖章节**

README 必须包含以下可执行命令与说明：

```powershell
conda activate base
python -m pip install -r requirements-dev.lock

nvm install 20.20.1
nvm use 20.20.1
cd frontend
npm install
```

并增加“依赖文件职责”表，逐项解释 `pyproject.toml`、`requirements.txt`、`requirements.lock`、`requirements-dev.lock`、`package.json`、`package-lock.json`；说明锁文件包含传递依赖，因此行数远多于直接依赖。保留 Microsoft Edge、Excel、MySQL、Prefect 的完整功能前置条件和不同用途。

- [ ] **Step 4: 验证 README 契约通过**

Run: `C:\anaconda3\python.exe -m pytest tests/test_development_environment_contract.py -q`

Expected: PASS。

- [ ] **Step 5: 提交文档更新**

```powershell
git add README.md tests/test_development_environment_contract.py
git commit -m "docs: 明确 base 开发环境与依赖清单"
```

### Task 4: 验证锁文件与完整开发环境

**Files:**
- Modify only if generated output differs: `requirements.lock`
- Modify only if generated output differs: `requirements-dev.lock`
- Modify only if npm installation updates it: `frontend/package-lock.json`

**Interfaces:**
- Consumes: `pyproject.toml`、`frontend/package.json`、`frontend/package-lock.json`。
- Produces: Python 3.13 与 Node 20 下可安装、可测试、可构建的开发环境证据。

- [ ] **Step 1: 验证 Python 锁文件可安装且依赖一致**

Run: `C:\anaconda3\python.exe -m pip check`

Expected: 项目锁文件中的包没有内部冲突；base 中与项目无关的预装包冲突单独记录，不修改项目锁文件以迎合它们。

- [ ] **Step 2: 使用 Python 3.13 重新生成并比较锁文件**

Run: `C:\anaconda3\python.exe -m piptools compile pyproject.toml --output-file requirements.lock --strip-extras`

Run: `C:\anaconda3\python.exe -m piptools compile pyproject.toml --extra dev --output-file requirements-dev.lock --strip-extras`

Expected: 直接依赖集合不减少；只有解析结果变化时才保留生成差异。

- [ ] **Step 3: 验证 Python 测试与静态检查**

Run: `C:\anaconda3\python.exe -m pytest -q`

Expected: 全部非 MySQL 集成测试通过；`mysql_integration` 标记测试按配置跳过。

Run: `C:\anaconda3\python.exe -m ruff check .`

Expected: exit 0。

- [ ] **Step 4: 验证 Node 20 前端依赖和构建**

Run: `nvm use 20.20.1`

Run: `node --version`

Expected: `v20.20.1`。

Run: `npm install`

Run: `npm run typecheck`

Run: `npm run build`

Expected: 三个 npm 命令 exit 0。

- [ ] **Step 5: 提交生成的锁文件变更**

```powershell
git add requirements.lock requirements-dev.lock frontend/package-lock.json
git commit -m "build: 校验开发依赖锁文件"
```

仅当这些文件确有差异时执行本步骤。
