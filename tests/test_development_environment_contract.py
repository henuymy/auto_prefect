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


def test_readme_explains_dependency_file_roles():
    source = (ROOT / "README.md").read_text(encoding="utf-8")

    for filename in (
        "pyproject.toml",
        "requirements.txt",
        "requirements.lock",
        "requirements-dev.lock",
    ):
        assert filename in source
