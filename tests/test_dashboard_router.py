from __future__ import annotations

from backend.routers import dashboard


class FakeEngine:
    def dispose(self) -> None:
        pass


def test_matrix_route_uses_cache_and_invalidates_on_data_version(monkeypatch):
    dashboard._dashboard_cache.clear()
    version = {"value": "v1"}
    calls = {"count": 0}

    monkeypatch.setattr(dashboard, "get_dashboard_engine", FakeEngine)
    monkeypatch.setattr(
        dashboard,
        "get_latest_dashboard_run",
        lambda engine: {"latest_run": None, "data_version": version["value"]},
    )

    def load_matrix(engine, **kwargs):
        calls["count"] += 1
        return {"rows": [], "total": 0, "page": 1, "page_size": 100}

    monkeypatch.setattr(dashboard, "get_dashboard_matrix_page", load_matrix)

    params = {
        "level_type": "CHANNEL",
        "scope_mode": "all",
        "parent_id": None,
        "parent_level": None,
        "branch_code": "AQ",
        "indicator_codes": "metric_a",
        "change_window": 60,
        "search": None,
        "sort_indicator": "metric_a",
        "sort_mode": "doneDesc",
        "page": 1,
        "page_size": 100,
    }
    dashboard.dashboard_matrix(**params)
    dashboard.dashboard_matrix(**params)
    assert calls["count"] == 1

    version["value"] = "v2"
    dashboard.dashboard_matrix(**params)
    assert calls["count"] == 2


def test_parse_indicator_codes_deduplicates_and_limits():
    codes = ",".join(f"metric_{index}" for index in range(25))
    parsed = dashboard._parse_indicator_codes(f"metric_1,metric_1,{codes}")

    assert parsed is not None
    assert len(parsed) == 20
    assert parsed[0] == "metric_1"
