import json
from pathlib import Path
import shutil
import subprocess
import tomllib

import yaml


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = [
    ROOT / "scripts" / "setup_windows_env.ps1",
    ROOT / "scripts" / "lib" / "start_web.ps1",
    ROOT / "scripts" / "lib" / "prefect_start.ps1",
    ROOT / "scripts" / "lib" / "prefect_stop.ps1",
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
    helper = ROOT / "scripts" / "lib" / "python_env.ps1"
    assert "function Get-ProjectPython" in helper.read_text(encoding="utf-8")

    for relative_path in (
        "scripts/lib/start_web.ps1",
        "scripts/lib/prefect_start.ps1",
        "scripts/lib/prefect_stop.ps1",
        "scripts/dev/env.ps1",
    ):
        assert "python_env.ps1" in (ROOT / relative_path).read_text(encoding="utf-8")


def test_prefect_stop_keeps_legacy_kill_switch_and_public_callers_forward_it():
    stop_source = (ROOT / "scripts" / "lib" / "prefect_stop.ps1").read_text(encoding="utf-8")
    public_source = (ROOT / "scripts" / "legacy" / "public_stack.ps1").read_text(encoding="utf-8")
    wrapper_source = (ROOT / "scripts" / "stop_public_stack.ps1").read_text(encoding="utf-8")

    assert "[switch]$KillAutoNotifyPython = $true" in stop_source
    assert "if ($KillAutoNotifyPython)" in stop_source
    assert "-KillAutoNotifyPython:$KillAutoNotifyPython" in public_source
    assert "legacy\\stop_public_stack.ps1" in wrapper_source


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


def test_single_local_environment_file_is_documented_and_loaded_first():
    template = ROOT / "scripts" / "environment.local.example.ps1"
    prefect_source = (ROOT / "scripts" / "lib" / "prefect_env_prod.ps1").read_text(encoding="utf-8")
    mysql_source = (ROOT / "scripts" / "tools" / "dashboard" / "mysql_env.ps1").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")

    source = template.read_text(encoding="utf-8")
    assert "AUTO_NOTIFY_PREFECT_DATABASE_URL" in source
    assert "DASHBOARD_MYSQL_HOST" in source
    assert "environment.local.ps1" in prefect_source
    assert "environment.local.ps1" in mysql_source
    assert "scripts/environment.local.ps1" in readme
    assert "scripts/environment.local.ps1" in gitignore


def test_runtime_json_template_is_ignored_and_loader_exports_shared_environment():
    template = ROOT / "config" / "runtime.local.example.json"
    loader = ROOT / "scripts" / "lib" / "runtime_config.ps1"
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")

    config = json.loads(template.read_text(encoding="utf-8"))
    loader_source = loader.read_text(encoding="utf-8")

    assert config["prefect"]["postgres"]["url"]
    assert config["dashboard"]["mysql"]["host"]

    assert "config/runtime.local.json" in gitignore
    assert "function Import-RuntimeConfig" in loader_source
    for variable in (
        "AUTO_NOTIFY_PREFECT_DATABASE_URL",
        "DASHBOARD_MYSQL_HOST",
        "DASHBOARD_MYSQL_PORT",
        "DASHBOARD_MYSQL_DATABASE",
        "DASHBOARD_MYSQL_USER",
        "DASHBOARD_MYSQL_PASSWORD",
        "PREFECT_API_URL",
        "PREFECT_WORK_POOL_NAME",
    ):
        assert variable in loader_source


def test_runtime_json_template_exports_three_pool_topology_and_runtime_root():
    template = json.loads(
        (ROOT / "config" / "runtime.local.example.json").read_text(encoding="utf-8")
    )
    runtime = template["runtime"]

    assert runtime["root"] == r"C:\AutoNotifyRuntime"
    assert runtime["scheduled_notify_grace_seconds"] == 600
    assert runtime["session_freshness_seconds"] == 180
    assert runtime["work_pools"] == {
        "session": {"name": "windows-session-pool", "limit": 1},
        "dashboard": {"name": "windows-dashboard-pool", "limit": 4},
        "notify": {"name": "windows-notify-pool", "limit": 6},
    }


def test_runtime_loader_exports_three_pool_environment_contract():
    source = (ROOT / "scripts" / "lib" / "runtime_config.ps1").read_text(
        encoding="utf-8"
    )
    for variable in (
        "AUTO_NOTIFY_RUNTIME_ROOT",
        "PREFECT_SESSION_POOL_NAME",
        "PREFECT_SESSION_POOL_LIMIT",
        "PREFECT_DASHBOARD_POOL_NAME",
        "PREFECT_DASHBOARD_POOL_LIMIT",
        "PREFECT_NOTIFY_POOL_NAME",
        "PREFECT_NOTIFY_POOL_LIMIT",
        "AUTO_NOTIFY_SCHEDULED_NOTIFY_GRACE_SECONDS",
        "AUTO_NOTIFY_SESSION_FRESHNESS_SECONDS",
    ):
        assert variable in source


def _run_runtime_config_import(config_path):
    powershell = shutil.which("pwsh") or shutil.which("powershell")
    if not powershell:
        raise AssertionError("PowerShell is required to verify runtime configuration validation")

    loader = (ROOT / "scripts" / "lib" / "runtime_config.ps1").as_posix()
    command = f"""
$ErrorActionPreference = 'Stop'
. '{loader}'
Import-RuntimeConfig -ConfigPath '{config_path.as_posix()}' | Out-Null
"""
    return subprocess.run(
        [powershell, "-NoProfile", "-Command", command],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def test_runtime_loader_rejects_fractional_positive_integer_value(tmp_path):
    config = json.loads(
        (ROOT / "config" / "runtime.local.example.json").read_text(encoding="utf-8")
    )
    config["runtime"]["work_pools"]["session"]["limit"] = 1.5
    config_path = tmp_path / "runtime.fractional.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")

    result = _run_runtime_config_import(config_path)

    assert result.returncode != 0
    assert "运行配置必须为正整数: runtime.work_pools.session.limit" in (
        result.stdout + result.stderr
    )


def test_runtime_loader_rejects_nonnumeric_positive_integer_value(tmp_path):
    config = json.loads(
        (ROOT / "config" / "runtime.local.example.json").read_text(encoding="utf-8")
    )
    config["runtime"]["work_pools"]["session"]["limit"] = "one"
    config_path = tmp_path / "runtime.nonnumeric.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")

    result = _run_runtime_config_import(config_path)

    assert result.returncode != 0
    assert "运行配置必须为正整数: runtime.work_pools.session.limit" in (
        result.stdout + result.stderr
    )


