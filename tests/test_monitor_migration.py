from pathlib import Path


def test_monitor_migration_quotes_mysql_trigger_column() -> None:
    source = Path("migrations/dashboard_v2/versions/20260718_0002_monitor_center.py").read_text(encoding="utf-8")

    assert "`trigger` VARCHAR(32) NOT NULL" in source


def test_monitor_event_idempotency_migration_depends_on_target_identity() -> None:
    source = Path("migrations/dashboard_v2/versions/20260719_0004_monitor_event_idempotency.py").read_text(encoding="utf-8")

    assert 'down_revision = "20260719_0003"' in source
    assert "state_occurred_at DATETIME(3)" in source
    assert "source_event_id VARCHAR(64)" in source
    assert "uq_monitor_events_source_event_id" in source
