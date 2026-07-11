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
    environment_source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in SCRIPTS
        if path.name != "prefect_stop.ps1"
    )

    assert "auto-notify" not in environment_source
    assert "PYTHONNOUSERSITE" not in source


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


def test_prefect_stop_keeps_legacy_kill_switch_and_public_callers_forward_it():
    stop_source = (ROOT / "scripts" / "prefect_stop.ps1").read_text(encoding="utf-8")
    public_source = (ROOT / "scripts" / "public_stack.ps1").read_text(encoding="utf-8")
    wrapper_source = (ROOT / "scripts" / "stop_public_stack.ps1").read_text(encoding="utf-8")

    assert "[switch]$KillAutoNotifyPython = $true" in stop_source
    assert "if ($KillAutoNotifyPython)" in stop_source
    assert "-KillAutoNotifyPython:$KillAutoNotifyPython" in public_source
    assert "-KillAutoNotifyPython:$KillAutoNotifyPython" in wrapper_source


def test_readme_explains_dependency_file_roles():
    source = (ROOT / "README.md").read_text(encoding="utf-8")

    for filename in (
        "pyproject.toml",
        "requirements.txt",
        "requirements.lock",
        "requirements-dev.lock",
    ):
        assert filename in source

    assert "| `frontend/package.json` | 前端的直接依赖、开发依赖与 npm 脚本声明。 |" in source
    assert "| `frontend/package-lock.json` | npm 解析出的前端精确版本清单，保证不同机器安装结果一致。 |" in source
    assert "传递依赖" in source
    assert "行数通常远多于" in source


def test_readme_declares_base_python_and_nvm_node_20():
    source = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "Conda `base`" in source
    assert "Python 3.13" in source
    assert "NVM" in source
    assert "Node.js 20 LTS" in source


def test_readme_declares_install_commands_and_feature_prerequisites():
    source = (ROOT / "README.md").read_text(encoding="utf-8")

    for command in (
        "conda activate base",
        "python -m pip install -r requirements-dev.lock",
        "nvm install 20.20.1",
        "nvm use 20.20.1",
        "cd frontend",
        "npm install",
    ):
        assert command in source

    assert "MySQL 8（仅驾驶舱功能需要）" in source
    assert "Prefect Server 3.7（仅本地运行 Prefect 服务与调度需要）" in source
    assert "Microsoft Edge（自动登录与网页采集需要）" in source
    assert "涉及 Excel COM 的比对、模板更新和截图功能还需 Microsoft Excel" in source
