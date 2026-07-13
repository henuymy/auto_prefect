"""Schema-neutral structure comparison and retry planning for Dashboard V2."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from services.dashboard_collection_runtime import CollectionTarget


_SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")


@dataclass(frozen=True)
class StructureNode:
    node_type: str
    code: str
    name: str
    node_id: int | None = None

    @property
    def identity(self) -> tuple[str, str]:
        return (self.node_type, self.code)


@dataclass(frozen=True)
class StructureEdge:
    parent_type: str
    parent_code: str
    child_type: str
    child_code: str


@dataclass(frozen=True)
class StructureGraph:
    areas: dict[tuple[str, str], StructureNode]
    targets: dict[tuple[str, str], StructureNode]
    edges: set[StructureEdge]

    def target_children(
        self, parent_type: str, child_type: str
    ) -> dict[str, dict[str, str]]:
        result: dict[str, dict[str, str]] = {}
        for edge in self.edges:
            if edge.parent_type != parent_type or edge.child_type != child_type:
                continue
            child = self.targets.get((edge.child_type, edge.child_code))
            if child is None and child_type == "CHANNEL":
                child = self.areas.get((edge.child_type, edge.child_code))
            result.setdefault(edge.parent_code, {})[edge.child_code] = (
                child.name if child else ""
            )
        return result


@dataclass(frozen=True)
class StructureDiff:
    added_targets: list[dict[str, Any]]
    removed_targets: list[dict[str, Any]]
    changed_targets: list[dict[str, Any]]
    added_relations: list[dict[str, Any]]
    removed_relations: list[dict[str, Any]]
    removed_areas: list[dict[str, Any]]

    @property
    def changed(self) -> bool:
        return any(
            [
                self.added_targets,
                self.removed_targets,
                self.changed_targets,
                self.added_relations,
                self.removed_relations,
                self.removed_areas,
            ]
        )

    @property
    def target_or_relation_changed(self) -> bool:
        return any(
            [
                self.added_targets,
                self.removed_targets,
                self.changed_targets,
                self.added_relations,
                self.removed_relations,
            ]
        )


def summarize_structure_changes(
    diff: StructureDiff,
    *,
    retry_strategy: str,
    relation_bootstrap: bool = False,
    affected_grid_codes: Iterable[str] | None = None,
    sample_size: int = 10,
) -> dict[str, Any]:
    affected = sorted({str(code) for code in affected_grid_codes or [] if code})

    def sample(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [dict(item) for item in items[:sample_size]]

    return {
        "changed": diff.changed or relation_bootstrap,
        "retry_strategy": retry_strategy,
        "relation_bootstrap": relation_bootstrap,
        "affected_grid_codes": affected,
        "counts": {
            "added_targets": len(diff.added_targets),
            "removed_targets": len(diff.removed_targets),
            "changed_targets": len(diff.changed_targets),
            "added_relations": len(diff.added_relations),
            "removed_relations": len(diff.removed_relations),
            "removed_areas": len(diff.removed_areas),
        },
        "samples": {
            "added_targets": sample(diff.added_targets),
            "removed_targets": sample(diff.removed_targets),
            "changed_targets": sample(diff.changed_targets),
            "added_relations": sample(diff.added_relations),
            "removed_relations": sample(diff.removed_relations),
            "removed_areas": sample(diff.removed_areas),
        },
    }


def naive_shanghai_now() -> datetime:
    return datetime.now(_SHANGHAI_TZ).replace(tzinfo=None)


def build_observed_structure_graph(
    rows: Iterable[dict[str, Any]],
    structure_observations: Iterable[dict[str, Any]],
) -> StructureGraph:
    areas: dict[tuple[str, str], StructureNode] = {}
    targets: dict[tuple[str, str], StructureNode] = {}
    edges: set[StructureEdge] = set()
    for row in rows:
        level_type = str(row.get("level_type") or "").strip().upper()
        area_code = str(row.get("area_code") or "").strip()
        area_name = str(row.get("area_name") or "").strip()
        parent_code = str(row.get("parent_request_code") or "").strip()
        if not level_type or not area_code:
            continue
        if level_type in {"CITY", "BRANCH", "GRID", "CHANNEL"}:
            areas[(level_type, area_code)] = StructureNode(level_type, area_code, area_name)
        if level_type in {"CITY", "BRANCH", "GRID"}:
            targets[(level_type, area_code)] = StructureNode(level_type, area_code, area_name)
        if level_type == "CHANNEL" and parent_code:
            edges.add(
                StructureEdge("CHANNEL_MANAGER", parent_code, "CHANNEL", area_code)
            )
    for item in structure_observations:
        parent_type = str(item.get("parent_type") or "").strip().upper()
        child_type = str(item.get("child_type") or "").strip().upper()
        parent_code = str(item.get("parent_code") or "").strip()
        child_code = str(item.get("child_code") or "").strip()
        child_name = str(item.get("child_name") or "").strip()
        if not parent_type or not child_type or not parent_code or not child_code:
            continue
        edges.add(StructureEdge(parent_type, parent_code, child_type, child_code))
        if child_type == "CHANNEL_MANAGER":
            targets[(child_type, child_code)] = StructureNode(child_type, child_code, child_name)
        elif child_type == "CHANNEL":
            areas.setdefault(
                (child_type, child_code),
                StructureNode(child_type, child_code, child_name),
            )
    return StructureGraph(areas=areas, targets=targets, edges=edges)


def diff_structure_graph(
    current: StructureGraph,
    observed: StructureGraph,
    *,
    recoverable_errors: bool = False,
    compare_manager_channel_relations: bool = True,
) -> StructureDiff:
    current_managers = current.target_children("GRID", "CHANNEL_MANAGER")
    observed_managers = observed.target_children("GRID", "CHANNEL_MANAGER")
    added_targets: list[dict[str, Any]] = []
    removed_targets: list[dict[str, Any]] = []
    changed_targets: list[dict[str, Any]] = []
    for grid_code, expected_managers in observed_managers.items():
        existing_managers = current_managers.get(grid_code, {})
        for manager_code, manager_name in sorted(existing_managers.items()):
            if manager_code not in expected_managers:
                removed_targets.append(
                    {
                        "target_type": "CHANNEL_MANAGER",
                        "target_code": manager_code,
                        "target_name": manager_name,
                        "parent_type": "GRID",
                        "parent_code": grid_code,
                    }
                )
        for manager_code, manager_name in sorted(expected_managers.items()):
            if manager_code not in existing_managers:
                added_targets.append(
                    {
                        "target_type": "CHANNEL_MANAGER",
                        "target_code": manager_code,
                        "target_name": manager_name,
                        "parent_type": "GRID",
                        "parent_code": grid_code,
                    }
                )
            elif existing_managers[manager_code] != manager_name:
                changed_targets.append(
                    {
                        "target_type": "CHANNEL_MANAGER",
                        "target_code": manager_code,
                        "old_name": existing_managers[manager_code],
                        "target_name": manager_name,
                        "parent_type": "GRID",
                        "parent_code": grid_code,
                    }
                )
    added_relations: list[dict[str, Any]] = []
    removed_relations: list[dict[str, Any]] = []
    if compare_manager_channel_relations:
        current_channels = current.target_children("CHANNEL_MANAGER", "CHANNEL")
        observed_channels = observed.target_children("CHANNEL_MANAGER", "CHANNEL")
        for manager_code, expected_channels in observed_channels.items():
            existing_channels = current_channels.get(manager_code, {})
            for channel_code, channel_name in sorted(existing_channels.items()):
                if channel_code not in expected_channels:
                    removed_relations.append(
                        {
                            "parent_type": "CHANNEL_MANAGER",
                            "parent_code": manager_code,
                            "child_type": "CHANNEL",
                            "child_code": channel_code,
                            "child_name": channel_name,
                        }
                    )
            for channel_code, channel_name in sorted(expected_channels.items()):
                if channel_code not in existing_channels:
                    added_relations.append(
                        {
                            "parent_type": "CHANNEL_MANAGER",
                            "parent_code": manager_code,
                            "child_type": "CHANNEL",
                            "child_code": channel_code,
                            "child_name": channel_name,
                        }
                    )
    removed_areas: list[dict[str, Any]] = []
    if not recoverable_errors:
        missing = set(current.areas) - set(observed.areas)
        if not [identity for identity in missing if identity[0] != "CHANNEL"]:
            for identity in sorted(missing):
                area = current.areas[identity]
                removed_areas.append(
                    {
                        "level_type": area.node_type,
                        "area_code": area.code,
                        "area_name": area.name,
                        "area_id": area.node_id,
                    }
                )
    return StructureDiff(
        added_targets=added_targets,
        removed_targets=removed_targets,
        changed_targets=changed_targets,
        added_relations=added_relations,
        removed_relations=removed_relations,
        removed_areas=removed_areas,
    )


def affected_grid_codes_from_diff(
    diff: StructureDiff, current_graph: StructureGraph, observed_graph: StructureGraph
) -> set[str]:
    affected: set[str] = set()
    current_manager_grid = _manager_grid_map(current_graph)
    observed_manager_grid = _manager_grid_map(observed_graph)
    current_channel_managers = _channel_manager_map(current_graph)
    observed_channel_managers = _channel_manager_map(observed_graph)
    for item in diff.added_targets + diff.removed_targets + diff.changed_targets:
        if item.get("parent_type") == "GRID" and item.get("parent_code"):
            affected.add(str(item["parent_code"]))
    for item in diff.added_relations + diff.removed_relations:
        manager_code = str(item.get("parent_code") or "")
        grid_code = observed_manager_grid.get(manager_code) or current_manager_grid.get(manager_code)
        if grid_code:
            affected.add(grid_code)
    for item in diff.removed_areas:
        channel_code = str(item.get("area_code") or "")
        managers = current_channel_managers.get(channel_code, set()) | observed_channel_managers.get(channel_code, set())
        for manager_code in managers:
            grid_code = observed_manager_grid.get(manager_code) or current_manager_grid.get(manager_code)
            if grid_code:
                affected.add(grid_code)
    return affected


def manager_codes_for_grids(graph: StructureGraph, grid_codes: set[str]) -> set[str]:
    return {
        edge.child_code
        for edge in graph.edges
        if edge.parent_type == "GRID"
        and edge.child_type == "CHANNEL_MANAGER"
        and edge.parent_code in grid_codes
    }


def build_subtree_retry_targets(
    current_targets: list[CollectionTarget],
    rows: list[dict[str, Any]],
    structure_observations: list[dict[str, Any]],
    affected_grid_codes: set[str],
) -> list[CollectionTarget]:
    if not affected_grid_codes:
        return []
    observed_graph = build_observed_structure_graph(rows, structure_observations)
    current_by_identity = {
        (target.target_type, target.target_code): target for target in current_targets
    }
    retry_targets: list[CollectionTarget] = []
    next_temp_id = -1

    def allocate_id() -> int:
        nonlocal next_temp_id
        value = next_temp_id
        next_temp_id -= 1
        return value

    for grid_code in sorted(affected_grid_codes):
        identity = ("GRID", grid_code)
        existing = current_by_identity.get(identity)
        observed = observed_graph.targets.get(identity)
        if existing is not None:
            grid_target = existing
        elif observed is not None:
            grid_target = CollectionTarget(
                id=allocate_id(),
                target_code=observed.code,
                target_name=observed.name,
                target_type=observed.node_type,
                area_id=None,
                parent_target_id=None,
                sort_order=_target_sort_key(observed.node_type) * 10,
            )
        else:
            continue
        retry_targets.append(grid_target)
        manager_children = observed_graph.target_children("GRID", "CHANNEL_MANAGER").get(grid_code, {})
        for manager_code, manager_name in sorted(manager_children.items()):
            existing_manager = current_by_identity.get(("CHANNEL_MANAGER", manager_code))
            if existing_manager is not None:
                manager_target = CollectionTarget(
                    id=existing_manager.id,
                    target_code=existing_manager.target_code,
                    target_name=manager_name or existing_manager.target_name,
                    target_type=existing_manager.target_type,
                    area_id=existing_manager.area_id,
                    parent_target_id=grid_target.id,
                    sort_order=existing_manager.sort_order,
                )
            else:
                manager_target = CollectionTarget(
                    id=allocate_id(),
                    target_code=manager_code,
                    target_name=manager_name,
                    target_type="CHANNEL_MANAGER",
                    area_id=None,
                    parent_target_id=grid_target.id,
                    sort_order=_target_sort_key("CHANNEL_MANAGER") * 10,
                )
            retry_targets.append(manager_target)
    return retry_targets


def merge_subtree_retry_collection(
    base_result: dict[str, Any],
    retry_result: dict[str, Any],
    affected_grid_codes: set[str],
    affected_manager_codes: set[str] | None = None,
) -> dict[str, Any]:
    base_graph = build_observed_structure_graph(
        base_result.get("rows", []), base_result.get("structure_observations", [])
    )
    retry_graph = build_observed_structure_graph(
        retry_result.get("rows", []), retry_result.get("structure_observations", [])
    )
    managers = set(affected_manager_codes or set())
    managers.update(
        edge.child_code
        for graph in (base_graph, retry_graph)
        for edge in graph.edges
        if edge.parent_type == "GRID"
        and edge.child_type == "CHANNEL_MANAGER"
        and edge.parent_code in affected_grid_codes
    )
    for row in retry_result.get("rows", []):
        if str(row.get("level_type") or "").strip().upper() == "CHANNEL":
            parent_code = str(row.get("parent_request_code") or "").strip()
            if parent_code:
                managers.add(parent_code)

    def row_in_subtree(row: dict[str, Any]) -> bool:
        level_type = str(row.get("level_type") or "").strip().upper()
        code = str(row.get("area_code") or "").strip()
        parent_code = str(row.get("parent_request_code") or "").strip()
        return (
            level_type == "GRID" and code in affected_grid_codes
        ) or (
            level_type == "CHANNEL_MANAGER" and code in managers
        ) or (
            level_type == "CHANNEL" and parent_code in managers
        )

    def observation_in_subtree(item: dict[str, Any]) -> bool:
        parent_type = str(item.get("parent_type") or "").strip().upper()
        parent_code = str(item.get("parent_code") or "").strip()
        return (
            parent_type == "GRID" and parent_code in affected_grid_codes
        ) or (
            parent_type == "CHANNEL_MANAGER" and parent_code in managers
        )

    def error_in_subtree(item: dict[str, Any]) -> bool:
        target_type = str(item.get("target_type") or "").strip().upper()
        target_code = str(item.get("target_code") or "").strip()
        return (
            target_type == "GRID" and target_code in affected_grid_codes
        ) or (
            target_type == "CHANNEL_MANAGER" and target_code in managers
        )

    merged_rows = [row for row in base_result.get("rows", []) if not row_in_subtree(row)] + list(retry_result.get("rows", []))
    observations = [
        item
        for item in base_result.get("structure_observations", [])
        if not observation_in_subtree(item)
    ] + list(retry_result.get("structure_observations", []))
    recoverable_errors = [
        item
        for item in base_result.get("recoverable_errors", [])
        if not error_in_subtree(item)
    ] + list(retry_result.get("recoverable_errors", []))
    return {
        **retry_result,
        "rows": merged_rows,
        "structure_observations": observations,
        "request_count": int(base_result.get("request_count", 0)) + int(retry_result.get("request_count", 0)),
        "row_count": len(merged_rows),
        "recoverable_errors": recoverable_errors,
        "subtree_retry": {
            "affected_grid_codes": sorted(affected_grid_codes),
            "retry_request_count": retry_result.get("request_count", 0),
        },
    }


def build_candidate_targets_from_observations(
    rows: list[dict[str, Any]], structure_observations: list[dict[str, Any]]
) -> list[CollectionTarget]:
    graph = build_observed_structure_graph(rows, structure_observations)
    candidates: dict[tuple[str, str], CollectionTarget] = {}
    next_temp_id = -1

    def allocate_id() -> int:
        nonlocal next_temp_id
        value = next_temp_id
        next_temp_id -= 1
        return value

    for identity, node in sorted(
        graph.targets.items(), key=lambda item: (_target_sort_key(item[0][0]), item[0][1])
    ):
        if identity[0] == "CHANNEL_MANAGER":
            continue
        candidates[identity] = CollectionTarget(
            id=allocate_id(),
            target_code=node.code,
            target_name=node.name,
            target_type=node.node_type,
            area_id=None,
            parent_target_id=None,
            sort_order=_target_sort_key(node.node_type) * 10,
        )
    manager_edges = sorted(
        (
            edge
            for edge in graph.edges
            if edge.parent_type == "GRID" and edge.child_type == "CHANNEL_MANAGER"
        ),
        key=lambda edge: (edge.parent_code, edge.child_code),
    )
    for edge in manager_edges:
        identity = (edge.child_type, edge.child_code)
        node = graph.targets.get(identity)
        if node is None or identity in candidates:
            continue
        parent = candidates.get(("GRID", edge.parent_code))
        candidates[identity] = CollectionTarget(
            id=allocate_id(),
            target_code=node.code,
            target_name=node.name,
            target_type=node.node_type,
            area_id=None,
            parent_target_id=parent.id if parent else None,
            sort_order=_target_sort_key(node.node_type) * 10,
        )
    return sorted(candidates.values(), key=lambda target: (target.sort_order, target.target_code))


def normalize_collection_retry_strategy(value: str | None) -> str:
    strategy = str(value or "affected_grid").strip().lower()
    if strategy in {"affected_grid", "affected-grid", "subtree"}:
        return "affected_grid"
    if strategy == "full":
        return "full"
    raise ValueError(
        "collection_retry_strategy 只支持 affected_grid 或 full: " f"{value!r}"
    )


def _manager_grid_map(graph: StructureGraph) -> dict[str, str]:
    return {
        edge.child_code: edge.parent_code
        for edge in graph.edges
        if edge.parent_type == "GRID" and edge.child_type == "CHANNEL_MANAGER"
    }


def _channel_manager_map(graph: StructureGraph) -> dict[str, set[str]]:
    result: dict[str, set[str]] = {}
    for edge in graph.edges:
        if edge.parent_type == "CHANNEL_MANAGER" and edge.child_type == "CHANNEL":
            result.setdefault(edge.child_code, set()).add(edge.parent_code)
    return result


def _target_sort_key(target_type: str) -> int:
    return {"CITY": 1, "BRANCH": 2, "GRID": 3, "CHANNEL_MANAGER": 4}.get(
        target_type, 99
    )
