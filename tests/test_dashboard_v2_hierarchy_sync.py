from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy import create_engine, event, select, text
from sqlalchemy.orm import Session

from models.dashboard_v2 import HierarchyNode
from services.dashboard_structure import (
    StructureEdge,
    StructureGraph,
    StructureNode,
)
from services.dashboard_v2_hierarchy import sync_v2_hierarchy_in_session


NODE_ROWS = [
    (1, "CITY", "A", None, 1, 1),
    (2, "BRANCH", "B", 1, 2, 1),
    (3, "GRID", "G", 2, 3, 1),
    (4, "CHANNEL_MANAGER", "M", 3, 4, 1),
    (5, "CHANNEL", "C", 4, 5, 0),
]


@pytest.fixture
def session():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    with engine.begin() as connection:
        connection.execute(text("""
            CREATE TABLE hierarchy_node (
                id INTEGER PRIMARY KEY,
                node_type VARCHAR(32) NOT NULL,
                node_code VARCHAR(100) NOT NULL,
                node_name VARCHAR(200) NOT NULL,
                parent_id INTEGER,
                level_no INTEGER NOT NULL,
                request_enabled BOOLEAN NOT NULL,
                metric_enabled BOOLEAN NOT NULL,
                enabled BOOLEAN NOT NULL,
                sort_order INTEGER NOT NULL,
                last_seen_at DATETIME,
                missing_count INTEGER NOT NULL,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """))
        connection.execute(text("""
            CREATE TABLE hierarchy_parent_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                child_node_id INTEGER NOT NULL,
                parent_node_id INTEGER NOT NULL,
                valid_from DATETIME NOT NULL,
                valid_to DATETIME,
                collection_run_id INTEGER,
                change_type VARCHAR(32) NOT NULL,
                active_child_node_id INTEGER,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """))
        for node_id, node_type, code, parent_id, level_no, request_enabled in NODE_ROWS:
            connection.execute(
                text("""
                    INSERT INTO hierarchy_node (
                        id, node_type, node_code, node_name, parent_id, level_no,
                        request_enabled, metric_enabled, enabled, sort_order,
                        last_seen_at, missing_count
                    ) VALUES (
                        :id, :node_type, :code, :code, :parent_id, :level_no,
                        :request_enabled, 1, 1, :sort_order,
                        '2026-07-01 08:00:00', 0
                    )
                """),
                {
                    "id": node_id,
                    "node_type": node_type,
                    "code": code,
                    "parent_id": parent_id,
                    "level_no": level_no,
                    "request_enabled": request_enabled,
                    "sort_order": level_no * 10,
                },
            )
            if parent_id is not None:
                connection.execute(
                    text("""
                        INSERT INTO hierarchy_parent_history (
                            child_node_id, parent_node_id, valid_from,
                            collection_run_id, change_type, active_child_node_id
                        ) VALUES (
                            :child_id, :parent_id, '2026-07-01 08:00:00',
                            NULL, 'CREATED', :child_id
                        )
                    """),
                    {"child_id": node_id, "parent_id": parent_id},
                )
    with Session(engine) as value:
        yield value
    engine.dispose()


def candidate_graph(
    *,
    include_manager: bool = True,
    include_channel: bool = True,
) -> StructureGraph:
    identities = [
        ("CITY", "A"),
        ("BRANCH", "B"),
        ("GRID", "G"),
    ]
    if include_manager:
        identities.append(("CHANNEL_MANAGER", "M"))
    if include_channel:
        if not include_manager:
            raise ValueError("CHANNEL requires CHANNEL_MANAGER")
        identities.append(("CHANNEL", "C"))
    nodes = {
        identity: StructureNode(
            node_type=identity[0],
            code=identity[1],
            name=identity[1],
        )
        for identity in identities
    }
    edges = {
        StructureEdge("CITY", "A", "BRANCH", "B"),
        StructureEdge("BRANCH", "B", "GRID", "G"),
    }
    if include_manager:
        edges.add(StructureEdge("GRID", "G", "CHANNEL_MANAGER", "M"))
    if include_channel:
        edges.add(StructureEdge("CHANNEL_MANAGER", "M", "CHANNEL", "C"))
    return StructureGraph(
        areas=dict(nodes),
        targets={key: node for key, node in nodes.items() if key[0] != "CHANNEL"},
        edges=edges,
    )


def test_stable_observed_nodes_refresh_last_seen_without_structure_update(session):
    observed_at = datetime(2026, 7, 10, 10, 0)

    result = sync_v2_hierarchy_in_session(
        session,
        candidate_graph(),
        collected_at=observed_at,
        collection_run_id=None,
    )
    branch = session.scalar(
        select(HierarchyNode).where(HierarchyNode.node_code == "B")
    )

    assert branch is not None
    assert branch.last_seen_at == observed_at
    assert branch.missing_count == 0
    assert result["updated"] == 0


