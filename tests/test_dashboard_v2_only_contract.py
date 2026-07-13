from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIRS = ("backend", "flows", "infrastructure", "models", "services", "tasks", "scripts")
FORBIDDEN_IMPORTS = (
    "infrastructure.dashboard_run_store",
    "models.dashboard_area",
    "models.dashboard_base",
    "models.dashboard_collection_run",
    "models.dashboard_metric",
    "services.dashboard_pipeline",
    "services.dashboard_collection_orchestrator",
    "services.dashboard_collection_service",
    "services.dashboard_metric_store",
    "services.dashboard_query_service",
)


def test_production_python_has_no_v1_dashboard_runtime_or_old_runtime_paths():
    for directory in SOURCE_DIRS:
        for path in (PROJECT_ROOT / directory).rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            assert "runtime/dashboard/" not in text, path
            assert "runtime/browser_session" not in text, path
            assert "runtime/cookies" not in text, path
            assert not any(item in text for item in FORBIDDEN_IMPORTS), path
