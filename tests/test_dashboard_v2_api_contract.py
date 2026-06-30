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
