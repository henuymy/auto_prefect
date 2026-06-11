"""Partial dashboard structure refresh scoped to one grid."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Callable

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session, sessionmaker

from models.dashboard_area import Area
from models.dashboard_request_target import RequestTarget
from services.dashboard_collection_service import (
    CollectionTarget,
    validate_payload,
)
from services.json_excel_service import extract_rows


def refresh_grid_structure(
    engine: Engine,
    grid_target: CollectionTarget,
    fetch_payload: Callable[[CollectionTarget], dict[str, Any]],
    collected_at: datetime,
) -> dict[str, Any]:
    if grid_target.target_type != "GRID":
        raise ValueError(
            f"局部结构同步只支持 GRID: {grid_target.target_type}/"
            f"{grid_target.target_code}"
        )

    grid_payload = fetch_payload(grid_target)
    validate_payload(grid_payload, grid_target)
    manager_rows = _child_rows(grid_payload, grid_target.target_code)
    managers = [
        CollectionTarget(
            id=-(index + 1),
            target_code=row["area_code"],
            target_name=row["area_name"],
            target_type="CHANNEL_MANAGER",
            area_id=None,
            parent_target_id=grid_target.id,
            sort_order=(index + 1) * 10,
        )
        for index, row in enumerate(manager_rows)
    ]

    channels: dict[str, dict[str, str]] = {}
    manager_errors = []
    for manager in managers:
        try:
            payload = fetch_payload(manager)
            validate_payload(payload, manager)
            for row in _child_rows(payload, manager.target_code):
                channels[row["area_code"]] = {
                    **row,
                    "manager_code": manager.target_code,
                }
        except Exception as exc:
            manager_errors.append(
                {
                    "manager_code": manager.target_code,
                    "manager_name": manager.target_name,
                    "error": f"{type(exc).__name__}: {str(exc)[:500]}",
                }
            )

    area_result = _update_grid_channels(
        engine,
        grid_target,
        list(channels.values()),
        collected_at,
        complete=not manager_errors,
    )
    target_result = _update_grid_managers(
        engine,
        grid_target,
        managers,
        collected_at,
    )
    return {
        "grid_code": grid_target.target_code,
        "grid_name": grid_target.target_name,
        "manager_count": len(managers),
        "channel_count": len(channels),
        "manager_errors": manager_errors,
        "area": area_result,
        "request_target": target_result,
    }


def _child_rows(
    payload: dict[str, Any],
    self_code: str,
) -> list[dict[str, str]]:
    result = []
    seen = set()
    for row in extract_rows(payload, "result.tableData"):
        area_code = str(row.get("areaCode") or "").strip()
        area_name = str(row.get("areaName") or "").strip()
        if (
            not area_code
            or not area_name
            or area_code == self_code
            or area_code in seen
        ):
            continue
        seen.add(area_code)
        result.append({"area_code": area_code, "area_name": area_name})
    return result


def _update_grid_channels(
    engine: Engine,
    grid_target: CollectionTarget,
    channels: list[dict[str, str]],
    collected_at: datetime,
    complete: bool,
) -> dict[str, int]:
    session_factory = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    created = 0
    updated = 0
    disabled = 0
    with session_factory.begin() as session:
        grid = session.scalar(
            select(Area).where(
                Area.level_type == "GRID",
                Area.area_code == grid_target.target_code,
            )
        )
        if grid is None:
            raise RuntimeError(
                f"局部同步找不到网格 area: {grid_target.target_code}"
            )
        existing_under_grid = {
            area.area_code: area
            for area in session.scalars(
                select(Area).where(
                    Area.level_type == "CHANNEL",
                    Area.parent_id == grid.id,
                )
            ).all()
        }
        observed_code_list = [row["area_code"] for row in channels]
        observed_existing = {
            area.area_code: area
            for area in session.scalars(
                select(Area).where(
                    Area.level_type == "CHANNEL",
                    Area.area_code.in_(observed_code_list),
                )
            ).all()
        } if observed_code_list else {}
        observed_codes = set()
        for row in channels:
            observed_codes.add(row["area_code"])
            area = observed_existing.get(row["area_code"])
            if area is None:
                session.add(
                    Area(
                        area_code=row["area_code"],
                        area_name=row["area_name"],
                        level_type="CHANNEL",
                        level_no=4,
                        parent_id=grid.id,
                        enabled=True,
                        missing_count=0,
                        last_seen_at=collected_at,
                    )
                )
                created += 1
            else:
                area.area_name = row["area_name"]
                area.parent_id = grid.id
                area.enabled = True
                area.missing_count = 0
                area.last_seen_at = collected_at
                area.updated_at = collected_at
                updated += 1

        if complete:
            for code, area in existing_under_grid.items():
                if code in observed_codes or not area.enabled:
                    continue
                area.enabled = False
                area.missing_count += 1
                area.updated_at = collected_at
                disabled += 1
    return {"created": created, "updated": updated, "disabled": disabled}


def _update_grid_managers(
    engine: Engine,
    grid_target: CollectionTarget,
    managers: list[CollectionTarget],
    collected_at: datetime,
) -> dict[str, int]:
    session_factory = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    created = 0
    updated = 0
    disabled = 0
    with session_factory.begin() as session:
        grid = session.scalar(
            select(RequestTarget).where(
                RequestTarget.target_type == "GRID",
                RequestTarget.target_code == grid_target.target_code,
            )
        )
        if grid is None:
            raise RuntimeError(
                f"局部同步找不到网格 request_target: {grid_target.target_code}"
            )
        existing_under_grid = {
            target.target_code: target
            for target in session.scalars(
                select(RequestTarget).where(
                    RequestTarget.target_type == "CHANNEL_MANAGER",
                    RequestTarget.parent_target_id == grid.id,
                )
            ).all()
        }
        observed_code_list = [manager.target_code for manager in managers]
        observed_existing = {
            target.target_code: target
            for target in session.scalars(
                select(RequestTarget).where(
                    RequestTarget.target_type == "CHANNEL_MANAGER",
                    RequestTarget.target_code.in_(observed_code_list),
                )
            ).all()
        } if observed_code_list else {}
        observed_codes = set()
        for manager in managers:
            observed_codes.add(manager.target_code)
            target = observed_existing.get(manager.target_code)
            if target is None:
                session.add(
                    RequestTarget(
                        target_code=manager.target_code,
                        target_name=manager.target_name,
                        target_type="CHANNEL_MANAGER",
                        area_id=None,
                        parent_target_id=grid.id,
                        enabled=True,
                        sort_order=manager.sort_order,
                    )
                )
                created += 1
            else:
                target.target_name = manager.target_name
                target.parent_target_id = grid.id
                target.enabled = True
                target.sort_order = manager.sort_order
                target.updated_at = collected_at
                updated += 1

        for code, target in existing_under_grid.items():
            if code in observed_codes or not target.enabled:
                continue
            target.enabled = False
            target.updated_at = collected_at
            disabled += 1
    return {"created": created, "updated": updated, "disabled": disabled}
