"""Shared collection, structure recovery, and area validation orchestration."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from sqlalchemy import Engine

from infrastructure.dashboard_run_store import CollectionRunStore
from services.dashboard_area_validation import (
    execute_area_validation_phase,
    load_enabled_area_map,
)
from services.dashboard_collection_service import (
    CollectionTarget,
    collect_metric_rows,
    execute_collection_phase,
    load_collection_targets,
)
from services.dashboard_structure_sync import refresh_grid_structure
from services.dashboard_trigger import now_shanghai


class AreaCoverageError(RuntimeError):
    phase = "VALIDATE_AREA"
    error_type = "AREA_COVERAGE_MISMATCH"


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


def recover_missing_channels(
    rows: list[dict[str, Any]],
    area_map: dict[Any, Any],
    fetch_payload: Callable[[CollectionTarget], dict[str, Any]],
    indicator_codes: list[str],
    max_workers: int,
    hard_limit: int,
    max_fallback_requests: int,
) -> dict[str, Any]:
    collected = {
        (str(row.get("level_type") or ""), str(row.get("area_code") or ""))
        for row in rows
    }
    missing = [
        area
        for identity, area in area_map.items()
        if identity not in collected
    ]
    if not missing:
        return {"rows": rows, "fallback_request_count": 0}

    invalid_levels = sorted(
        {area.level_type for area in missing if area.level_type != "CHANNEL"}
    )
    if invalid_levels:
        raise AreaCoverageError(
            "缺失区域包含不可直接补采的层级: "
            + ", ".join(invalid_levels)
        )
    if len(missing) > max_fallback_requests:
        raise AreaCoverageError(
            f"缺失渠道数量 {len(missing)} 超过补采上限 "
            f"{max_fallback_requests}，拒绝退化为大规模渠道直查"
        )

    fallback_targets = [
        CollectionTarget(
            id=-area.id,
            target_code=area.area_code,
            target_name=area.area_name,
            target_type="CHANNEL",
            area_id=area.id,
            parent_target_id=None,
            sort_order=index,
        )
        for index, area in enumerate(missing, start=1)
    ]
    fallback = collect_metric_rows(
        fallback_targets,
        indicator_codes,
        fetch_payload,
        max_workers=min(max_workers, max(1, len(fallback_targets))),
        hard_limit=hard_limit,
    )
    return {
        "rows": rows + fallback["rows"],
        "fallback_request_count": fallback["request_count"],
    }


def affected_grid_targets(
    targets: list[CollectionTarget],
    collection_result: dict[str, Any],
    area_map: dict[Any, Any],
) -> list[CollectionTarget]:
    by_id = {target.id: target for target in targets}
    managers = {
        target.target_code: target
        for target in targets
        if target.target_type == "CHANNEL_MANAGER"
    }
    manager_codes = {
        error["target_code"]
        for error in collection_result.get("recoverable_errors") or []
    }
    managers_by_grid: dict[int, dict[str, str]] = {}
    for manager in managers.values():
        if manager.parent_target_id is None:
            continue
        managers_by_grid.setdefault(manager.parent_target_id, {})[
            manager.target_code
        ] = manager.target_name

    observed_managers_by_grid: dict[str, dict[str, str]] = {}
    observed_grid_codes = set()
    for observation in collection_result.get("structure_observations") or []:
        if (
            observation.get("parent_type") != "GRID"
            or observation.get("child_type") != "CHANNEL_MANAGER"
        ):
            continue
        grid_code = str(observation.get("parent_code") or "")
        observed_grid_codes.add(grid_code)
        observed_managers_by_grid.setdefault(grid_code, {})[
            str(observation.get("child_code") or "")
        ] = str(observation.get("child_name") or "")

    grids = {}
    for target in targets:
        if (
            target.target_type == "GRID"
            and target.target_code in observed_grid_codes
            and managers_by_grid.get(target.id, {})
            != observed_managers_by_grid.get(target.target_code, {})
        ):
            grids[target.id] = target

    for row in collection_result["rows"]:
        identity = (
            str(row.get("level_type") or ""),
            str(row.get("area_code") or ""),
        )
        if identity not in area_map and identity[0] == "CHANNEL":
            manager_code = str(row.get("parent_request_code") or "")
            if manager_code:
                manager_codes.add(manager_code)

    for manager_code in manager_codes:
        manager = managers.get(manager_code)
        if manager is None or manager.parent_target_id is None:
            continue
        grid = by_id.get(manager.parent_target_id)
        if grid and grid.target_type == "GRID":
            grids[grid.id] = grid
    return list(grids.values())


def collect_validate_metric_rows(
    *,
    engine: Engine,
    run_store: CollectionRunStore,
    batch_no: str,
    targets: list[CollectionTarget],
    indicator_codes: list[str],
    fetch_metrics: Callable[[CollectionTarget], dict[str, Any]],
    fetch_structure: Callable[[CollectionTarget], dict[str, Any]],
    max_workers: int,
    hard_limit: int,
    max_fallback_requests: int,
    anomaly_directory: Path,
) -> dict[str, Any]:
    collection_result = execute_collection_phase(
        batch_no,
        targets,
        indicator_codes,
        fetch_metrics,
        run_store,
        max_workers=max_workers,
        hard_limit=hard_limit,
        now_provider=naive_shanghai_now,
        allow_recoverable_manager_failures=True,
    )

    area_map = load_enabled_area_map(engine)
    sync_grids = affected_grid_targets(
        targets,
        collection_result,
        area_map,
    )
    structure_sync = []
    if sync_grids:
        run_store.update(
            batch_no,
            status="RUNNING",
            phase="SYNC_STRUCTURE",
        )
        for grid in sync_grids:
            structure_sync.append(
                refresh_grid_structure(
                    engine,
                    grid,
                    fetch_structure,
                    naive_shanghai_now(),
                )
            )
        targets = load_collection_targets(engine)
        collection_result = execute_collection_phase(
            batch_no,
            targets,
            indicator_codes,
            fetch_metrics,
            run_store,
            max_workers=max_workers,
            hard_limit=hard_limit,
            now_provider=naive_shanghai_now,
            allow_recoverable_manager_failures=False,
        )
        area_map = load_enabled_area_map(engine)

    recovery_result = recover_missing_channels(
        collection_result["rows"],
        area_map,
        fetch_metrics,
        indicator_codes,
        max_workers=max_workers,
        hard_limit=hard_limit,
        max_fallback_requests=max_fallback_requests,
    )
    collection_result["rows"] = recovery_result["rows"]
    collection_result["fallback_request_count"] = recovery_result[
        "fallback_request_count"
    ]
    collection_result["request_count"] += recovery_result[
        "fallback_request_count"
    ]
    collection_result["row_count"] = len(collection_result["rows"])
    run_store.update(
        batch_no,
        status="RUNNING",
        phase="COLLECTED",
        request_count=collection_result["request_count"],
        row_count=collection_result["row_count"],
    )

    validation_result = execute_area_validation_phase(
        batch_no,
        collection_result["rows"],
        engine,
        run_store,
        anomaly_directory,
        now_provider=naive_shanghai_now,
    )
    ensure_complete_area_coverage(validation_result, len(area_map))
    return {
        "collection": collection_result,
        "validation": validation_result,
        "structure_sync": structure_sync,
    }