def test_runtime_json_is_preferred_and_legacy_local_files_remain_fallbacks():
    powershell = shutil.which("pwsh") or shutil.which("powershell")
    if not powershell:
        raise AssertionError("PowerShell is required to verify runtime configuration exports")

    config_path = ROOT / "config" / "runtime.local.json"
    unified_path = ROOT / "scripts" / "environment.local.ps1"
    legacy_path = ROOT / "scripts" / "prefect_env_prod.local.ps1"
    dashboard_legacy_path = ROOT / "scripts" / "tools" / "dashboard" / "mysql_env.v2.local.ps1"
    original_files = {
        path: path.read_bytes() if path.exists() else None
        for path in (config_path, unified_path, legacy_path, dashboard_legacy_path)
    }
    json_config = {
        "prefect": {
            "postgres": {"url": "postgresql+asyncpg://json:json@db.example:5432/prefect"},
            "api_url": "http://json.example/api",
        },
        "dashboard": {
            "mysql": {
                "host": "json-db.example",
                "port": 3307,
                "database": "json_dashboard",
                "user": "json_user",
                "password": "json_password",
            }
        },
        "runtime": {
            "root": r"C:\JsonRuntime",
            "scheduled_notify_grace_seconds": 601,
            "session_freshness_seconds": 181,
            "work_pools": {
                "session": {"name": "windows-session-pool", "limit": 2},
                "dashboard": {"name": "windows-dashboard-pool", "limit": 5},
                "notify": {"name": "windows-notify-pool", "limit": 7},
            },
        },
    }
    unified_source = """\
$env:AUTO_NOTIFY_PREFECT_DATABASE_URL = 'postgresql+asyncpg://unified:unified@db.example:5432/prefect'
$env:DASHBOARD_MYSQL_HOST = 'unified-db.example'
$env:PREFECT_API_URL = 'http://unified.example/api'
$env:PREFECT_WORK_POOL_NAME = 'unified-pool'
"""
    legacy_source = unified_source.replace("unified", "legacy")
    command = """
$ErrorActionPreference = 'Stop'
. '{script}'
[pscustomobject]@{{
  database_url = $env:AUTO_NOTIFY_PREFECT_DATABASE_URL
  mysql_host = $env:DASHBOARD_MYSQL_HOST
  mysql_port = $env:DASHBOARD_MYSQL_PORT
  mysql_database = $env:DASHBOARD_MYSQL_DATABASE
  mysql_user = $env:DASHBOARD_MYSQL_USER
  mysql_password = $env:DASHBOARD_MYSQL_PASSWORD
  prefect_api_url = $env:PREFECT_API_URL
  runtime_root = $env:AUTO_NOTIFY_RUNTIME_ROOT
  session_pool = $env:PREFECT_SESSION_POOL_NAME
  session_pool_limit = $env:PREFECT_SESSION_POOL_LIMIT
  dashboard_pool = $env:PREFECT_DASHBOARD_POOL_NAME
  dashboard_pool_limit = $env:PREFECT_DASHBOARD_POOL_LIMIT
  notify_pool = $env:PREFECT_NOTIFY_POOL_NAME
  notify_pool_limit = $env:PREFECT_NOTIFY_POOL_LIMIT
  scheduled_notify_grace_seconds = $env:AUTO_NOTIFY_SCHEDULED_NOTIFY_GRACE_SECONDS
  session_freshness_seconds = $env:AUTO_NOTIFY_SESSION_FRESHNESS_SECONDS
  work_pool = $env:PREFECT_WORK_POOL_NAME
}} | ConvertTo-Json -Compress
""".format(script=(ROOT / "scripts" / "dev" / "env.ps1").as_posix())

    def run_environment():
        result = subprocess.run(
            [powershell, "-NoProfile", "-Command", command],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=True,
        )
        return json.loads(result.stdout.splitlines()[-1])

    try:
        config_path.write_text(json.dumps(json_config), encoding="utf-8")
        unified_path.write_text(unified_source, encoding="utf-8")
        legacy_path.write_text(legacy_source, encoding="utf-8")
        dashboard_legacy_path.write_text(
            "$env:DASHBOARD_MYSQL_HOST = 'legacy-dashboard.example'\n",
            encoding="utf-8",
        )
        assert run_environment() == {
            "database_url": json_config["prefect"]["postgres"]["url"],
            "mysql_host": json_config["dashboard"]["mysql"]["host"],
            "mysql_port": str(json_config["dashboard"]["mysql"]["port"]),
            "mysql_database": json_config["dashboard"]["mysql"]["database"],
            "mysql_user": json_config["dashboard"]["mysql"]["user"],
            "mysql_password": json_config["dashboard"]["mysql"]["password"],
            "prefect_api_url": json_config["prefect"]["api_url"],
            "runtime_root": json_config["runtime"]["root"],
            "session_pool": json_config["runtime"]["work_pools"]["session"]["name"],
            "session_pool_limit": str(
                json_config["runtime"]["work_pools"]["session"]["limit"]
            ),
            "dashboard_pool": json_config["runtime"]["work_pools"]["dashboard"]["name"],
            "dashboard_pool_limit": str(
                json_config["runtime"]["work_pools"]["dashboard"]["limit"]
            ),
            "notify_pool": json_config["runtime"]["work_pools"]["notify"]["name"],
            "notify_pool_limit": str(
                json_config["runtime"]["work_pools"]["notify"]["limit"]
            ),
            "scheduled_notify_grace_seconds": str(
                json_config["runtime"]["scheduled_notify_grace_seconds"]
            ),
            "session_freshness_seconds": str(
                json_config["runtime"]["session_freshness_seconds"]
            ),
            "work_pool": json_config["runtime"]["work_pools"]["notify"]["name"],
        }

        config_path.unlink()
        assert run_environment()["database_url"].startswith("postgresql+asyncpg://unified:")

        unified_path.unlink()
        assert run_environment()["database_url"].startswith("postgresql+asyncpg://legacy:")
    finally:
        for path, content in original_files.items():
            if content is None:
                path.unlink(missing_ok=True)
            else:
                path.write_bytes(content)


