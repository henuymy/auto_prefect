"""Shared collection, structure recovery, and area validation orchestration."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from time import perf_counter
from typing import Any, Callable, Iterable

from sqlalchemy import Engine, inspect, select
from sqlalchemy.orm import Session, sessionmaker

from infrastructure.dashboard_run_store import CollectionRunStore
from models.dashboard_area import Area
from models.dashboard_channel_manager_area import ChannelManagerArea
from models.dashboard_request_target import RequestTarget
from services.dashboard_area_validation import (
    execute_area_validation_phase,
    load_enabled_area_map,
    validate_metric_rows,
)
from services.dashboard_collection_service import (
    CollectionTarget,
    execute_collection_phase,
)
from services.dashboard_trigger import now_shanghai


@dataclass(frozen=True)
class CollectionResult:
    rows: list[dict[str, Any]]
    structure_observations: list[dict[str, Any]]
    recoverable_errors: list[dict[str, Any]]
    request_count: int
    row_count: int


@dataclass(frozen=True)
class ValidationResult:
    matched_area_count: int
    matched_row_count: int
    unmatched_areas: list[dict[str, Any]]


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
        self,
        parent_type: str,
        child_type: str,
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
    """Build a compact, log-friendly summary of structure changes."""
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


class AreaCoverageError(RuntimeError):
    phase = "VALIDATE_AREA"
    error_type = "AREA_COVERAGE_MISMATCH"


class AreaIdResolutionError(RuntimeError):
    phase = "WRITE_METRICS"
    error_type = "AREA_ID_RESOLUTION_FAILED"


class StructureDriftError(RuntimeError):
    """检测到结构漂移，需要更新结构后重采"""
    phase = "VALIDATE_AREA"
    error_type = "STRUCTURE_DRIFT"

    def __init__(self, validation_result: ValidationResult, observations: list[dict[str, Any]]):
        self.validation_result = validation_result
        self.observations = observations
        unmatched = validation_result.unmatched_areas
        preview = "；".join(
            f"{item['level_type']}/{item['area_code']}/{item['area_name']}"
            for item in unmatched[:10]
        )
        suffix = f" 等{len(unmatched)}个区域" if len(unmatched) > 10 else ""
        super().__init__(
            f"检测到结构漂移: {preview}{suffix}。将更新结构后重采。"
        )


def ensure_complete_area_coverage(
    validation_result: dict[str, Any],
    expected_area_count: int,
) -> None:
    actual = int(validation_result["matched_area_count"])
    row_count = int(validation_result["matched_row_count"])
    if actual != expected_area_count or row_count != expected_area_count:
        raise AreaCoverageError(
            f"正式区域覆盖不完整或存在重复: expected={expected_area_count}, "
            f"matched_areas={actual}, matched_rows={row_count}"
        )


def metric_rows(
    validated_rows: list[dict[str, Any]],
    indicator_code: str,
) -> list[dict[str, Any]]:
    return [
        {
            "area_id": row["area_id"],
            "raw_value": row.get(indicator_code),
        }
        for row in validated_rows
    ]


def naive_shanghai_now() -> datetime:
    return now_shanghai().replace(tzinfo=None)


def detect_new_areas(
    rows: list[dict[str, Any]],
    area_map: dict[Any, Any],
) -> set[tuple[str, str]]:
    """检测采集数据中的新区域

    Args:
        rows: 采集到的数据行
        area_map: 现有的区域映射 {(level_type, area_code): Area}

    Returns:
        新区域的集合 {(level_type, area_code), ...}
    """
    collected = {
        (str(row.get("level_type") or ""), str(row.get("area_code") or ""))
        for row in rows
        if row.get("level_type") and row.get("area_code")
    }
    existing = set(area_map.keys())
    return collected - existing


def affected_grid_targets(
    targets: list[CollectionTarget],
    collection_result: dict[str, Any],
    _area_map: dict[Any, Any],
) -> list[CollectionTarget]:
    grid_by_id = {
        target.id: target
        for target in targets
        if target.target_type == "GRID"
    }
    grid_by_code = {
        target.target_code: target
        for target in targets
        if target.target_type == "GRID"
    }
    manager_to_grid = {
        target.target_code: grid_by_id.get(target.parent_target_id)
        for target in targets
        if target.target_type == "CHANNEL_MANAGER"
    }

    affected: list[CollectionTarget] = []
    seen_codes: set[str] = set()

    for error in collection_result.get("recoverable_errors", []):
        if error.get("target_type") != "CHANNEL_MANAGER":
            continue
        grid = manager_to_grid.get(str(error.get("target_code") or ""))
        if grid and grid.target_code not in seen_codes:
            affected.append(grid)
            seen_codes.add(grid.target_code)

    for row in collection_result.get("rows", []):
        if str(row.get("level_type") or "").upper() != "CHANNEL":
            continue
        parent_code = str(row.get("parent_request_code") or "")
        grid = manager_to_grid.get(parent_code)
        if grid and grid.target_code not in seen_codes:
            affected.append(grid)
            seen_codes.add(grid.target_code)

    for obs in collection_result.get("structure_observations", []):
        if str(obs.get("parent_type") or "").upper() != "GRID":
            continue
        if str(obs.get("child_type") or "").upper() != "CHANNEL_MANAGER":
            continue
        grid = grid_by_code.get(str(obs.get("parent_code") or ""))
        if grid and grid.target_code not in seen_codes:
            affected.append(grid)
            seen_codes.add(grid.target_code)

    return affected


def detect_structure_drift(
    validation_result: dict[str, Any],
    structure_observations: list[dict[str, Any]],
) -> bool:
    """检测是否存在结构漂移

    结构漂移判定条件：
    1. 验证不通过（有 unmatched_areas）
    2. 并且存在结构观察（structure_observations）表明新结构

    Args:
        validation_result: 验证结果
        structure_observations: 结构观察列表

    Returns:
        True 表示存在结构漂移，False 表示非结构问题
    """
    if validation_result.get("unmatched_area_count", 0) == 0:
        return False

    unmatched_codes = {
        (item["level_type"], item["area_code"])
        for item in validation_result.get("unmatched_areas", [])
    }

    observed_codes = set()
    for obs in structure_observations:
        child_type = str(obs.get("child_type") or "").upper()
        child_code = str(obs.get("child_code") or "").strip()
        if child_type and child_code:
            observed_codes.add((child_type, child_code))

    return bool(unmatched_codes & observed_codes)


def build_observed_structure_graph(
    rows: Iterable[dict[str, Any]],
    structure_observations: Iterable[dict[str, Any]],
) -> StructureGraph:
    """Build the structure tree observed in one collection attempt."""
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
            areas[(level_type, area_code)] = StructureNode(
                node_type=level_type,
                code=area_code,
                name=area_name,
            )
        if level_type in {"CITY", "BRANCH", "GRID"}:
            targets[(level_type, area_code)] = StructureNode(
                node_type=level_type,
                code=area_code,
                name=area_name,
            )
        if level_type == "CHANNEL" and parent_code:
            edges.add(
                StructureEdge(
                    parent_type="CHANNEL_MANAGER",
                    parent_code=parent_code,
                    child_type="CHANNEL",
                    child_code=area_code,
                )
            )

    for item in structure_observations:
        parent_type = str(item.get("parent_type") or "").strip().upper()
        child_type = str(item.get("child_type") or "").strip().upper()
        parent_code = str(item.get("parent_code") or "").strip()
        child_code = str(item.get("child_code") or "").strip()
        child_name = str(item.get("child_name") or "").strip()
        if not parent_type or not child_type or not parent_code or not child_code:
            continue
        edges.add(
            StructureEdge(
                parent_type=parent_type,
                parent_code=parent_code,
                child_type=child_type,
                child_code=child_code,
            )
        )
        if child_type == "CHANNEL_MANAGER":
            targets[(child_type, child_code)] = StructureNode(
                node_type=child_type,
                code=child_code,
                name=child_name,
            )
        elif child_type == "CHANNEL":
            areas.setdefault(
                (child_type, child_code),
                StructureNode(
                    node_type=child_type,
                    code=child_code,
                    name=child_name,
                ),
            )

    return StructureGraph(areas=areas, targets=targets, edges=edges)


def load_current_structure_graph(
    engine: Engine,
    targets: Iterable[CollectionTarget] | None = None,
) -> StructureGraph:
    """Load the currently enabled dashboard structure from DB."""
    areas: dict[tuple[str, str], StructureNode] = {}
    target_nodes: dict[tuple[str, str], StructureNode] = {}
    target_identities_by_id: dict[int, tuple[str, str]] = {}
    area_codes_by_id: dict[int, str] = {}
    edges: set[StructureEdge] = set()

    with Session(engine) as session:
        for area in session.scalars(select(Area).where(Area.enabled.is_(True))).all():
            identity = (area.level_type, area.area_code)
            areas[identity] = StructureNode(
                node_type=area.level_type,
                code=area.area_code,
                name=area.area_name,
                node_id=area.id,
            )
            area_codes_by_id[area.id] = area.area_code

        if targets is None and inspect(engine).has_table("request_target"):
            db_targets = session.scalars(
                select(RequestTarget).where(RequestTarget.enabled.is_(True))
            ).all()
            for target in db_targets:
                identity = (target.target_type, target.target_code)
                target_nodes[identity] = StructureNode(
                    node_type=target.target_type,
                    code=target.target_code,
                    name=target.target_name,
                    node_id=target.id,
                )
                target_identities_by_id[target.id] = identity
        else:
            for target in targets:
                identity = (target.target_type, target.target_code)
                target_nodes[identity] = StructureNode(
                    node_type=target.target_type,
                    code=target.target_code,
                    name=target.target_name,
                    node_id=target.id if target.id > 0 else None,
                )
                if target.id > 0:
                    target_identities_by_id[target.id] = identity

        if targets is not None:
            for target in targets:
                if target.parent_target_id is None:
                    continue
                parent_identity = target_identities_by_id.get(target.parent_target_id)
                if parent_identity is None:
                    continue
                edges.add(
                    StructureEdge(
                        parent_type=parent_identity[0],
                        parent_code=parent_identity[1],
                        child_type=target.target_type,
                        child_code=target.target_code,
                    )
                )
        elif inspect(engine).has_table("request_target"):
            source_targets = session.scalars(
                select(RequestTarget).where(RequestTarget.enabled.is_(True))
            ).all()
            for target in source_targets:
                if target.parent_target_id is None:
                    continue
                parent_identity = target_identities_by_id.get(target.parent_target_id)
                if parent_identity is None:
                    continue
                edges.add(
                    StructureEdge(
                        parent_type=parent_identity[0],
                        parent_code=parent_identity[1],
                        child_type=target.target_type,
                        child_code=target.target_code,
                    )
                )

        if inspect(engine).has_table("channel_manager_area"):
            relations = session.scalars(
                select(ChannelManagerArea).where(ChannelManagerArea.enabled.is_(True))
            ).all()
            for relation in relations:
                manager_identity = target_identities_by_id.get(
                    relation.manager_target_id
                )
                channel_code = area_codes_by_id.get(relation.channel_area_id)
                if manager_identity is None or channel_code is None:
                    continue
                edges.add(
                    StructureEdge(
                        parent_type=manager_identity[0],
                        parent_code=manager_identity[1],
                        child_type="CHANNEL",
                        child_code=channel_code,
                    )
                )

    return StructureGraph(areas=areas, targets=target_nodes, edges=edges)


def _structure_graph_from_collection_targets(
    targets: Iterable[CollectionTarget],
) -> StructureGraph:
    target_list = list(targets)
    targets_by_id = {
        target.id: target
        for target in target_list
    }
    target_nodes = {
        (target.target_type, target.target_code): StructureNode(
            node_type=target.target_type,
            code=target.target_code,
            name=target.target_name,
            node_id=target.id if target.id > 0 else None,
        )
        for target in target_list
    }
    edges: set[StructureEdge] = set()
    for target in target_list:
        if target.parent_target_id is None:
            continue
        parent = targets_by_id.get(target.parent_target_id)
        if parent is None:
            continue
        edges.add(
            StructureEdge(
                parent_type=parent.target_type,
                parent_code=parent.target_code,
                child_type=target.target_type,
                child_code=target.target_code,
            )
        )
    return StructureGraph(areas={}, targets=target_nodes, edges=edges)


def diff_structure_graph(
    current: StructureGraph,
    observed: StructureGraph,
    *,
    recoverable_errors: bool = False,
    compare_manager_channel_relations: bool = True,
) -> StructureDiff:
    """Diff current DB structure against the observed collection tree."""
    current_managers_by_grid = current.target_children("GRID", "CHANNEL_MANAGER")
    observed_managers_by_grid = observed.target_children("GRID", "CHANNEL_MANAGER")

    added_targets: list[dict[str, Any]] = []
    removed_targets: list[dict[str, Any]] = []
    changed_targets: list[dict[str, Any]] = []
    for grid_code, observed_managers in observed_managers_by_grid.items():
        current_managers = current_managers_by_grid.get(grid_code, {})
        for manager_code, manager_name in sorted(current_managers.items()):
            if manager_code not in observed_managers:
                removed_targets.append(
                    {
                        "target_type": "CHANNEL_MANAGER",
                        "target_code": manager_code,
                        "target_name": manager_name,
                        "parent_type": "GRID",
                        "parent_code": grid_code,
                    }
                )
        for manager_code, manager_name in sorted(observed_managers.items()):
            if manager_code not in current_managers:
                added_targets.append(
                    {
                        "target_type": "CHANNEL_MANAGER",
                        "target_code": manager_code,
                        "target_name": manager_name,
                        "parent_type": "GRID",
                        "parent_code": grid_code,
                    }
                )
            elif current_managers[manager_code] != manager_name:
                changed_targets.append(
                    {
                        "target_type": "CHANNEL_MANAGER",
                        "target_code": manager_code,
                        "old_name": current_managers[manager_code],
                        "target_name": manager_name,
                        "parent_type": "GRID",
                        "parent_code": grid_code,
                    }
                )

    added_relations: list[dict[str, Any]] = []
    removed_relations: list[dict[str, Any]] = []
    if compare_manager_channel_relations:
        current_channels_by_manager = current.target_children(
            "CHANNEL_MANAGER",
            "CHANNEL",
        )
        observed_channels_by_manager = observed.target_children(
            "CHANNEL_MANAGER",
            "CHANNEL",
        )
        for manager_code, observed_channels in observed_channels_by_manager.items():
            current_channels = current_channels_by_manager.get(manager_code, {})
            for channel_code, channel_name in sorted(current_channels.items()):
                if channel_code not in observed_channels:
                    removed_relations.append(
                        {
                            "parent_type": "CHANNEL_MANAGER",
                            "parent_code": manager_code,
                            "child_type": "CHANNEL",
                            "child_code": channel_code,
                            "child_name": channel_name,
                        }
                    )
            for channel_code, channel_name in sorted(observed_channels.items()):
                if channel_code not in current_channels:
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
        missing_identities = set(current.areas) - set(observed.areas)
        non_channel_missing = [
            identity for identity in missing_identities if identity[0] != "CHANNEL"
        ]
        if not non_channel_missing:
            for identity in sorted(missing_identities):
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


def _build_candidate_area_map(
    rows: list[dict[str, Any]],
) -> dict[tuple[str, str], "EnabledArea"]:
    """从采集行构建候选区域映射（内存中，不写库）

    返回:
        {(level_type, area_code): EnabledArea}
    """
    from services.dashboard_area_validation import EnabledArea

    graph = build_observed_structure_graph(rows, [])
    area_map: dict[tuple[str, str], EnabledArea] = {}
    for idx, (identity, area) in enumerate(graph.areas.items()):
        area_map[identity] = EnabledArea(
            id=-(idx + 1),
            level_type=area.node_type,
            area_code=area.code,
            area_name=area.name,
        )

    return area_map


def detect_removed_channel_areas(
    area_map: dict[tuple[str, str], Any],
    collection_result: dict[str, Any],
) -> list[dict[str, Any]]:
    """检测可信采集后完全消失的 CHANNEL area。"""
    current = StructureGraph(
        areas={
            identity: StructureNode(
                node_type=identity[0],
                code=identity[1],
                name=getattr(area, "area_name", ""),
                node_id=getattr(area, "id", None),
            )
            for identity, area in area_map.items()
        },
        targets={},
        edges=set(),
    )
    observed = build_observed_structure_graph(
        collection_result.get("rows", []),
        collection_result.get("structure_observations", []),
    )
    return diff_structure_graph(
        current,
        observed,
        recoverable_errors=bool(collection_result.get("recoverable_errors")),
        compare_manager_channel_relations=False,
    ).removed_areas


def detect_manager_target_changes(
    targets: list[CollectionTarget],
    collection_result: dict[str, Any],
) -> dict[str, Any]:
    """检测 GRID 返回的渠道经理集合是否与当前 request_target 不一致。"""
    current = _structure_graph_from_collection_targets(targets)
    observed = build_observed_structure_graph(
        collection_result.get("rows", []),
        collection_result.get("structure_observations", []),
    )
    diff = diff_structure_graph(
        current,
        observed,
        compare_manager_channel_relations=False,
    )
    return {
        "changed": bool(
            diff.added_targets or diff.removed_targets or diff.changed_targets
        ),
        "removed_targets": diff.removed_targets,
        "added_targets": diff.added_targets,
        "changed_targets": diff.changed_targets,
    }


def detect_manager_channel_relation_drift(
    engine: Engine,
    structure_observations: list[dict[str, Any]],
) -> bool:
    """检测 CHANNEL_MANAGER -> CHANNEL 关系是否和当前关系表不一致。"""
    if not inspect(engine).has_table("channel_manager_area"):
        return False

    current = load_current_structure_graph(engine)
    observed = build_observed_structure_graph([], structure_observations)
    diff = diff_structure_graph(current, observed)
    return bool(diff.added_relations or diff.removed_relations)


def has_manager_channel_relations(engine: Engine) -> bool:
    """Return whether the manager-channel relation table has any baseline rows."""
    if not inspect(engine).has_table("channel_manager_area"):
        return False
    with Session(engine) as session:
        relation_id = session.scalar(select(ChannelManagerArea.id).limit(1))
        return relation_id is not None


def detect_manager_target_drift(
    targets: list[CollectionTarget],
    structure_observations: list[dict[str, Any]],
) -> bool:
    """兼容旧调用：仅判断观察到的 manager 集合是否漂移。"""
    return detect_manager_target_changes(
        targets,
        {
            "rows": [],
            "structure_observations": structure_observations,
        },
    )["changed"]


def without_removed_areas(
    area_map: dict[tuple[str, str], Any],
    removed_areas: Iterable[dict[str, Any]],
) -> dict[tuple[str, str], Any]:
    removed = {
        (
            str(item.get("level_type") or "").strip().upper(),
            str(item.get("area_code") or "").strip(),
        )
        for item in removed_areas
    }
    return {
        identity: area
        for identity, area in area_map.items()
        if identity not in removed
    }


def _manager_grid_map(graph: StructureGraph) -> dict[str, str]:
    result: dict[str, str] = {}
    for edge in graph.edges:
        if edge.parent_type == "GRID" and edge.child_type == "CHANNEL_MANAGER":
            result[edge.child_code] = edge.parent_code
    return result


def _channel_manager_map(graph: StructureGraph) -> dict[str, set[str]]:
    result: dict[str, set[str]] = {}
    for edge in graph.edges:
        if edge.parent_type == "CHANNEL_MANAGER" and edge.child_type == "CHANNEL":
            result.setdefault(edge.child_code, set()).add(edge.parent_code)
    return result


def _affected_grid_codes_from_diff(
    diff: StructureDiff,
    current_graph: StructureGraph,
    observed_graph: StructureGraph,
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
        grid_code = observed_manager_grid.get(manager_code) or current_manager_grid.get(
            manager_code
        )
        if grid_code:
            affected.add(grid_code)

    for item in diff.removed_areas:
        channel_code = str(item.get("area_code") or "")
        for manager_code in (
            current_channel_managers.get(channel_code, set())
            | observed_channel_managers.get(channel_code, set())
        ):
            grid_code = observed_manager_grid.get(manager_code) or current_manager_grid.get(
                manager_code
            )
            if grid_code:
                affected.add(grid_code)

    return affected


def _manager_codes_for_grids(
    graph: StructureGraph,
    grid_codes: set[str],
) -> set[str]:
    return {
        edge.child_code
        for edge in graph.edges
        if edge.parent_type == "GRID"
        and edge.child_type == "CHANNEL_MANAGER"
        and edge.parent_code in grid_codes
    }


def _build_subtree_retry_targets(
    current_targets: list[CollectionTarget],
    rows: list[dict[str, Any]],
    structure_observations: list[dict[str, Any]],
    affected_grid_codes: set[str],
) -> list[CollectionTarget]:
    """Build retry targets for only the affected GRID subtrees."""
    if not affected_grid_codes:
        return []

    observed_graph = build_observed_structure_graph(rows, structure_observations)
    current_by_identity = {
        (target.target_type, target.target_code): target
        for target in current_targets
    }
    retry_targets: list[CollectionTarget] = []
    retry_by_identity: dict[tuple[str, str], CollectionTarget] = {}
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
        retry_by_identity[identity] = grid_target

        manager_children = observed_graph.target_children(
            "GRID",
            "CHANNEL_MANAGER",
        ).get(grid_code, {})
        for manager_code, manager_name in sorted(manager_children.items()):
            manager_identity = ("CHANNEL_MANAGER", manager_code)
            existing_manager = current_by_identity.get(manager_identity)
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
            retry_by_identity[manager_identity] = manager_target

    return retry_targets


def _merge_subtree_retry_collection(
    base_result: dict[str, Any],
    retry_result: dict[str, Any],
    affected_grid_codes: set[str],
    affected_manager_codes: set[str] | None = None,
) -> dict[str, Any]:
    base_graph = build_observed_structure_graph(
        base_result.get("rows", []),
        base_result.get("structure_observations", []),
    )
    retry_graph = build_observed_structure_graph(
        retry_result.get("rows", []),
        retry_result.get("structure_observations", []),
    )
    affected_manager_codes = set(affected_manager_codes or set()) | {
        edge.child_code
        for edge in retry_graph.edges
        if edge.parent_type == "GRID"
        and edge.child_type == "CHANNEL_MANAGER"
        and edge.parent_code in affected_grid_codes
    }
    affected_manager_codes.update(
        {
            edge.child_code
            for edge in base_graph.edges
            if edge.parent_type == "GRID"
            and edge.child_type == "CHANNEL_MANAGER"
            and edge.parent_code in affected_grid_codes
        }
    )
    for row in retry_result.get("rows", []):
        if str(row.get("level_type") or "").strip().upper() == "CHANNEL":
            parent_code = str(row.get("parent_request_code") or "").strip()
            if parent_code:
                affected_manager_codes.add(parent_code)

    def row_in_affected_subtree(row: dict[str, Any]) -> bool:
        level_type = str(row.get("level_type") or "").strip().upper()
        area_code = str(row.get("area_code") or "").strip()
        parent_code = str(row.get("parent_request_code") or "").strip()
        if level_type == "GRID" and area_code in affected_grid_codes:
            return True
        if level_type == "CHANNEL" and parent_code in affected_manager_codes:
            return True
        return False

    def obs_in_affected_subtree(item: dict[str, Any]) -> bool:
        parent_type = str(item.get("parent_type") or "").strip().upper()
        parent_code = str(item.get("parent_code") or "").strip()
        if parent_type == "GRID" and parent_code in affected_grid_codes:
            return True
        if parent_type == "CHANNEL_MANAGER" and parent_code in affected_manager_codes:
            return True
        return False

    def error_in_affected_subtree(item: dict[str, Any]) -> bool:
        target_type = str(item.get("target_type") or "").strip().upper()
        target_code = str(item.get("target_code") or "").strip()
        if target_type == "GRID" and target_code in affected_grid_codes:
            return True
        if target_type == "CHANNEL_MANAGER" and target_code in affected_manager_codes:
            return True
        return False

    merged_rows = [
        row
        for row in base_result.get("rows", [])
        if not row_in_affected_subtree(row)
    ] + list(retry_result.get("rows", []))
    merged_observations = [
        item
        for item in base_result.get("structure_observations", [])
        if not obs_in_affected_subtree(item)
    ] + list(retry_result.get("structure_observations", []))
    merged_recoverable_errors = [
        item
        for item in base_result.get("recoverable_errors", [])
        if not error_in_affected_subtree(item)
    ] + list(retry_result.get("recoverable_errors", []))

    return {
        **retry_result,
        "rows": merged_rows,
        "structure_observations": merged_observations,
        "request_count": int(base_result.get("request_count", 0))
        + int(retry_result.get("request_count", 0)),
        "row_count": len(merged_rows),
        "recoverable_errors": merged_recoverable_errors,
        "subtree_retry": {
            "affected_grid_codes": sorted(affected_grid_codes),
            "retry_request_count": retry_result.get("request_count", 0),
        },
    }


def _build_candidate_targets_from_observations(
    rows: list[dict[str, Any]],
    structure_observations: list[dict[str, Any]],
) -> list[CollectionTarget]:
    """从采集行和结构观察构建候选采集目标（内存中，不写库）

    优先级：CHANNEL_MANAGER > GRID > BRANCH > CITY
    """
    graph = build_observed_structure_graph(rows, structure_observations)
    candidate_targets: dict[tuple[str, str], CollectionTarget] = {}
    next_temp_id = -1

    def allocate_id() -> int:
        nonlocal next_temp_id
        value = next_temp_id
        next_temp_id -= 1
        return value

    for identity, node in sorted(
        graph.targets.items(),
        key=lambda item: (_target_sort_key(item[0][0]), item[0][1]),
    ):
        if identity[0] == "CHANNEL_MANAGER":
            continue
        if identity not in candidate_targets:
            candidate_targets[identity] = CollectionTarget(
                id=allocate_id(),
                target_code=node.code,
                target_name=node.name,
                target_type=node.node_type,
                area_id=None,
                parent_target_id=None,
                sort_order=_target_sort_key(node.node_type) * 10,
            )

    manager_edges = sorted(
        [
            edge
            for edge in graph.edges
            if edge.parent_type == "GRID" and edge.child_type == "CHANNEL_MANAGER"
        ],
        key=lambda edge: (edge.parent_code, edge.child_code),
    )
    for edge in manager_edges:
        identity = (edge.child_type, edge.child_code)
        node = graph.targets.get(identity)
        if node is None:
            continue
        if identity not in candidate_targets:
            parent_target = candidate_targets.get(("GRID", edge.parent_code))
            candidate_targets[identity] = CollectionTarget(
                id=allocate_id(),
                target_code=node.code,
                target_name=node.name,
                target_type=node.node_type,
                area_id=None,
                parent_target_id=parent_target.id if parent_target else None,
                sort_order=_target_sort_key(node.node_type) * 10,
            )

    ordered = sorted(
        candidate_targets.values(),
        key=lambda t: (t.sort_order, t.target_code),
    )
    return ordered


def validate_rows_with_candidate_area_map(
    rows: list[dict[str, Any]],
    indicator_codes: Iterable[str],
    recoverable_errors: Iterable[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """用本轮采集行构建候选 area_map，并验证候选结构自身完整性。"""
    errors = list(recoverable_errors or [])
    if errors:
        preview = "；".join(
            f"{item.get('target_type')}/{item.get('target_code')}: {item.get('error')}"
            for item in errors[:5]
        )
        suffix = f" 等{len(errors)}个" if len(errors) > 5 else ""
        raise AreaCoverageError(
            f"候选结构仍存在可恢复采集错误，不能作为最终结构入库: {preview}{suffix}"
        )

    candidate_area_map = _build_candidate_area_map(rows)
    if not candidate_area_map:
        raise AreaCoverageError("候选结构为空，不能作为最终结构入库")

    result = validate_metric_rows(rows, candidate_area_map)
    if result.get("skipped_row_count", 0):
        raise AreaCoverageError(
            f"候选结构存在跳过行: skipped={result.get('skipped_row_count')}"
        )
    ensure_complete_area_coverage(result, len(candidate_area_map))

    from services.dashboard_metric_store import parse_metric_value

    codes = list(indicator_codes)
    for row in result["matched_rows"]:
        for code in codes:
            parse_metric_value(row.get(code))
    return result


def normalize_collection_retry_strategy(value: str | None) -> str:
    strategy = str(value or "affected_grid").strip().lower()
    if strategy in {"affected_grid", "affected-grid", "subtree"}:
        return "affected_grid"
    if strategy == "full":
        return "full"
    raise ValueError(
        "collection_retry_strategy 只支持 affected_grid 或 full: "
        f"{value!r}"
    )


def _sync_structure_in_transaction(
    engine: Engine,
    rows: list[dict[str, Any]],
    structure_observations: list[dict[str, Any]],
    collected_at: datetime,
) -> dict[str, Any]:
    """在事务中同步结构和指标（用于最终提交）"""
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)

    area_sync_result: dict[str, int] = {}
    target_sync_result: dict[str, int] = {}

    with session_factory.begin() as session:
        area_sync_result = _sync_areas_in_session(
            session,
            rows,
            collected_at,
        )
        target_sync_result = _sync_targets_in_session(
            session,
            rows,
            structure_observations,
            collected_at,
        )

    return {
        "area": area_sync_result,
        "request_target": target_sync_result,
    }


def sync_structure_in_session(
    session: Session,
    rows: list[dict[str, Any]],
    structure_observations: list[dict[str, Any]],
    collected_at: datetime,
    change_plan: dict[str, Any] | None = None,
    relation_missing_disable_threshold: int = 2,
) -> dict[str, Any]:
    """同步结构到调用方事务中，供最终提交阶段原子写入。"""
    removed_areas = (change_plan or {}).get("removed_areas") or []
    removed_targets = (change_plan or {}).get("removed_targets") or []
    area_result = _sync_areas_in_session(
        session,
        rows,
        collected_at,
        removed_areas=removed_areas,
    )
    target_result = _sync_targets_in_session(
        session,
        rows,
        structure_observations,
        collected_at,
        removed_targets=removed_targets,
    )
    relation_result = _sync_manager_channel_relations_in_session(
        session,
        structure_observations,
        collected_at,
        removed_targets=removed_targets,
        missing_disable_threshold=relation_missing_disable_threshold,
    )
    return {
        "area": area_result,
        "request_target": target_result,
        "channel_manager_area": relation_result,
    }


def attach_area_ids_in_session(
    session: Session,
    rows: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    """基于事务内最新 area 表，为采集行补齐正式 area_id。"""
    areas = {
        (area.level_type, area.area_code): area.id
        for area in session.scalars(
            select(Area).where(Area.enabled.is_(True))
        ).all()
    }
    resolved: list[dict[str, Any]] = []
    missing: list[dict[str, str]] = []
    for source_row in rows:
        row = dict(source_row)
        level_type = str(row.get("level_type") or "").strip().upper()
        area_code = str(row.get("area_code") or "").strip()
        area_id = areas.get((level_type, area_code))
        if area_id is None:
            if level_type and area_code:
                missing.append(
                    {
                        "level_type": level_type,
                        "area_code": area_code,
                        "area_name": str(row.get("area_name") or "").strip(),
                    }
                )
            continue
        row["area_id"] = area_id
        row["level_type"] = level_type
        row["area_code"] = area_code
        resolved.append(row)
    if missing:
        preview = "；".join(
            f"{item['level_type']}/{item['area_code']}/{item['area_name']}"
            for item in missing[:10]
        )
        suffix = f" 等{len(missing)}行" if len(missing) > 10 else ""
        raise AreaIdResolutionError(
            f"结构同步后仍有采集行无法匹配正式 area_id: {preview}{suffix}"
        )
    return resolved


def _sync_areas_in_session(
    session: Session,
    rows: Iterable[dict[str, Any]],
    collected_at: datetime,
    removed_areas: Iterable[dict[str, Any]] | None = None,
) -> dict[str, int]:
    """在 session 内同步 area 表"""
    from sqlalchemy import and_, select

    created = 0
    updated = 0
    disabled = 0

    area_updates: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        level_type = str(row.get("level_type") or "").strip().upper()
        area_code = str(row.get("area_code") or "").strip()
        area_name = str(row.get("area_name") or "").strip()
        parent_request_code = str(row.get("parent_request_code") or "").strip() or None

        if not level_type or not area_code:
            continue
        if level_type not in {"CITY", "BRANCH", "GRID", "CHANNEL"}:
            continue

        identity = (level_type, area_code)
        if area_name:
            area_updates[identity] = {
                "area_code": area_code,
                "area_name": area_name,
                "level_type": level_type,
                "level_no": _level_no(level_type),
                "parent_request_code": parent_request_code,
            }

    existing_areas = {
        (area.level_type, area.area_code): area
        for area in session.scalars(
            select(Area).where(Area.enabled.is_(True))
        ).all()
    }
    area_ids = {
        identity: area.id
        for identity, area in existing_areas.items()
    }
    manager_grid_area_ids = _load_manager_grid_area_ids(session)

    for identity, data in area_updates.items():
        parent_id = _resolve_parent_area_id(
            area_ids,
            manager_grid_area_ids,
            data["level_type"],
            data["area_code"],
            data.get("parent_request_code"),
        )
        if identity not in existing_areas:
            area = Area(
                area_code=data["area_code"],
                area_name=data["area_name"],
                level_type=data["level_type"],
                level_no=data["level_no"],
                parent_id=parent_id,
                enabled=True,
                missing_count=0,
                last_seen_at=collected_at,
            )
            session.add(area)
            session.flush()
            existing_areas[identity] = area
            area_ids[identity] = area.id
            created += 1
        else:
            area = existing_areas[identity]
            changed = False
            if area.area_name != data["area_name"]:
                area.area_name = data["area_name"]
                changed = True
                updated += 1
            if area.parent_id != parent_id:
                area.parent_id = parent_id
                changed = True
                updated += 1
            if not area.enabled:
                area.enabled = True
                changed = True
                updated += 1
            if area.missing_count != 0:
                area.missing_count = 0
                changed = True
                updated += 1
            if changed:
                area.last_seen_at = collected_at

    for item in removed_areas or []:
        identity = (
            str(item.get("level_type") or "").strip().upper(),
            str(item.get("area_code") or "").strip(),
        )
        if identity[0] != "CHANNEL":
            continue
        area = existing_areas.get(identity)
        if area is not None and area.enabled:
            area.enabled = False
            area.missing_count += 1
            area.last_seen_at = collected_at
            disabled += 1

    return {"created": created, "updated": updated, "disabled": disabled}


def _sync_targets_in_session(
    session: Session,
    rows: Iterable[dict[str, Any]],
    structure_observations: Iterable[dict[str, Any]],
    collected_at: datetime,
    removed_targets: Iterable[dict[str, Any]] | None = None,
) -> dict[str, int]:
    """在 session 内同步 request_target 表"""
    from sqlalchemy import and_, select

    observed_targets: dict[tuple[str, str], dict[str, Any]] = {}

    for row in rows:
        level_type = str(row.get("level_type") or "").strip().upper()
        area_code = str(row.get("area_code") or "").strip()
        area_name = str(row.get("area_name") or "").strip()
        if level_type not in {"CITY", "BRANCH", "GRID"}:
            continue
        if not area_code or not area_name:
            continue
        observed_targets[(level_type, area_code)] = {
            "target_type": level_type,
            "target_code": area_code,
            "target_name": area_name,
        }

    for item in structure_observations:
        parent_type = str(item.get("parent_type") or "").strip().upper()
        child_type = str(item.get("child_type") or "").strip().upper()
        child_code = str(item.get("child_code") or "").strip()
        child_name = str(item.get("child_name") or "").strip()
        parent_code = str(item.get("parent_code") or "").strip()
        if parent_type != "GRID" or child_type != "CHANNEL_MANAGER":
            continue
        if not child_code or not child_name or not parent_code:
            continue
        observed_targets[(child_type, child_code)] = {
            "target_type": child_type,
            "target_code": child_code,
            "target_name": child_name,
            "parent_code": parent_code,
            "parent_type": "GRID",
        }

    created = 0
    updated = 0
    disabled = 0

    areas = {
        (area.level_type, area.area_code): area
        for area in session.scalars(select(Area).where(Area.enabled.is_(True))).all()
    }
    existing_targets = {
        (target.target_type, target.target_code): target
        for target in session.scalars(
            select(RequestTarget).where(RequestTarget.enabled.is_(True))
        ).all()
    }
    all_targets = {
        (target.target_type, target.target_code): target
        for target in session.scalars(select(RequestTarget)).all()
    }

    ordered_identities = sorted(
        observed_targets.keys(),
        key=lambda identity: _target_sort_key(identity[0]),
    )
    resolved_ids: dict[tuple[str, str], int] = {}

    for identity in ordered_identities:
        data = observed_targets[identity]
        target_type = data["target_type"]
        target_code = data["target_code"]
        target_name = data["target_name"]

        area_id = None
        if target_type in {"CITY", "BRANCH", "GRID"}:
            area = areas.get((target_type, target_code))
            area_id = area.id if area else None

        with session.no_autoflush:
            parent_target_id = _resolve_parent_target_id_in_session(
                session,
                all_targets,
                resolved_ids,
                target_type,
                target_code,
                observed_targets,
                data,
            )

        target = all_targets.get(identity)
        if target is None:
            sort_order = area.sort_order if area_id and area else _default_sort_order(target_type)
            target = RequestTarget(
                target_code=target_code,
                target_name=target_name,
                target_type=target_type,
                area_id=area_id,
                parent_target_id=parent_target_id,
                enabled=True,
                sort_order=sort_order,
            )
            session.add(target)
            session.flush()
            all_targets[identity] = target
            existing_targets[identity] = target
            created += 1
        else:
            changed = False
            if target.target_name != target_name:
                target.target_name = target_name
                changed = True
            if target.area_id != area_id:
                target.area_id = area_id
                changed = True
            if target.parent_target_id != parent_target_id:
                target.parent_target_id = parent_target_id
                changed = True
            if not target.enabled:
                target.enabled = True
                changed = True
            if changed:
                updated += 1
        resolved_ids[identity] = target.id

    for item in removed_targets or []:
        identity = (
            str(item.get("target_type") or "").strip().upper(),
            str(item.get("target_code") or "").strip(),
        )
        if identity[0] != "CHANNEL_MANAGER":
            continue
        target = all_targets.get(identity)
        if target is not None and target.enabled:
            target.enabled = False
            updated += 1
            disabled += 1

    return {"created": created, "updated": updated, "disabled": disabled}


def _sync_manager_channel_relations_in_session(
    session: Session,
    structure_observations: Iterable[dict[str, Any]],
    collected_at: datetime,
    removed_targets: Iterable[dict[str, Any]] | None = None,
    missing_disable_threshold: int = 2,
) -> dict[str, int]:
    """同步 CHANNEL_MANAGER -> CHANNEL 关系。"""
    if missing_disable_threshold <= 0:
        raise ValueError("missing_disable_threshold 必须大于 0")

    if not inspect(session.connection()).has_table("channel_manager_area"):
        return {"created": 0, "updated": 0, "disabled": 0, "skipped": 1}

    created = 0
    updated = 0
    disabled = 0
    missing_incremented = 0

    observed_by_manager: dict[str, set[str]] = {}
    observed_manager_codes: set[str] = set()
    for item in structure_observations:
        parent_type = str(item.get("parent_type") or "").strip().upper()
        child_type = str(item.get("child_type") or "").strip().upper()
        if parent_type == "GRID" and child_type == "CHANNEL_MANAGER":
            manager_code = str(item.get("child_code") or "").strip()
            if manager_code:
                observed_manager_codes.add(manager_code)
            continue
        if parent_type != "CHANNEL_MANAGER" or child_type != "CHANNEL":
            continue
        manager_code = str(item.get("parent_code") or "").strip()
        channel_code = str(item.get("child_code") or "").strip()
        if manager_code:
            observed_manager_codes.add(manager_code)
            if channel_code:
                observed_by_manager.setdefault(manager_code, set()).add(channel_code)

    removed_manager_codes = {
        str(item.get("target_code") or "").strip()
        for item in removed_targets or []
        if str(item.get("target_type") or "").strip().upper() == "CHANNEL_MANAGER"
    }
    manager_codes = observed_manager_codes | removed_manager_codes
    if not manager_codes:
        return {"created": 0, "updated": 0, "disabled": 0}

    managers = {
        target.target_code: target
        for target in session.scalars(
            select(RequestTarget).where(
                RequestTarget.target_type == "CHANNEL_MANAGER",
                RequestTarget.target_code.in_(manager_codes),
            )
        ).all()
    }
    channels = {
        area.area_code: area
        for area in session.scalars(
            select(Area).where(Area.level_type == "CHANNEL")
        ).all()
    }
    manager_ids = [target.id for target in managers.values()]
    existing = {
        (relation.manager_target_id, relation.channel_area_id): relation
        for relation in session.scalars(
            select(ChannelManagerArea).where(
                ChannelManagerArea.manager_target_id.in_(manager_ids or [-1])
            )
        ).all()
    }
    parent_target_ids = {
        target.parent_target_id
        for target in managers.values()
        if target.parent_target_id is not None
    }
    grid_area_by_target_id = {
        target.id: target.area_id
        for target in session.scalars(
            select(RequestTarget).where(
                RequestTarget.id.in_(parent_target_ids or {-1})
            )
        ).all()
        if target.area_id is not None
    }

    observed_relation_keys: set[tuple[int, int]] = set()
    observed_channel_area_ids: set[int] = set()
    for manager_code, channel_codes in observed_by_manager.items():
        manager = managers.get(manager_code)
        if manager is None:
            continue
        grid_area_id = grid_area_by_target_id.get(manager.parent_target_id)
        for channel_code in channel_codes:
            channel = channels.get(channel_code)
            if channel is None:
                continue
            key = (manager.id, channel.id)
            observed_relation_keys.add(key)
            observed_channel_area_ids.add(channel.id)
            relation = existing.get(key)
            if relation is None:
                session.add(
                    ChannelManagerArea(
                        manager_target_id=manager.id,
                        channel_area_id=channel.id,
                        grid_area_id=grid_area_id,
                        enabled=True,
                        last_seen_at=collected_at,
                        missing_count=0,
                    )
                )
                created += 1
            else:
                changed = False
                if relation.grid_area_id != grid_area_id:
                    relation.grid_area_id = grid_area_id
                    changed = True
                if not relation.enabled:
                    relation.enabled = True
                    changed = True
                if relation.missing_count != 0:
                    relation.missing_count = 0
                    changed = True
                if changed:
                    relation.last_seen_at = collected_at
                    updated += 1

    observed_manager_ids = {
        managers[manager_code].id
        for manager_code in observed_manager_codes
        if manager_code in managers
    }
    removed_manager_ids = {
        managers[manager_code].id
        for manager_code in removed_manager_codes
        if manager_code in managers
    }
    for key, relation in existing.items():
        manager_id = key[0]
        channel_area_id = key[1]
        missing_from_observed_manager = (
            manager_id in observed_manager_ids
            and key not in observed_relation_keys
        )
        should_disable_immediately = (
            manager_id in removed_manager_ids
            or (
                missing_from_observed_manager
                and channel_area_id in observed_channel_area_ids
            )
        )
        should_count_missing = (
            missing_from_observed_manager
            and channel_area_id not in observed_channel_area_ids
        )
        if should_disable_immediately and relation.enabled:
            relation.enabled = False
            relation.missing_count += 1
            relation.last_seen_at = collected_at
            disabled += 1
        elif should_count_missing and relation.enabled:
            relation.missing_count += 1
            relation.last_seen_at = collected_at
            missing_incremented += 1
            if relation.missing_count >= missing_disable_threshold:
                relation.enabled = False
                disabled += 1

    return {
        "created": created,
        "updated": updated,
        "disabled": disabled,
        "missing_incremented": missing_incremented,
        "missing_disable_threshold": missing_disable_threshold,
    }


def _level_no(level_type: str) -> int:
    mapping = {
        "CITY": 1,
        "BRANCH": 2,
        "GRID": 3,
        "CHANNEL": 4,
    }
    return mapping.get(level_type, 0)


def _target_sort_key(target_type: str) -> int:
    order = {
        "CITY": 1,
        "BRANCH": 2,
        "GRID": 3,
        "CHANNEL_MANAGER": 4,
    }
    return order.get(target_type, 99)


def _default_sort_order(target_type: str) -> int:
    return _target_sort_key(target_type) * 10


def _load_manager_grid_area_ids(session: Session) -> dict[str, int]:
    targets = session.scalars(
        select(RequestTarget).where(
            RequestTarget.target_type.in_(["GRID", "CHANNEL_MANAGER"]),
            RequestTarget.enabled.is_(True),
        )
    ).all()
    grid_area_by_target_id = {
        target.id: target.area_id
        for target in targets
        if target.target_type == "GRID" and target.area_id is not None
    }
    return {
        target.target_code: grid_area_by_target_id[target.parent_target_id]
        for target in targets
        if target.target_type == "CHANNEL_MANAGER"
        and target.parent_target_id in grid_area_by_target_id
    }


def _resolve_parent_area_id(
    area_ids: dict[tuple[str, str], int],
    manager_grid_area_ids: dict[str, int],
    level_type: str,
    area_code: str,
    parent_request_code: str | None = None,
) -> int | None:
    if level_type == "BRANCH":
        return area_ids.get(("CITY", "A"))
    if level_type == "GRID":
        branch_code = area_code[:2] if len(area_code) >= 2 else ""
        return area_ids.get(("BRANCH", branch_code))
    if level_type == "CHANNEL":
        if parent_request_code:
            grid_area_id = manager_grid_area_ids.get(parent_request_code)
            if grid_area_id is not None:
                return grid_area_id
        grid_code = area_code[:2] if len(area_code) >= 2 else ""
        return area_ids.get(("GRID", grid_code))
    return None


def _find_parent_id_in_session(
    session: Session,
    level_type: str,
    area_code: str,
    parent_request_code: str | None = None,
) -> int | None:
    from sqlalchemy import and_, select

    parent_mapping = {
        "BRANCH": ("CITY", "A"),
        "GRID": ("BRANCH", None),
        "CHANNEL": ("GRID", None),
    }

    if level_type not in parent_mapping:
        return None

    parent_type, parent_code_hint = parent_mapping[level_type]

    if parent_code_hint:
        parent = session.scalar(
            select(Area).where(
                and_(
                    Area.level_type == parent_type,
                    Area.area_code == parent_code_hint,
                    Area.enabled.is_(True),
                )
            )
        )
        return parent.id if parent else None

    if level_type == "CHANNEL" and parent_request_code:
        grid_area_id = _find_grid_area_id_from_manager_in_session(session, parent_request_code)
        if grid_area_id is not None:
            return grid_area_id

    if level_type == "GRID":
        branch_code = area_code[:2] if len(area_code) >= 2 else None
        if branch_code:
            parent = session.scalar(
                select(Area).where(
                    and_(
                        Area.level_type == "BRANCH",
                        Area.area_code == branch_code,
                        Area.enabled.is_(True),
                    )
                )
            )
            return parent.id if parent else None

    if level_type == "CHANNEL":
        if len(area_code) >= 2:
            grid_code = area_code[:2]
            parent = session.scalar(
                select(Area).where(
                    and_(
                        Area.level_type == "GRID",
                        Area.area_code == grid_code,
                        Area.enabled.is_(True),
                    )
                )
            )
            return parent.id if parent else None

    return None


def _find_grid_area_id_from_manager_in_session(
    session: Session,
    manager_code: str,
) -> int | None:
    from sqlalchemy import and_, select

    manager_target = session.scalar(
        select(RequestTarget).where(
            and_(
                RequestTarget.target_type == "CHANNEL_MANAGER",
                RequestTarget.target_code == manager_code,
                RequestTarget.enabled.is_(True),
            )
        )
    )
    if manager_target is None or manager_target.parent_target_id is None:
        return None
    grid_target = session.scalar(
        select(RequestTarget).where(RequestTarget.id == manager_target.parent_target_id)
    )
    if grid_target is None:
        return None
    return grid_target.area_id


def _resolve_parent_target_id_in_session(
    session: Session,
    all_targets: dict[tuple[str, str], RequestTarget],
    resolved_ids: dict[tuple[str, str], int],
    target_type: str,
    target_code: str,
    observed_targets: dict[tuple[str, str], dict[str, Any]],
    data: dict[str, Any],
) -> int | None:
    from sqlalchemy import and_, select

    if target_type == "CITY":
        return None

    if target_type == "BRANCH":
        parent_identity = ("CITY", "A")
    elif target_type == "GRID":
        parent_identity = ("BRANCH", target_code[:2]) if target_code else None
    elif target_type == "CHANNEL_MANAGER":
        parent_code = str(data.get("parent_code") or "").strip()
        parent_identity = ("GRID", parent_code) if parent_code else None
    else:
        parent_identity = None

    if not parent_identity:
        return None
    if parent_identity in resolved_ids:
        return resolved_ids[parent_identity]
    parent = all_targets.get(parent_identity)
    if parent is not None:
        return parent.id
    if parent_identity in observed_targets:
        parent = session.scalar(
            select(RequestTarget).where(
                and_(
                    RequestTarget.target_type == parent_identity[0],
                    RequestTarget.target_code == parent_identity[1],
                )
            )
        )
        return parent.id if parent else None
    return None


def collect_validate_metric_rows_simple(
    *,
    engine: Engine,
    run_store: CollectionRunStore,
    batch_no: str,
    targets: list[CollectionTarget],
    indicator_codes: list[str],
    fetch_metrics: Callable[[CollectionTarget], dict[str, Any]],
    max_workers: int,
    hard_limit: int,
    anomaly_directory: Path,
    failure_directory: Path,
    event_logger: Any = None,
    retry_strategy: str = "affected_grid",
) -> dict[str, Any]:
    """简化版采集验证 - 按新流程图实现

    流程（按推荐流程图）：
    1. 采集（只收集 observation，不更新结构）
    2. 首轮校验与判定
       - 校验通过 + 无结构漂移 → 不消费 observation，直接进入最终事务
       - 校验失败 + 确认结构漂移 → 消费 observation 更新结构，重采
       - 校验失败 + 非结构问题 → 失败结束
    3. 重采分支（仅结构漂移时）
    4. 最终 MySQL 短事务提交结构和指标
    """
    max_attempts = 2
    attempt = 0
    logger = event_logger or logging.getLogger(__name__)
    retry_strategy = normalize_collection_retry_strategy(retry_strategy)
    timings: list[dict[str, Any]] = []

    validated_rows: list[dict[str, Any]] | None = None
    structure_observations: list[dict[str, Any]] = []
    final_collection_result: dict[str, Any] | None = None
    final_validation_result: dict[str, Any] | None = None
    change_plan: dict[str, Any] = {
        "expanded": False,
        "removed_areas": [],
        "removed_targets": [],
        "relation_bootstrap": False,
    }
    subtree_retry_context: dict[str, Any] | None = None
    last_graph_diff: StructureDiff | None = None
    last_affected_grid_codes: set[str] = set()

    area_map = load_enabled_area_map(engine)
    current_graph = load_current_structure_graph(engine, targets)
    manager_relation_table_exists = inspect(engine).has_table("channel_manager_area")
    relation_bootstrap_needed = (
        manager_relation_table_exists
        and not has_manager_channel_relations(engine)
    )
    compare_manager_channel_relations = (
        manager_relation_table_exists
        and not relation_bootstrap_needed
    )
    if relation_bootstrap_needed:
        change_plan["relation_bootstrap"] = True
    expected_area_count = len(area_map)

    for attempt in range(max_attempts):
        attempt_no = attempt + 1

        run_store.update(batch_no, status="RUNNING", phase="COLLECT")
        collect_started = perf_counter()
        collection_result = execute_collection_phase(
            batch_no,
            targets,
            indicator_codes,
            fetch_metrics,
            run_store,
            max_workers=max_workers,
            hard_limit=hard_limit,
            now_provider=naive_shanghai_now,
            allow_recoverable_manager_failures=(attempt == 0),
        )
        collect_seconds = perf_counter() - collect_started
        if subtree_retry_context is not None:
            collection_result = _merge_subtree_retry_collection(
                subtree_retry_context["base_collection"],
                collection_result,
                subtree_retry_context["affected_grid_codes"],
                subtree_retry_context["affected_manager_codes"],
            )
        final_collection_result = collection_result
        structure_observations = collection_result.get("structure_observations", [])
        observed_graph = build_observed_structure_graph(
            collection_result.get("rows", []),
            structure_observations,
        )
        graph_diff = diff_structure_graph(
            current_graph,
            observed_graph,
            recoverable_errors=bool(collection_result.get("recoverable_errors")),
            compare_manager_channel_relations=compare_manager_channel_relations,
        )
        last_graph_diff = graph_diff

        run_store.update(batch_no, status="RUNNING", phase="VALIDATE")

        validate_started = perf_counter()
        try:
            validation_result = execute_area_validation_phase(
                batch_no,
                collection_result["rows"],
                engine,
                run_store,
                anomaly_directory,
                failure_directory=failure_directory,
                now_provider=naive_shanghai_now,
                area_map=area_map,
            )
            final_validation_result = validation_result
            structure_drift_detected = False
            removed_channel_areas = []
        except Exception as validation_error:
            validate_seconds = perf_counter() - validate_started

            error_result = getattr(validation_error, "result", {})
            unmatched_count = error_result.get("unmatched_area_count", 0)
            is_structure_drift = (
                unmatched_count > 0 and
                detect_structure_drift(
                    error_result,
                    structure_observations,
                )
            )

            if not is_structure_drift:
                logger.warning(
                    f"第{attempt_no}轮验证失败: 非结构问题导致失败，终止采集"
                )
                raise

            logger.info(
                f"第{attempt_no}轮检测到结构漂移: unmatched={unmatched_count}, "
                f"将基于候选结构重采"
            )
            final_validation_result = {
                "matched_area_count": 0,
                "matched_row_count": 0,
                "matched_rows": [],
                "unmatched_area_count": unmatched_count,
            }
            structure_drift_detected = True
            removed_channel_areas = []

        validate_seconds = perf_counter() - validate_started

        matched = final_validation_result.get("matched_area_count", 0)
        matched_rows = final_validation_result.get("matched_rows", [])
        if graph_diff.target_or_relation_changed:
            structure_drift_detected = True
            change_plan["removed_targets"] = graph_diff.removed_targets
            change_plan["expanded"] = True
        if graph_diff.removed_areas:
            structure_drift_detected = True
            change_plan["removed_areas"] = graph_diff.removed_areas

        timings.append(
            {
                "attempt": attempt_no,
                "collect_seconds": round(collect_seconds, 3),
                "validate_seconds": round(validate_seconds, 3),
                "request_count": collection_result.get("request_count", 0),
                "row_count": collection_result.get("row_count", 0),
                "matched_area_count": matched,
                "matched_row_count": final_validation_result.get("matched_row_count", 0),
                "structure_drift_detected": False,
                "manager_target_drift": bool(
                    graph_diff.added_targets
                    or graph_diff.removed_targets
                    or graph_diff.changed_targets
                ),
                "manager_channel_relation_drift": bool(
                    graph_diff.added_relations or graph_diff.removed_relations
                ),
                "manager_channel_relation_bootstrap": relation_bootstrap_needed,
                "retry_strategy": retry_strategy,
            }
        )

        if (
            matched == expected_area_count
            and final_validation_result.get("matched_row_count") == expected_area_count
            and not structure_drift_detected
        ):
            logger.info(
                f"验证通过: matched={matched}, expected={expected_area_count}, attempt={attempt_no}"
            )
            validated_rows = matched_rows
            run_store.update(batch_no, status="RUNNING", phase="AREA_VALIDATED")
            break

        removed_channel_areas = graph_diff.removed_areas
        if removed_channel_areas:
            logger.warning(
                "第%s轮检测到渠道收缩候选: removed=%s，将基于候选结构重采",
                attempt_no,
                len(removed_channel_areas),
            )

        if attempt < max_attempts - 1 and structure_drift_detected:
            logger.warning(
                f"第{attempt_no}轮验证未通过: matched={matched}, expected={expected_area_count}, "
                f"检测到结构漂移，将基于候选结构重采（不立即写库）"
            )
            timings[-1]["structure_drift_detected"] = True

            run_store.update(batch_no, status="RUNNING", phase="PREPARE_CANDIDATE")
            candidate_started = perf_counter()

            if removed_channel_areas and not graph_diff.target_or_relation_changed:
                affected_grid_codes = _affected_grid_codes_from_diff(
                    graph_diff,
                    current_graph,
                    observed_graph,
                )
                last_affected_grid_codes = affected_grid_codes
                affected_manager_codes = _manager_codes_for_grids(
                    current_graph,
                    affected_grid_codes,
                ) | _manager_codes_for_grids(
                    observed_graph,
                    affected_grid_codes,
                )
                if retry_strategy == "affected_grid":
                    retry_targets = _build_subtree_retry_targets(
                        targets,
                        collection_result["rows"],
                        structure_observations,
                        affected_grid_codes,
                    )
                    if retry_targets:
                        targets = retry_targets
                        subtree_retry_context = {
                            "base_collection": collection_result,
                            "affected_grid_codes": affected_grid_codes,
                            "affected_manager_codes": affected_manager_codes,
                        }
                    else:
                        subtree_retry_context = None
                else:
                    subtree_retry_context = None
                area_map = without_removed_areas(area_map, removed_channel_areas)
            else:
                affected_grid_codes = _affected_grid_codes_from_diff(
                    graph_diff,
                    current_graph,
                    observed_graph,
                )
                last_affected_grid_codes = affected_grid_codes
                affected_manager_codes = _manager_codes_for_grids(
                    current_graph,
                    affected_grid_codes,
                ) | _manager_codes_for_grids(
                    observed_graph,
                    affected_grid_codes,
                )
                if retry_strategy == "affected_grid":
                    candidate_targets = _build_subtree_retry_targets(
                        targets,
                        collection_result["rows"],
                        structure_observations,
                        affected_grid_codes,
                    )
                else:
                    candidate_targets = []
                if not candidate_targets:
                    candidate_targets = _build_candidate_targets_from_observations(
                        collection_result["rows"],
                        structure_observations,
                    )
                    subtree_retry_context = None
                else:
                    subtree_retry_context = {
                        "base_collection": collection_result,
                        "affected_grid_codes": affected_grid_codes,
                        "affected_manager_codes": affected_manager_codes,
                    }
                targets = candidate_targets
                area_map = _build_candidate_area_map(collection_result["rows"])
                change_plan["expanded"] = True
            expected_area_count = len(area_map)

            candidate_seconds = perf_counter() - candidate_started
            timings[-1]["candidate_build_seconds"] = round(candidate_seconds, 3)
            timings[-1]["subtree_retry"] = {
                "affected_grid_codes": sorted(
                    subtree_retry_context["affected_grid_codes"]
                    if subtree_retry_context is not None
                    else []
                ),
                "retry_target_count": len(targets),
            }
            continue

        if structure_drift_detected and attempt == max_attempts - 1:
            candidate_result = validate_rows_with_candidate_area_map(
                collection_result["rows"],
                indicator_codes,
                collection_result.get("recoverable_errors"),
            )
            final_validation_result = candidate_result
            validated_rows = candidate_result["matched_rows"]
            final_diff = diff_structure_graph(
                current_graph,
                observed_graph,
                recoverable_errors=bool(collection_result.get("recoverable_errors")),
                compare_manager_channel_relations=compare_manager_channel_relations,
            )
            last_graph_diff = final_diff
            change_plan["removed_areas"] = final_diff.removed_areas
            change_plan["removed_targets"] = final_diff.removed_targets
            run_store.update(batch_no, status="RUNNING", phase="AREA_VALIDATED")
            break

        ensure_complete_area_coverage(final_validation_result, expected_area_count)

    if not validated_rows and final_validation_result:
        ensure_complete_area_coverage(final_validation_result, expected_area_count)

    empty_diff = StructureDiff([], [], [], [], [], [])
    structure_change_summary = summarize_structure_changes(
        last_graph_diff or empty_diff,
        retry_strategy=retry_strategy,
        relation_bootstrap=bool(change_plan.get("relation_bootstrap")),
        affected_grid_codes=last_affected_grid_codes,
    )

    return {
        "collection": final_collection_result,
        "validation": final_validation_result,
        "validated_rows": validated_rows,
        "structure_observations": structure_observations,
        "attempt_timings": timings,
        "attempts": attempt + 1,
        "structure_changed": (
            any(t.get("structure_drift_detected", False) for t in timings)
            or bool(change_plan.get("relation_bootstrap"))
        ),
        "change_plan": change_plan,
        "structure_change_summary": structure_change_summary,
    }