def test_missing_node_disable_keeps_last_trusted_observation_time(session):
    last_observed_at = datetime(2026, 7, 1, 8, 0)
    session.execute(
        text("UPDATE hierarchy_node SET missing_count = 1 WHERE node_code = 'C'")
    )

    result = sync_v2_hierarchy_in_session(
        session,
        candidate_graph(include_channel=False),
        collected_at=datetime(2026, 7, 10, 10, 0),
        collection_run_id=None,
        removed_identities={("CHANNEL", "C")},
        missing_disable_threshold=2,
    )
    channel = session.scalar(
        select(HierarchyNode).where(HierarchyNode.node_code == "C")
    )

    assert channel is not None
    assert channel.enabled is False
    assert channel.last_seen_at == last_observed_at
    assert result["disabled"] == 1


def test_missing_parent_with_active_descendant_defers_disable(session):
    session.execute(
        text("UPDATE hierarchy_node SET missing_count = 1 WHERE node_code = 'M'")
    )

    result = sync_v2_hierarchy_in_session(
        session,
        candidate_graph(include_manager=False, include_channel=False),
        collected_at=datetime(2026, 7, 10, 10, 0),
        collection_run_id=None,
        removed_identities={("CHANNEL_MANAGER", "M")},
        missing_disable_threshold=2,
    )
    manager = session.scalar(
        select(HierarchyNode).where(HierarchyNode.node_code == "M")
    )
    channel = session.scalar(
        select(HierarchyNode).where(HierarchyNode.node_code == "C")
    )

    assert manager is not None
    assert channel is not None
    assert manager.enabled is True
    assert channel.enabled is True
    assert manager.missing_count == 2
    assert channel.missing_count == 0
    active_history_count = session.scalar(
        text(
            "SELECT COUNT(*) FROM hierarchy_parent_history "
            "WHERE child_node_id IN (4, 5) AND valid_to IS NULL"
        )
    )
    assert active_history_count == 2
    assert result["disabled"] == 0
    assert result["disable_deferred"] == 1


def test_missing_parent_disables_after_descendant_is_independently_removed(session):
    session.execute(
        text(
            "UPDATE hierarchy_node SET missing_count = 1 "
            "WHERE node_code IN ('M', 'C')"
        )
    )

    result = sync_v2_hierarchy_in_session(
        session,
        candidate_graph(include_manager=False, include_channel=False),
        collected_at=datetime(2026, 7, 10, 10, 0),
        collection_run_id=None,
        removed_identities={
            ("CHANNEL_MANAGER", "M"),
            ("CHANNEL", "C"),
        },
        missing_disable_threshold=2,
    )
    manager = session.scalar(
        select(HierarchyNode).where(HierarchyNode.node_code == "M")
    )
    channel = session.scalar(
        select(HierarchyNode).where(HierarchyNode.node_code == "C")
    )

    assert manager is not None
    assert channel is not None
    assert manager.enabled is False
    assert channel.enabled is False
    assert result["disabled"] == 2
    assert result["disable_deferred"] == 0


def test_confirmed_subtree_reconciliation_disables_unobserved_descendants(session):
    session.execute(
        text("UPDATE hierarchy_node SET missing_count = 1 WHERE node_code = 'M'")
    )

    result = sync_v2_hierarchy_in_session(
        session,
        candidate_graph(include_manager=False, include_channel=False),
        collected_at=datetime(2026, 7, 10, 10, 0),
        collection_run_id=None,
        removed_identities={("CHANNEL_MANAGER", "M")},
        missing_disable_threshold=2,
        complete_structure_reconciliation=True,
    )
    manager = session.scalar(
        select(HierarchyNode).where(HierarchyNode.node_code == "M")
    )
    channel = session.scalar(
        select(HierarchyNode).where(HierarchyNode.node_code == "C")
    )

    assert manager is not None
    assert channel is not None
    assert manager.enabled is False
    assert channel.enabled is False
    assert manager.missing_count == 2
    assert channel.missing_count == 2
    assert result["disabled"] == 2
    assert result["disable_deferred"] == 0


def test_parent_history_is_preloaded_once_instead_of_queried_per_node(session):
    history_selects = 0

    def count_history_selects(_connection, _cursor, statement, *_args):
        nonlocal history_selects
        normalized = statement.lower().lstrip()
        if normalized.startswith("select") and "hierarchy_parent_history" in normalized:
            history_selects += 1

    event.listen(session.bind, "before_cursor_execute", count_history_selects)
    try:
        sync_v2_hierarchy_in_session(
            session,
            candidate_graph(),
            collected_at=datetime(2026, 7, 10, 10, 0),
            collection_run_id=None,
        )
    finally:
        event.remove(session.bind, "before_cursor_execute", count_history_selects)

    assert history_selects == 1
