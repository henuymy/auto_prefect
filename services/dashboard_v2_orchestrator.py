"""V2 collection orchestration with trusted two-pass structure drift handling."""

from __future__ import annotations

import logging
from pathlib import Path
from time import perf_counter
from typing import Any, Callable, Iterable

from sqlalchemy import Engine
from sqlalchemy.orm import Session

from infrastructure.dashboard_run_store import CollectionRunStore
from services.dashboard_collection_orchestrator import (
    StructureDiff,
    StructureGraph,
    _affected_grid_codes_from_diff,
    _build_candidate_targets_from_observations,
    _build_subtree_retry_targets,
    _manager_codes_for_grids,
    _merge_subtree_retry_collection,
    diff_structure_graph,
    naive_shanghai_now,
    normalize_collection_retry_strategy,
    summarize_structure_changes,
)
from services.dashboard_collection_service import (
    CollectionTarget,
    execute_collection_phase,
)
from services.dashboard_metric_store import parse_metric_value
from services.dashboard_v2_hierarchy import (
    BASE_NODE_TYPES,
    V2HierarchyError,
    build_v2_observed_graph,
    load_v2_structure_graph,
    validate_v2_structure_graph,
)


class V2MetricCoverageError(RuntimeError):
    phase = "VALIDATE_AREA"
    error_type = "NODE_COVERAGE_MISMATCH"


