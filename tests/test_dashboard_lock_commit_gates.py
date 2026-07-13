from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_v2_pipeline_checks_database_lock_after_run_finalization():
    source = _source("services/dashboard_v2_pipeline.py")
    finalize = source.index("finalize_v2_run_in_session(")
    check = source.index("batch.database_lock.assert_held()")

    assert finalize < check


def test_v2_indicator_sync_checks_database_lock_after_success_state():
    source = _source("services/dashboard_v2_indicator_sync.py")
    success = source.index('run.status = "SUCCESS"')
    check = source.index("database_lock.assert_held()")

    assert success < check


def test_batch_contexts_keep_lease_separate_from_serializable_lock_metadata():
    v2 = _source("services/dashboard_v2_batch_runner.py")

    assert "database_lock: DashboardMySQLLockLease" in v2
    assert '"database_lock": database_lock.as_dict()' in v2


def test_v2_batch_defaults_use_controlled_module_output_paths():
    source = _source("services/dashboard_v2_batch_runner.py")

    assert "runtime/dashboard/" not in source
    assert "runtime/modules/dashboard/output/area_anomalies" in source
    assert "runtime/modules/dashboard/output/failure_reports" in source