def test_prefect_and_web_entry_points_preserve_runtime_json_defaults():
    prefect_start = (ROOT / "scripts" / "lib" / "prefect_start.ps1").read_text(encoding="utf-8")
    start_web = (ROOT / "scripts" / "lib" / "start_web.ps1").read_text(encoding="utf-8")

    assert '"runtime_config.ps1"' in prefect_start
    assert "Import-ProjectRuntimeConfig" in prefect_start
    assert "$ApiUrl = $env:PREFECT_API_URL" in prefect_start
    assert "$WorkPool = $env:PREFECT_WORK_POOL_NAME" in prefect_start

    assert '"runtime_config.ps1"' in start_web
    assert "Import-ProjectRuntimeConfig" in start_web
    assert "$PrefectApiUrl = $env:PREFECT_API_URL" in start_web


def test_prefect_helpers_use_configured_database_url_directly():
    prefect_start = (ROOT / "scripts" / "lib" / "prefect_start.ps1").read_text(encoding="utf-8")
    prefect_env = (ROOT / "scripts" / "lib" / "prefect_env_prod.ps1").read_text(encoding="utf-8")
    dev_env = (ROOT / "scripts" / "dev" / "env.ps1").read_text(encoding="utf-8")

    for source in (prefect_start, prefect_env, dev_env):
        assert "/prefect_dev" not in source
        assert "AUTO_NOTIFY_PREFECT_DATABASE_URL" in source

    assert "$DatabaseUrl = $env:AUTO_NOTIFY_PREFECT_DATABASE_URL" in prefect_start
    assert "$DatabaseUrl = $SourceDatabaseUrl" in prefect_env
    assert "$env:AUTO_NOTIFY_PREFECT_DEV_DATABASE_URL = $prodUrl" in dev_env


