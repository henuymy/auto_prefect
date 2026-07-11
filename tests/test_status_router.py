from __future__ import annotations

from backend.routers import status


def test_status_includes_dashboard_mysql(monkeypatch):
    monkeypatch.setattr(
        status.prefect_runner,
        "check_prefect_status",
        lambda: {"ok": True},
    )
    monkeypatch.setattr(
        status,
        "check_dashboard_mysql",
        lambda: {
            "ok": False,
            "configured": False,
            "message": "未配置",
        },
    )

    result = status.get_status()

    assert result["dashboard_mysql"]["configured"] is False


def test_status_includes_latest_collection_run_when_mysql_is_ready(monkeypatch):
    class RunStore:
        closed = False

        def latest(self):
            return {"batch_no": "dashboard-test"}

        def close(self):
            self.closed = True

    run_store = RunStore()
    monkeypatch.setattr(
        status.prefect_runner,
        "check_prefect_status",
        lambda: {"ok": True},
    )
    monkeypatch.setattr(
        status,
        "check_dashboard_mysql",
        lambda: {
            "ok": True,
            "configured": True,
            "message": "ok",
        },
    )
    monkeypatch.setattr(
        status,
        "MySQLV2CollectionRunStore",
        lambda: run_store,
    )

    result = status.get_status()

    assert result["dashboard_mysql"]["collection_run"]["schema_ready"] is True
    assert result["dashboard_mysql"]["collection_run"]["latest"]["batch_no"] == "dashboard-test"
    assert run_store.closed is True
