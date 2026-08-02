"""Dashboard V2 unified hierarchy bootstrap and persistence helpers."""

from __future__ import annotations

import csv
import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from models.dashboard_v2 import HierarchyNode, HierarchyParentHistory
from services.dashboard_structure import (
    StructureEdge,
    StructureGraph,
    StructureNode,
    build_observed_structure_graph,
)
from services.dashboard_collection_runtime import CollectionTarget


NODE_LEVELS = {
    "CITY": 1,
    "BRANCH": 2,
    "GRID": 3,
    "CHANNEL_MANAGER": 4,
    "CHANNEL": 5,
}
EXPECTED_PARENT_TYPES = {
    "CITY": None,
    "BRANCH": "CITY",
    "GRID": "BRANCH",
    "CHANNEL_MANAGER": "GRID",
    "CHANNEL": "CHANNEL_MANAGER",
}
REQUEST_NODE_TYPES = {"CITY", "BRANCH", "GRID", "CHANNEL_MANAGER"}
BASE_NODE_TYPES = {"CITY", "BRANCH", "GRID"}


class V2HierarchyError(RuntimeError):
    """The hierarchy input cannot be safely persisted."""

    phase = "VALIDATE_AREA"
    error_type = "INVALID_HIERARCHY"


@dataclass(frozen=True)
class BootstrapNode:
    node_type: str
    node_code: str
    node_name: str
    parent_node_code: str | None
    sort_order: int

    @property
    def identity(self) -> tuple[str, str]:
        return (self.node_type, self.node_code)