def test_direct_database_documentation_uses_the_configured_database_name():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    environment_template = (ROOT / "scripts" / "environment.local.example.ps1").read_text(encoding="utf-8")

    assert "自动派生开发库" not in readme
    assert "prefect_test" in environment_template
    assert "directly to Prefect" in environment_template


def test_prefect_runtime_uses_a_fastapi_release_compatible_with_prefect_3_7():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    dependencies = project["project"]["dependencies"]

    assert "prefect==3.7.0" in dependencies
    assert "fastapi>=0.110.0,<0.116" in dependencies


def test_prefect_deployments_are_partitioned_across_three_pools():
    config = yaml.safe_load((ROOT / "prefect.yaml").read_text(encoding="utf-8"))
    pools = {row["name"]: row["work_pool"]["name"] for row in config["deployments"]}

    assert pools["session-keeper"] == "windows-session-pool"
    assert pools["notify-daily"] == "windows-notify-pool"
    for name in (
        "dashboard-collection",
        "dashboard-daily-acc",
        "dashboard-monthly",
        "dashboard-indicator-sync",
        "dashboard-v2-partition-maintenance",
    ):
        assert pools[name] == "windows-dashboard-pool"


def test_notify_single_deployment_uses_notify_pool():
    config = yaml.safe_load(
        (ROOT / "deployments" / "notify_single_deployment.yaml").read_text(encoding="utf-8")
    )

    assert config["work_pool"] == {
        "name": "windows-notify-pool",
        "work_queue_name": "default",
    }


def test_prefect_deploys_session_keeper_on_fixed_quarter_hours():
    prefect_config = yaml.safe_load(
        (ROOT / "prefect.yaml").read_text(encoding="utf-8")
    )
    keeper_deployments = [
        deployment
        for deployment in prefect_config["deployments"]
        if deployment["name"] == "session-keeper"
    ]

    assert keeper_deployments == [
        {
            "name": "session-keeper",
            "entrypoint": "flows/session_keeper_flow.py:session_keeper_flow",
            "parameters": {"config_path": "config/modules/session_keeper.json"},
            "schedules": [
                {
                    "cron": "*/15 * * * *",
                    "timezone": "Asia/Shanghai",
                    "active": True,
                }
            ],
            "work_pool": {
                "name": "windows-session-pool",
                "work_queue_name": "default",
            },
        }
    ]


def test_runtime_start_queues_initial_session_keeper_run_after_worker_start():
    source = (ROOT / "scripts" / "run.ps1").read_text(encoding="utf-8")

    worker_command = """\
& (Join-Path $PSScriptRoot "lib\\prefect_start.ps1") `
    -Mode worker `
    -Detached `
    -ApiUrl $ApiUrl `
    -WorkPool $WorkPool `
    -UseSqliteDebug:$UseSqliteDebug
"""
    keeper_command = 'prefect deployment run "session-keeper-flow/session-keeper"'
    web_marker = "if (-not $SkipWeb)"
    keeper_line = next(line for line in source.splitlines() if keeper_command in line)
    worker_command_end = source.index(worker_command) + len(worker_command)

    assert source.count(keeper_command) == 1
    assert worker_command_end <= source.index(keeper_command)
    assert source.index(keeper_command) < source.index(web_marker)
    assert "--watch" not in keeper_line


