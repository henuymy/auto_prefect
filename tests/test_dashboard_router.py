from __future__ import annotations

from backend.routers import dashboard


class FakeEngine:
    def dispose(self) -> None:
        pass


def test_matrix_route_uses_cache_and_invalidates_on_data_version(monkeypatch):
    dashboard._dashboard_cache.clear()
    dashboard._invalidate_version_state()
    monkeypatch.setattr(dashboard, "_VERSION_STATE_TTL_SECONDS", -1)
    version = {"value": "v1"}
    calls = {"count": 0}

    monkeypatch.setattr(dashboard, "get_dashboard_engine", FakeEngine)
    monkeypatch.setattr(
        dashboard,
        "get_latest_dashboard_run",
        lambda engine: {"latest_run": None, "data_version": version["value"]},
    )

    received_modes = []
    received_scenarios = []

    def load_matrix(engine, **kwargs):
        calls["count"] += 1
        received_modes.append(kwargs["value_mode"])
        received_scenarios.append(kwargs["target_scenario"])
        return {"rows": [], "total": 0, "page": 1, "page_size": 100}

    monkeypatch.setattr(dashboard, "get_dashboard_matrix_page", load_matrix)

    params = {
        "node_type": "CHANNEL",
        "scope_mode": "all",
        "parent_id": None,
        "parent_node_type": None,
        "branch_code": "AQ",
        "indicator_codes": "metric_a",
        "change_window": 60,
        "search": None,
        "sort_indicator": "metric_a",
        "sort_mode": "doneDesc",
        "page": 1,
        "page_size": 100,
        "value_mode": "REALTIME",
        "target_scenario": "NORMAL",
    }
    dashboard.dashboard_matrix(**params)
    dashboard.dashboard_matrix(**params)
    assert calls["count"] == 1

    version["value"] = "v2"
    dashboard.dashboard_matrix(**params)
    assert calls["count"] == 2

    dashboard.dashboard_matrix(**{**params, "value_mode": "REALTIME_ACC"})
    assert calls["count"] == 3
    assert received_modes == ["REALTIME", "REALTIME", "REALTIME_ACC"]
    assert received_scenarios == ["NORMAL", "NORMAL", "NORMAL"]

    dashboard.dashboard_matrix(**{**params, "target_scenario": "PK"})
    assert calls["count"] == 4
    assert received_scenarios[-1] == "PK"


def test_parse_indicator_codes_deduplicates_and_limits():
    codes = ",".join(f"metric_{index}" for index in range(25))
    parsed = dashboard._parse_indicator_codes(f"metric_1,metric_1,{codes}")

    assert parsed is not None
    assert len(parsed) == 20
    assert parsed[0] == "metric_1"


def test_acc_options_route_forwards_pagination(monkeypatch):
    dashboard._dashboard_cache.clear()
    monkeypatch.setattr(dashboard, "get_dashboard_engine", FakeEngine)
    monkeypatch.setattr(dashboard, "get_acc_options", lambda engine, **kw: kw)

    assert dashboard.dashboard_acc_options(page=2, page_size=50) == {
        "page": 2,
        "page_size": 50,
    }