def collect_validate_metric_rows_v2(
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
    """Collect without writes, retry affected subtrees, then return one candidate."""
    del anomaly_directory, failure_directory
    logger = event_logger or logging.getLogger(__name__)
    strategy = normalize_collection_retry_strategy(retry_strategy)
    with Session(engine) as session:
        current_graph = load_v2_structure_graph(session)
    if not current_graph.targets:
        raise V2HierarchyError("V2 当前树没有可请求节点，请先执行空库初始化")

    timings: list[dict[str, Any]] = []
    first_collection: dict[str, Any] | None = None
    affected_grid_codes: set[str] = set()
    affected_manager_codes: set[str] = set()
    final_collection: dict[str, Any] | None = None
    final_observed: StructureGraph | None = None
    final_diff: StructureDiff | None = None

    active_targets = list(targets)
    for attempt_index in range(2):
        attempt_no = attempt_index + 1
        run_store.update(batch_no, status="RUNNING", phase="COLLECT")
        started = perf_counter()
        collection = execute_collection_phase(
            batch_no,
            active_targets,
            indicator_codes,
            fetch_metrics,
            run_store,
            max_workers=max_workers,
            hard_limit=hard_limit,
            now_provider=naive_shanghai_now,
            allow_recoverable_manager_failures=(attempt_index == 0),
            include_manager_self_rows=True,
        )
        collect_seconds = perf_counter() - started
        if first_collection is not None:
            collection = _merge_subtree_retry_collection(
                first_collection,
                collection,
                affected_grid_codes,
                affected_manager_codes,
            )
        else:
            first_collection = collection

        observed = augment_v2_observed_with_base(
            current_graph,
            collection.get("rows", []),
            collection.get("structure_observations", []),
        )
        graph_diff = diff_structure_graph(
            current_graph,
            observed,
            recoverable_errors=bool(collection.get("recoverable_errors")),
            compare_manager_channel_relations=True,
        )
        metadata_changed = v2_graph_metadata_changed(current_graph, observed)
        structure_changed = graph_diff.changed or metadata_changed
        timings.append(
            {
                "attempt": attempt_no,
                "collect_seconds": round(collect_seconds, 3),
                "request_count": collection.get("request_count", 0),
                "row_count": collection.get("row_count", 0),
                "structure_drift_detected": structure_changed,
                "retry_strategy": strategy,
            }
        )

        if not structure_changed and not collection.get("recoverable_errors"):
            validated = validate_v2_metric_coverage(
                collection.get("rows", []),
                current_graph,
                indicator_codes,
            )
            run_store.update(
                batch_no,
                status="RUNNING",
                phase="AREA_VALIDATED",
                node_count=len(validated),
                row_count=len(validated),
            )
            return _orchestration_result(
                collection=collection,
                validated_rows=validated,
                candidate_graph=current_graph,
                graph_diff=graph_diff,
                timings=timings,
                structure_changed=False,
                strategy=strategy,
                affected_grid_codes=set(),
            )

        final_collection = collection
        final_observed = observed
        final_diff = graph_diff
        if attempt_index == 1:
            break

        affected_grid_codes = _affected_grid_codes_from_diff(
            graph_diff,
            current_graph,
            observed,
        )
        affected_manager_codes = _manager_codes_for_grids(
            current_graph, affected_grid_codes
        ) | _manager_codes_for_grids(observed, affected_grid_codes)
        run_store.update(batch_no, status="RUNNING", phase="PREPARE_CANDIDATE")
        if strategy == "affected_grid" and affected_grid_codes:
            retry_targets = _build_subtree_retry_targets(
                targets,
                collection.get("rows", []),
                collection.get("structure_observations", []),
                affected_grid_codes,
            )
        else:
            retry_targets = []
        if not retry_targets:
            retry_targets = _build_candidate_targets_from_observations(
                collection.get("rows", []),
                collection.get("structure_observations", []),
            )
            first_collection = None
            affected_grid_codes = set()
            affected_manager_codes = set()
        active_targets = retry_targets
        logger.warning(
            "V2 结构漂移进入第二轮 batch_no=%s grids=%s targets=%s",
            batch_no,
            sorted(affected_grid_codes),
            len(active_targets),
        )

    if final_collection is None or final_observed is None or final_diff is None:
        raise AssertionError("V2 结构漂移编排结果不完整")
    if final_collection.get("recoverable_errors"):
        raise V2MetricCoverageError("V2 二轮采集仍存在可恢复错误")
    validate_v2_structure_graph(final_observed)
    validated = validate_v2_metric_coverage(
        final_collection.get("rows", []),
        final_observed,
        indicator_codes,
    )
    run_store.update(
        batch_no,
        status="RUNNING",
        phase="AREA_VALIDATED",
        node_count=len(validated),
        row_count=len(validated),
    )
    return _orchestration_result(
        collection=final_collection,
        validated_rows=validated,
        candidate_graph=final_observed,
        graph_diff=final_diff,
        timings=timings,
        structure_changed=True,
        strategy=strategy,
        affected_grid_codes=affected_grid_codes,
    )


def augment_v2_observed_with_base(
    current_graph: StructureGraph,
    rows: Iterable[dict[str, Any]],
    structure_observations: Iterable[dict[str, Any]],
) -> StructureGraph:
    """Keep approved base nodes/edges while replacing observed lower subtrees."""
    observed = build_v2_observed_graph(rows, structure_observations)
    areas = dict(observed.areas)
    targets = dict(observed.targets)
    edges = set(observed.edges)
    for identity, node in current_graph.areas.items():
        if identity[0] in BASE_NODE_TYPES:
            areas.setdefault(identity, node)
    for identity, node in current_graph.targets.items():
        if identity[0] in BASE_NODE_TYPES:
            targets.setdefault(identity, node)
    for edge in current_graph.edges:
        if edge.child_type in {"BRANCH", "GRID"}:
            edges.add(edge)
    return StructureGraph(areas=areas, targets=targets, edges=edges)


def validate_v2_metric_coverage(
    rows: Iterable[dict[str, Any]],
    graph: StructureGraph,
    indicator_codes: Iterable[str],
) -> list[dict[str, Any]]:
    """Require exactly one row for every metric node in the candidate graph."""
    by_identity: dict[tuple[str, str], dict[str, Any]] = {}
    for source in rows:
        row = dict(source)
        identity = (
            str(row.get("level_type") or "").strip().upper(),
            str(row.get("area_code") or "").strip(),
        )
        if not all(identity):
            raise V2MetricCoverageError("V2 指标行缺少 node_type/node_code")
        existing = by_identity.get(identity)
        if existing is not None:
            raise V2MetricCoverageError(
                f"V2 指标节点出现重复行: {identity[0]}/{identity[1]}"
            )
        by_identity[identity] = row

    expected = set(graph.areas)
    actual = set(by_identity)
    if actual != expected:
        missing = sorted(expected - actual)[:10]
        unknown = sorted(actual - expected)[:10]
        raise V2MetricCoverageError(
            f"V2 指标节点覆盖不完: expected={len(expected)}, "
            f"actual={len(actual)}, missing={missing}, unknown={unknown}"
        )

    codes = list(indicator_codes)
    result: list[dict[str, Any]] = []
    for identity in sorted(actual):
        row = by_identity[identity]
        for code in codes:
            parse_metric_value(row.get(code))
        node = graph.areas[identity]
        if node.node_id is not None:
            row["node_id"] = node.node_id
        row["node_type"] = identity[0]
        row["node_code"] = identity[1]
        result.append(row)
    return result


def v2_graph_metadata_changed(
    current: StructureGraph,
    observed: StructureGraph,
) -> bool:
    """Detect name changes not represented by the legacy relation diff."""
    current_nodes = {**current.areas, **current.targets}
    observed_nodes = {**observed.areas, **observed.targets}
    for identity in set(current_nodes) & set(observed_nodes):
        if current_nodes[identity].name != observed_nodes[identity].name:
            return True
    return False


def _orchestration_result(
    *,
    collection: dict[str, Any],
    validated_rows: list[dict[str, Any]],
    candidate_graph: StructureGraph,
    graph_diff: StructureDiff,
    timings: list[dict[str, Any]],
    structure_changed: bool,
    strategy: str,
    affected_grid_codes: set[str],
) -> dict[str, Any]:
    summary = summarize_structure_changes(
        graph_diff,
        retry_strategy=strategy,
        affected_grid_codes=affected_grid_codes,
    )
    return {
        "collection": collection,
        "validated_rows": validated_rows,
        "validation": {
            "matched_node_count": len(validated_rows),
            "matched_row_count": len(validated_rows),
        },
        "candidate_graph": candidate_graph,
        "structure_observations": collection.get("structure_observations", []),
        "attempt_timings": timings,
        "attempts": len(timings),
        "structure_changed": structure_changed,
        "change_plan": {
            "removed_targets": graph_diff.removed_targets,
            "removed_areas": graph_diff.removed_areas,
            "removed_relations": graph_diff.removed_relations,
        },
        "structure_change_summary": summary,
    }