def test_unified_runtime_entry_points_start_services_without_running_flows():
    run_script = ROOT / "scripts" / "run.ps1"
    stop_script = ROOT / "scripts" / "stop.ps1"
    status_script = ROOT / "scripts" / "status.ps1"

    for script in (run_script, stop_script, status_script):
        assert script.is_file(), f"missing unified entry point: {script.name}"

    source = run_script.read_text(encoding="utf-8").lower()
    assert "lib\\runtime_config.ps1" in source
    assert "import-projectruntimeconfig" in source
    assert "prefect_start.ps1" in source
    assert "-mode server" in source
    assert "prefect deploy --all" in source
    assert "-mode worker" in source
    assert "start_web.ps1" in source
    assert "prefect flow run" not in source
    assert "flow run" not in source

    assert source.index("-mode server") < source.index("prefect deploy --all")
    assert source.index("prefect deploy --all") < source.index("-mode worker")


def test_prefect_worker_start_has_no_embedded_dashboard_cleanup():
    source = (ROOT / "scripts" / "lib" / "prefect_start.ps1").read_text(
        encoding="utf-8"
    )

    assert "Prepare-DashboardWorkerStart" not in source
    assert "set_flow_run_state" not in source


def test_deprecated_scheduled_backlog_deletion_script_is_retired():
    retired = ROOT / "scripts" / "dev" / "clear_scheduled_backlog.ps1"
    assert not retired.exists()

    public_sources = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (ROOT / "scripts").rglob("*.ps1")
    )
    assert "clear_scheduled_backlog.ps1" not in public_sources


def test_legacy_dev_entry_points_only_forward_to_unified_scripts():
    expected_targets = {
        "scripts/dev/start.ps1": "run.ps1",
        "scripts/dev/stop.ps1": "stop.ps1",
        "scripts/dev/status.ps1": "status.ps1",
    }

    for relative_path, target in expected_targets.items():
        source = (ROOT / relative_path).read_text(encoding="utf-8")
        assert target in source
        assert "prefect deploy --all" not in source.lower()
        assert "prefect flow run" not in source.lower()


def test_dashboard_low_frequency_tools_are_archived_outside_lifecycle_scripts():
    tools_dir = ROOT / "scripts" / "tools" / "dashboard"
    for name in (
        "import_areas.py",
        "export_areas.py",
        "import_metric_targets.py",
        "export_v2_migration_bundle.py",
        "v2_cutover_audit.py",
    ):
        assert (tools_dir / name).is_file(), f"missing archived tool: {name}"

    for relative_path in (
        "scripts/run.ps1",
        "scripts/stop.ps1",
        "scripts/status.ps1",
        "scripts/lib/prefect_start.ps1",
        "scripts/lib/start_web.ps1",
    ):
        assert "scripts\\dashboard" not in (ROOT / relative_path).read_text(encoding="utf-8")


def test_docs_describe_json_lifecycle_entry_points_and_cron_safety():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    guide = (ROOT / "PROJECT_GUIDE.md").read_text(encoding="utf-8")

    for document in (readme, guide):
        assert "config/runtime.local.json" in document
        assert "scripts/run.ps1" in document
        assert "scripts/stop.ps1" in document
        assert "scripts/status.ps1" in document
        assert "Cron" in document
        assert "不会自动" in document


def test_script_root_contains_only_public_entry_points_or_compatibility_wrappers():
    root = ROOT / "scripts"
    for name in (
        "python_env.ps1",
        "prefect_env_prod.ps1",
        "prefect_start.ps1",
        "prefect_stop.ps1",
        "start_web.ps1",
        "public_stack.ps1",
    ):
        assert not (root / name).exists(), f"internal script remains at root: {name}"

    for relative_path, target in (
        ("scripts/start_public_stack.ps1", "legacy\\start_public_stack.ps1"),
        ("scripts/stop_public_stack.ps1", "legacy\\stop_public_stack.ps1"),
    ):
        assert target in (ROOT / relative_path).read_text(encoding="utf-8")
