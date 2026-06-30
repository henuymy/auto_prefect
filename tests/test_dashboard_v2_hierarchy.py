from __future__ import annotations

import json

import pytest

from services.dashboard_collection_orchestrator import (
    StructureEdge,
    StructureGraph,
    StructureNode,
)
from services.dashboard_v2_hierarchy import (
    V2HierarchyError,
    build_v2_observed_graph,
    load_bootstrap_manifest,
    removed_identities_from_change_plan,
    validate_bootstrap_manifest,
    validate_v2_structure_graph,
)


def manifest_rows():
    return [
        {
            "node_type": "CITY",
            "node_code": "A",
            "node_name": "城市",
            "parent_node_code": None,
            "sort_order": 1,
        },
        {
            "node_type": "BRANCH",
            "node_code": "B",
            "node_name": "分局",
            "parent_node_code": "A",
            "sort_order": 2,
        },
        {
            "node_type": "GRID",
            "node_code": "G",
            "node_name": "网格",
            "parent_node_code": "B",
            "sort_order": 3,
        },
    ]


def graph_node(node_type: str, code: str) -> StructureNode:
    return StructureNode(node_type=node_type, code=code, name=code)


def complete_graph() -> StructureGraph:
    nodes = {
        (node_type, code): graph_node(node_type, code)
        for node_type, code in [
            ("CITY", "A"),
            ("BRANCH", "B"),
            ("GRID", "G"),
            ("CHANNEL_MANAGER", "M"),
            ("CHANNEL", "C"),
        ]
    }
    return StructureGraph(
        areas=dict(nodes),
        targets={key: value for key, value in nodes.items() if key[0] != "CHANNEL"},
        edges={
            StructureEdge("CITY", "A", "BRANCH", "B"),
            StructureEdge("BRANCH", "B", "GRID", "G"),
            StructureEdge("GRID", "G", "CHANNEL_MANAGER", "M"),
            StructureEdge("CHANNEL_MANAGER", "M", "CHANNEL", "C"),
        },
    )


def test_bootstrap_manifest_is_normalized_in_parent_first_order():
    result = validate_bootstrap_manifest(reversed(manifest_rows()))

    assert [(node.node_type, node.node_code) for node in result] == [
        ("CITY", "A"),
        ("BRANCH", "B"),
        ("GRID", "G"),
    ]


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"node_type": "CHANNEL"}, "层级非法"),
        ({"parent_node_code": "UNKNOWN"}, "父节点不存在"),
        ({"node_code": ""}, "不得为空"),
    ],
)
def test_bootstrap_manifest_rejects_invalid_rows(change, message):
    rows = manifest_rows()
    rows[-1].update(change)

    with pytest.raises(V2HierarchyError, match=message):
        validate_bootstrap_manifest(rows)


def test_load_bootstrap_manifest_supports_json(tmp_path):
    path = tmp_path / "hierarchy.json"
    path.write_text(json.dumps(manifest_rows(), ensure_ascii=False), encoding="utf-8")

    result = load_bootstrap_manifest(path)

    assert result[-1].node_code == "G"


def test_complete_v2_hierarchy_is_valid():
    validate_v2_structure_graph(complete_graph())


def test_v2_hierarchy_rejects_multiple_parents():
    graph = complete_graph()
    graph.areas[("CHANNEL_MANAGER", "M2")] = graph_node("CHANNEL_MANAGER", "M2")
    graph.targets[("CHANNEL_MANAGER", "M2")] = graph_node(
        "CHANNEL_MANAGER", "M2"
    )
    graph.edges.add(StructureEdge("GRID", "G", "CHANNEL_MANAGER", "M2"))
    graph.edges.add(StructureEdge("CHANNEL_MANAGER", "M2", "CHANNEL", "C"))

    with pytest.raises(V2HierarchyError, match="只能有一个父节点"):
        validate_v2_structure_graph(graph)


def test_v2_observed_graph_keeps_manager_as_metric_node():
    graph = build_v2_observed_graph(
        [
            {
                "level_type": "CHANNEL_MANAGER",
                "area_code": "M1",
                "area_name": "经理1",
            },
            {
                "level_type": "CHANNEL",
                "area_code": "C1",
                "area_name": "渠道1",
                "parent_request_code": "M1",
            },
        ],
        [
            {
                "parent_type": "GRID",
                "parent_code": "G1",
                "child_type": "CHANNEL_MANAGER",
                "child_code": "M1",
                "child_name": "经理1",
            },
            {
                "parent_type": "CHANNEL_MANAGER",
                "parent_code": "M1",
                "child_type": "CHANNEL",
                "child_code": "C1",
                "child_name": "渠道1",
            },
        ],
    )

    assert ("CHANNEL_MANAGER", "M1") in graph.areas
    assert ("CHANNEL_MANAGER", "M1") in graph.targets


def test_removed_relations_translate_to_channel_identity():
    identities = removed_identities_from_change_plan(
        {
            "removed_targets": [
                {"target_type": "CHANNEL_MANAGER", "target_code": "M1"}
            ],
            "removed_areas": [{"level_type": "CHANNEL", "area_code": "C1"}],
            "removed_relations": [
                {"child_type": "CHANNEL", "child_code": "C2"}
            ],
        }
    )

    assert identities == {
        ("CHANNEL_MANAGER", "M1"),
        ("CHANNEL", "C1"),
        ("CHANNEL", "C2"),
    }