def load_bootstrap_manifest(path: str | Path) -> list[BootstrapNode]:
    """Load an approved CITY/BRANCH/GRID manifest from JSON or CSV."""
    resolved = Path(path).resolve()
    suffix = resolved.suffix.lower()
    if suffix == ".json":
        payload = json.loads(resolved.read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            raise V2HierarchyError("基础结构 JSON 顶层必须是数组")
        rows = payload
    elif suffix == ".csv":
        with resolved.open("r", encoding="utf-8-sig", newline="") as stream:
            rows = list(csv.DictReader(stream))
    else:
        raise V2HierarchyError("基础结构清单只支持 .json 或 .csv")
    return validate_bootstrap_manifest(rows)


def validate_bootstrap_manifest(
    rows: Iterable[dict[str, Any]],
) -> list[BootstrapNode]:
    """Normalize and validate the deterministic base hierarchy seed."""
    result: list[BootstrapNode] = []
    identities: set[tuple[str, str]] = set()
    for index, row in enumerate(rows, start=1):
        node_type = str(row.get("node_type") or "").strip().upper()
        node_code = str(row.get("node_code") or "").strip()
        node_name = str(row.get("node_name") or "").strip()
        parent_code = str(row.get("parent_node_code") or "").strip() or None
        if node_type not in BASE_NODE_TYPES:
            raise V2HierarchyError(
                f"基础结构第 {index} 行层级非法: {node_type or '<empty>'}"
            )
        if not node_code or not node_name:
            raise V2HierarchyError(
                f"基础结构第 {index} 行 node_code/node_name 不得为空"
            )
        identity = (node_type, node_code)
        if identity in identities:
            raise V2HierarchyError(f"基础结构节点重复: {node_type}/{node_code}")
        identities.add(identity)
        try:
            sort_order = int(row.get("sort_order") or 0)
        except (TypeError, ValueError) as exc:
            raise V2HierarchyError(
                f"基础结构第 {index} 行 sort_order 必须是整数"
            ) from exc
        if sort_order < 0:
            raise V2HierarchyError(
                f"基础结构第 {index} 行 sort_order 不得小于 0"
            )
        result.append(
            BootstrapNode(
                node_type=node_type,
                node_code=node_code,
                node_name=node_name,
                parent_node_code=parent_code,
                sort_order=sort_order,
            )
        )

    city_nodes = [node for node in result if node.node_type == "CITY"]
    if len(city_nodes) != 1:
        raise V2HierarchyError(
            f"基础结构必须且只能有一个 CITY: count={len(city_nodes)}"
        )
    by_identity = {node.identity: node for node in result}
    for node in result:
        parent_type = EXPECTED_PARENT_TYPES[node.node_type]
        if parent_type is None:
            if node.parent_node_code is not None:
                raise V2HierarchyError(f"CITY 不得有父节点: {node.node_code}")
            continue
        if node.parent_node_code is None:
            raise V2HierarchyError(
                f"基础结构节点缺少父级: {node.node_type}/{node.node_code}"
            )
        parent_identity = (parent_type, node.parent_node_code)
        if parent_identity not in by_identity:
            raise V2HierarchyError(
                f"基础结构父节点不存在: "
                f"{node.node_type}/{node.node_code} -> "
                f"{parent_type}/{node.parent_node_code}"
            )
    return sorted(result, key=lambda item: (NODE_LEVELS[item.node_type], item.sort_order, item.node_code))


def initialize_base_hierarchy_in_session(
    session: Session,
    manifest: Iterable[BootstrapNode],
    *,
    initialized_at: datetime,
) -> dict[str, int]:
    """Idempotently seed CITY/BRANCH/GRID in the caller-owned transaction."""
    nodes = list(manifest)
    if not nodes or any(not isinstance(node, BootstrapNode) for node in nodes):
        raise V2HierarchyError("基础结构清单必须先通过规范化校验")

    existing = {
        (node.node_type, node.node_code): node
        for node in session.scalars(select(HierarchyNode)).all()
    }
    resolved: dict[tuple[str, str], HierarchyNode] = {}
    created = 0
    reused = 0
    history_created = 0

    for source in nodes:
        identity = source.identity
        parent_type = EXPECTED_PARENT_TYPES[source.node_type]
        parent_identity = (
            (parent_type, source.parent_node_code) if parent_type is not None else None
        )
        parent = resolved.get(parent_identity) if parent_identity else None
        if parent_identity and parent is None:
            raise V2HierarchyError(
                f"基础结构未按父子顺序解析: {identity[0]}/{identity[1]}"
            )

        node = existing.get(identity)
        if node is None:
            node = HierarchyNode(
                node_type=source.node_type,
                node_code=source.node_code,
                node_name=source.node_name,
                parent_id=parent.id if parent else None,
                level_no=NODE_LEVELS[source.node_type],
                request_enabled=True,
                metric_enabled=True,
                enabled=True,
                sort_order=source.sort_order,
                last_seen_at=initialized_at,
                missing_count=0,
            )
            session.add(node)
            session.flush()
            existing[identity] = node
            created += 1
        else:
            expected_parent_id = parent.id if parent else None
            mismatches = {
                "node_name": (node.node_name, source.node_name),
                "parent_id": (node.parent_id, expected_parent_id),
                "level_no": (node.level_no, NODE_LEVELS[source.node_type]),
            }
            actual_mismatches = {
                key: value for key, value in mismatches.items() if value[0] != value[1]
            }
            if actual_mismatches:
                raise V2HierarchyError(
                    f"已存基础节点与清单不一致: "
                    f"{source.node_type}/{source.node_code} {actual_mismatches}"
                )
            if not node.enabled or not node.request_enabled or not node.metric_enabled:
                raise V2HierarchyError(
                    f"已存基础节点状态不允许自动覆盖: "
                    f"{source.node_type}/{source.node_code}"
                )
            reused += 1
        resolved[identity] = node

        if parent is None:
            continue
        active_history = session.scalar(
            select(HierarchyParentHistory).where(
                HierarchyParentHistory.child_node_id == node.id,
                HierarchyParentHistory.valid_to.is_(None),
            )
        )
        if active_history is None:
            session.add(
                HierarchyParentHistory(
                    child_node_id=node.id,
                    parent_node_id=parent.id,
                    valid_from=initialized_at,
                    collection_run_id=None,
                    change_type="CREATED",
                )
            )
            history_created += 1
        elif active_history.parent_node_id != parent.id:
            raise V2HierarchyError(
                f"已存基础节点当前父级历史冲突: "
                f"{source.node_type}/{source.node_code}"
            )

    session.flush()
    return {
        "created": created,
        "reused": reused,
        "history_created": history_created,
        "node_count": len(nodes),
    }


def load_v2_collection_targets(engine: Engine) -> list[CollectionTarget]:
    """Load request targets from the unified hierarchy."""
    with Session(engine) as session:
        records = session.scalars(
            select(HierarchyNode)
            .where(
                HierarchyNode.enabled.is_(True),
                HierarchyNode.request_enabled.is_(True),
                HierarchyNode.node_type.in_(REQUEST_NODE_TYPES),
            )
            .order_by(HierarchyNode.sort_order, HierarchyNode.id)
        ).all()
    return [
        CollectionTarget(
            id=record.id,
            target_code=record.node_code,
            target_name=record.node_name,
            target_type=record.node_type,
            area_id=record.id,
            parent_target_id=record.parent_id,
            sort_order=record.sort_order,
        )
        for record in records
    ]


def attach_v2_node_ids_in_session(
    session: Session,
    rows: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Attach committed V2 node ids after candidate hierarchy synchronization."""
    nodes = {
        (node.node_type, node.node_code): node.id
        for node in session.scalars(
            select(HierarchyNode).where(
                HierarchyNode.enabled.is_(True),
                HierarchyNode.metric_enabled.is_(True),
            )
        )
    }
    result: list[dict[str, Any]] = []
    missing: list[tuple[str, str]] = []
    for source in rows:
        row = dict(source)
        identity = (
            str(row.get("node_type") or row.get("level_type") or "")
            .strip()
            .upper(),
            str(row.get("node_code") or row.get("area_code") or "").strip(),
        )
        node_id = nodes.get(identity)
        if node_id is None:
            missing.append(identity)
            continue
        row["node_id"] = node_id
        row["node_type"] = identity[0]
        row["node_code"] = identity[1]
        result.append(row)
    if missing:
        raise V2HierarchyError(f"结构同步后仍有指标行无法关联节点: {missing[:10]}")
    return result


def load_v2_structure_graph(session: Session) -> StructureGraph:
    """Expose enabled V2 nodes through the established drift graph contract."""
    records = session.scalars(
        select(HierarchyNode).where(HierarchyNode.enabled.is_(True))
    ).all()
    by_id = {record.id: record for record in records}
    areas: dict[tuple[str, str], StructureNode] = {}
    targets: dict[tuple[str, str], StructureNode] = {}
    edges: set[StructureEdge] = set()
    for record in records:
        identity = (record.node_type, record.node_code)
        graph_node = StructureNode(
            node_type=record.node_type,
            code=record.node_code,
            name=record.node_name,
            node_id=record.id,
        )
        if record.metric_enabled:
            areas[identity] = graph_node
        if record.request_enabled:
            targets[identity] = graph_node
        if record.parent_id is None:
            continue
        parent = by_id.get(record.parent_id)
        if parent is None:
            raise V2HierarchyError(
                f"启用节点父级不存在或已停用: "
                f"{record.node_type}/{record.node_code}"
            )
        edges.add(
            StructureEdge(
                parent_type=parent.node_type,
                parent_code=parent.node_code,
                child_type=record.node_type,
                child_code=record.node_code,
            )
        )
    graph = StructureGraph(areas=areas, targets=targets, edges=edges)
    validate_v2_structure_graph(graph)
    return graph


def build_v2_observed_graph(
    rows: Iterable[dict[str, Any]],
    structure_observations: Iterable[dict[str, Any]],
) -> StructureGraph:
    """Build an observed graph where managers are metric and request nodes."""
    row_list = list(rows)
    graph = build_observed_structure_graph(row_list, structure_observations)
    areas = dict(graph.areas)
    targets = dict(graph.targets)
    for row in row_list:
        node_type = str(row.get("level_type") or "").strip().upper()
        node_code = str(row.get("area_code") or "").strip()
        node_name = str(row.get("area_name") or "").strip()
        if node_type != "CHANNEL_MANAGER" or not node_code:
            continue
        node = StructureNode(node_type=node_type, code=node_code, name=node_name)
        areas[(node_type, node_code)] = node
        targets[(node_type, node_code)] = node
    for identity, node in targets.items():
        if identity[0] == "CHANNEL_MANAGER":
            areas.setdefault(identity, node)
    return StructureGraph(areas=areas, targets=targets, edges=set(graph.edges))


def validate_v2_structure_graph(graph: StructureGraph) -> None:
    """Reject broken, multi-parent, and incorrectly typed candidate trees."""
    nodes = {**graph.areas, **graph.targets}
    parents: dict[tuple[str, str], list[tuple[str, str]]] = defaultdict(list)
    for edge in graph.edges:
        parent = (edge.parent_type, edge.parent_code)
        child = (edge.child_type, edge.child_code)
        if parent not in nodes:
            raise V2HierarchyError(f"父节点不存在: {parent[0]}/{parent[1]}")
        if child not in nodes:
            raise V2HierarchyError(f"子节点不存在: {child[0]}/{child[1]}")
        if EXPECTED_PARENT_TYPES.get(edge.child_type) != edge.parent_type:
            raise V2HierarchyError(
                f"非法父子层级: {edge.parent_type}/{edge.parent_code} -> "
                f"{edge.child_type}/{edge.child_code}"
            )
        parents[child].append(parent)

    for identity in nodes:
        if identity[0] not in NODE_LEVELS:
            raise V2HierarchyError(f"未知节点类型: {identity[0]}/{identity[1]}")
        assigned = parents.get(identity, [])
        if identity[0] == "CITY":
            if assigned:
                raise V2HierarchyError(f"CITY 不得有父节点: {identity[1]}")
        elif len(assigned) != 1:
            raise V2HierarchyError(
                f"非 CITY 节点必须且只能有一个父节点: "
                f"{identity[0]}/{identity[1]}, count={len(assigned)}"
            )

    parent_by_child = {child: values[0] for child, values in parents.items()}
    for identity in nodes:
        visited: set[tuple[str, str]] = set()
        cursor = identity
        while cursor in parent_by_child:
            if cursor in visited:
                raise V2HierarchyError(f"结构存在循环: {identity[0]}/{identity[1]}")
            visited.add(cursor)
            cursor = parent_by_child[cursor]


def sync_v2_hierarchy_in_session(
    session: Session,
    candidate_graph: StructureGraph,
    *,
    collected_at: datetime,
    collection_run_id: int | None = None,
    removed_identities: Iterable[tuple[str, str]] = (),
    missing_disable_threshold: int = 2,
    complete_structure_reconciliation: bool = False,
) -> dict[str, int]:
    """Persist a complete, validated candidate graph in the caller transaction.

    Removed identities must come from a trusted drift comparison.  This
    function never infers removal from partial response data.
    """
    if missing_disable_threshold <= 0:
        raise ValueError("missing_disable_threshold 必须大于 0")
    validate_v2_structure_graph(candidate_graph)

    candidate_nodes = {**candidate_graph.areas, **candidate_graph.targets}
    parent_by_child = {
        (edge.child_type, edge.child_code): (edge.parent_type, edge.parent_code)
        for edge in candidate_graph.edges
    }
    existing = {
        (node.node_type, node.node_code): node
        for node in session.scalars(select(HierarchyNode)).all()
    }
    active_history_by_child = {
        history.child_node_id: history
        for history in session.scalars(
            select(HierarchyParentHistory).where(
                HierarchyParentHistory.valid_to.is_(None)
            )
        ).all()
    }
    created = updated = moved = restored = disabled = missing_incremented = 0
    disable_deferred = 0

    ordered_identities = sorted(
        candidate_nodes,
        key=lambda identity: (NODE_LEVELS[identity[0]], identity[1]),
    )
    for identity in ordered_identities:
        source = candidate_nodes[identity]
        parent_identity = parent_by_child.get(identity)
        parent = existing.get(parent_identity) if parent_identity else None
        if parent_identity and parent is None:
            raise V2HierarchyError(
                f"候选父节点尚未落库: {parent_identity[0]}/{parent_identity[1]}"
            )

        node = existing.get(identity)
        was_disabled = node is not None and not node.enabled
        old_parent_id = node.parent_id if node is not None else None
        if node is None:
            node = HierarchyNode(
                node_type=source.node_type,
                node_code=source.code,
                node_name=source.name or source.code,
                parent_id=parent.id if parent else None,
                level_no=NODE_LEVELS[source.node_type],
                request_enabled=source.node_type in REQUEST_NODE_TYPES,
                metric_enabled=True,
                enabled=True,
                sort_order=NODE_LEVELS[source.node_type] * 10,
                last_seen_at=collected_at,
                missing_count=0,
            )
            session.add(node)
            session.flush()
            existing[identity] = node
            created += 1
        else:
            changed = False
            values = {
                "node_name": source.name or source.code,
                "parent_id": parent.id if parent else None,
                "level_no": NODE_LEVELS[source.node_type],
                "request_enabled": source.node_type in REQUEST_NODE_TYPES,
                "metric_enabled": True,
                "enabled": True,
                "missing_count": 0,
            }
            for field, value in values.items():
                if getattr(node, field) != value:
                    setattr(node, field, value)
                    changed = True
            node.last_seen_at = collected_at
            if changed:
                updated += 1
            if was_disabled:
                restored += 1

        if parent is not None:
            relation_change = _ensure_active_parent_history(
                session,
                active_history_by_child=active_history_by_child,
                node=node,
                parent=parent,
                collected_at=collected_at,
                collection_run_id=collection_run_id,
                restored=was_disabled,
                old_parent_id=old_parent_id,
            )
            moved += int(relation_change == "MOVED")

    observed_identities = set(candidate_nodes)
    normalized_removed = {
        (str(node_type).strip().upper(), str(node_code).strip())
        for node_type, node_code in removed_identities
        if str(node_type).strip() and str(node_code).strip()
    }
    children_by_parent_id: dict[int, list[HierarchyNode]] = defaultdict(list)
    for candidate in existing.values():
        if candidate.parent_id is not None:
            children_by_parent_id[candidate.parent_id].append(candidate)
    removal_identities = sorted(
        normalized_removed - observed_identities,
        key=lambda identity: (-NODE_LEVELS.get(identity[0], 0), identity[1]),
    )
    for identity in removal_identities:
        node = existing.get(identity)
        if node is None or not node.enabled:
            continue
        node.missing_count += 1
        missing_incremented += 1
        if node.missing_count < missing_disable_threshold:
            continue
        # A missing manager alone does not establish that its channels were
        # removed. Only the completed, error-free subtree retry may confirm
        # that the whole unobserved branch can be retired.
        if _has_enabled_descendant(
            node=node,
            children_by_parent_id=children_by_parent_id,
        ):
            if complete_structure_reconciliation:
                disabled += _disable_enabled_subtree(
                    root=node,
                    children_by_parent_id=children_by_parent_id,
                    active_history_by_child=active_history_by_child,
                    collected_at=collected_at,
                    missing_disable_threshold=missing_disable_threshold,
                )
                continue
            disable_deferred += 1
            continue
        node.enabled = False
        _close_active_parent_history(
            active_history_by_child, node.id, collected_at
        )
        disabled += 1

    session.flush()
    return {
        "created": created,
        "updated": updated,
        "moved": moved,
        "restored": restored,
        "disabled": disabled,
        "missing_incremented": missing_incremented,
        "disable_deferred": disable_deferred,
    }


def removed_identities_from_change_plan(
    change_plan: dict[str, Any] | None,
) -> set[tuple[str, str]]:
    """Translate the established drift plan to unified node identities."""
    result: set[tuple[str, str]] = set()
    plan = change_plan or {}
    for item in plan.get("removed_targets") or []:
        identity = (
            str(item.get("target_type") or "").strip().upper(),
            str(item.get("target_code") or "").strip(),
        )
        if all(identity):
            result.add(identity)
    for item in plan.get("removed_areas") or []:
        identity = (
            str(item.get("level_type") or "").strip().upper(),
            str(item.get("area_code") or "").strip(),
        )
        if all(identity):
            result.add(identity)
    for item in plan.get("removed_relations") or []:
        identity = (
            str(item.get("child_type") or "").strip().upper(),
            str(item.get("child_code") or "").strip(),
        )
        if all(identity):
            result.add(identity)
    return result


def _ensure_active_parent_history(
    session: Session,
    *,
    active_history_by_child: dict[int, HierarchyParentHistory],
    node: HierarchyNode,
    parent: HierarchyNode,
    collected_at: datetime,
    collection_run_id: int | None,
    restored: bool,
    old_parent_id: int | None,
) -> str | None:
    active = active_history_by_child.get(node.id)
    if active is not None and active.parent_node_id == parent.id:
        return None

    change_type = "RESTORED" if restored else "CREATED"
    if active is not None:
        active.valid_to = collected_at
        active_history_by_child.pop(node.id, None)
        # Release the generated-column unique key before inserting the new
        # active history for the same child. This flush only occurs on moves.
        session.flush([active])
        change_type = "MOVED"
    elif old_parent_id is not None and old_parent_id != parent.id and not restored:
        change_type = "MOVED"
    new_history = HierarchyParentHistory(
        child_node_id=node.id,
        parent_node_id=parent.id,
        valid_from=collected_at,
        collection_run_id=collection_run_id,
        change_type=change_type,
    )
    session.add(new_history)
    active_history_by_child[node.id] = new_history
    return change_type


def _close_active_parent_history(
    active_history_by_child: dict[int, HierarchyParentHistory],
    child_node_id: int,
    collected_at: datetime,
) -> None:
    active = active_history_by_child.pop(child_node_id, None)
    if active is not None:
        active.valid_to = collected_at


def _has_enabled_descendant(
    *,
    node: HierarchyNode,
    children_by_parent_id: dict[int, list[HierarchyNode]],
) -> bool:
    """Return whether a node still has an enabled descendant."""
    pending = list(children_by_parent_id.get(node.id, []))
    visited: set[int] = set()
    while pending:
        descendant = pending.pop()
        if descendant.id in visited:
            continue
        visited.add(descendant.id)
        if descendant.enabled:
            return True
        pending.extend(children_by_parent_id.get(descendant.id, []))
    return False


def _disable_enabled_subtree(
    *,
    root: HierarchyNode,
    children_by_parent_id: dict[int, list[HierarchyNode]],
    active_history_by_child: dict[int, HierarchyParentHistory],
    collected_at: datetime,
    missing_disable_threshold: int,
) -> int:
    """Retire every enabled node in a confirmed missing subtree."""
    pending = [root]
    visited: set[int] = set()
    disabled = 0
    while pending:
        node = pending.pop()
        if node.id in visited:
            continue
        visited.add(node.id)
        pending.extend(children_by_parent_id.get(node.id, []))
        if not node.enabled:
            continue
        node.enabled = False
        node.missing_count = max(node.missing_count, missing_disable_threshold)
        _close_active_parent_history(active_history_by_child, node.id, collected_at)
        disabled += 1
    return disabled
