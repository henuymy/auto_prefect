from __future__ import annotations

from backend.app import app


def _parameter_names(path: str) -> set[str]:
    operation = app.openapi()["paths"][path]["get"]
    return {item["name"] for item in operation["parameters"]}


def test_v2_node_query_parameters_replace_legacy_area_vocabulary():
    matrix = _parameter_names("/api/dashboard/matrix")
    history = _parameter_names("/api/dashboard/history/current-with-changes")
    drill = _parameter_names("/api/dashboard/drill-down")

    assert {"node_type", "parent_node_type"} <= matrix
    assert {"node_type", "parent_node_type"} <= history
    assert "parent_node_type" in drill
    for parameters in (matrix, history, drill):
        assert "level_type" not in parameters
        assert "parent_level" not in parameters


def test_realtime_acc_value_mode_is_exposed_on_all_realtime_queries():
    for path in (
        "/api/dashboard/current",
        "/api/dashboard/current-with-changes",
        "/api/dashboard/matrix",
        "/api/dashboard/overview",
        "/api/dashboard/drill-down",
    ):
        assert "value_mode" in _parameter_names(path)


def test_current_query_supports_bounded_channel_search():
    parameters = _parameter_names("/api/dashboard/current")

    assert {"search", "limit"} <= parameters
