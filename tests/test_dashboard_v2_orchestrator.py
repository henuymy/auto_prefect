from __future__ import annotations

from decimal import Decimal

import pytest

from services.dashboard_structure import (
    StructureEdge,
    StructureGraph,
    StructureNode,
    merge_subtree_retry_collection,
)
from services.dashboard_v2_orchestrator import (
    V2MetricCoverageError,
    augment_v2_observed_with_base,
    validate_v2_metric_coverage,
)


def node(node_type: str, code: str, name: str | None = None, node_id: int | None = None):
    return StructureNode(
        node_type=node_type,
        code=code,
        name=name or code,
        node_id=node_id,
    )


def base_graph() -> StructureGraph:
    nodes = {
        ("CITY", "A"): node("CITY", "A", node_id=1),
        ("BRANCH", "B"): node("BRANCH", "B", node_id=2),
        ("GRID", "G"): node("GRID", "G", node_id=3),
    }
    return StructureGraph(
        areas=dict(nodes),
        targets=dict(nodes),
        edges={
            StructureEdge("CITY", "A", "BRANCH", "B"),
            StructureEdge("BRANCH", "B", "GRID", "G"),
        },
    )


def test_augment_observed_preserves_approved_base_edges():
    graph = augment_v2_observed_with_base(
        base_graph(),
        [
            {
                "level_type": "CHANNEL_MANAGER",
                "area_code": "M",
                "area_name": "经理",
                "metric": 10,
            },
            {
                "level_type": "CHANNEL",
                "area_code": "C",
                "area_name": "渠道",
                "parent_request_code": "M",
                "metric": 5,
            },
        ],
        [
            {
                "parent_type": "GRID",
                "parent_code": "G",
                "child_type": "CHANNEL_MANAGER",
                "child_code": "M",
                "child_name": "经理",
            },
            {
                "parent_type": "CHANNEL_MANAGER",
                "parent_code": "M",
                "child_type": "CHANNEL",
                "child_code": "C",
                "child_name": "渠道",
            },
        ],
    )

    assert set(graph.areas) == {
        ("CITY", "A"),
        ("BRANCH", "B"),
        ("GRID", "G"),
        ("CHANNEL_MANAGER", "M"),
        ("CHANNEL", "C"),
    }
    assert StructureEdge("CITY", "A", "BRANCH", "B") in graph.edges
    assert StructureEdge("GRID", "G", "CHANNEL_MANAGER", "M") in graph.edges


def test_metric_coverage_requires_manager_self_row():
    graph = augment_v2_observed_with_base(
        base_graph(),
        [],
        [
            {
                "parent_type": "GRID",
                "parent_code": "G",
                "child_type": "CHANNEL_MANAGER",
                "child_code": "M",
                "child_name": "经理",
            }
        ],
    )

    with pytest.raises(V2MetricCoverageError, match="missing"):
        validate_v2_metric_coverage(
            [
                {
                    "level_type": "CITY",
                    "area_code": "A",
                    "metric": 1,
                },
                {
                    "level_type": "BRANCH",
                    "area_code": "B",
                    "metric": 1,
                },
                {
                    "level_type": "GRID",
                    "area_code": "G",
                    "metric": 1,
                },
            ],
            graph,
            ["metric"],
        )


def test_metric_coverage_attaches_existing_node_ids():
    graph = base_graph()
    rows = [
        {"level_type": "CITY", "area_code": "A", "metric": "1.5"},
        {"level_type": "BRANCH", "area_code": "B", "metric": 2},
        {"level_type": "GRID", "area_code": "G", "metric": 3},
    ]

    result = validate_v2_metric_coverage(rows, graph, ["metric"])

    assert [row["node_id"] for row in result] == [2, 1, 3]
    assert Decimal(str(result[0]["metric"])) == Decimal("2")


def test_subtree_merge_replaces_manager_self_row_instead_of_duplicating_it():
    base = {
        "rows": [
            {
                "level_type": "CHANNEL_MANAGER",
                "area_code": "M",
                "area_name": "经理",
                "metric": 1,
            }
        ],
        "structure_observations": [
            {
                "parent_type": "GRID",
                "parent_code": "G",
                "child_type": "CHANNEL_MANAGER",
                "child_code": "M",
                "child_name": "经理",
            }
        ],
        "request_count": 1,
        "recoverable_errors": [],
    }
    retry = {
        "rows": [
            {
                "level_type": "CHANNEL_MANAGER",
                "area_code": "M",
                "area_name": "经理",
                "metric": 2,
            }
        ],
        "structure_observations": base["structure_observations"],
        "request_count": 2,
        "recoverable_errors": [],
    }

    merged = merge_subtree_retry_collection(base, retry, {"G"}, {"M"})

    assert len(merged["rows"]) == 1
    assert merged["rows"][0]["metric"] == 2
