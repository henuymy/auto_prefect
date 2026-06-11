"""Validate collected metric rows against the enabled dashboard areas."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from infrastructure.dashboard_run_store import CollectionRunStore
from models.dashboard_area import Area


FORMAL_AREA_LEVELS = {"CITY", "BRANCH", "GRID", "CHANNEL"}
IGNORED_PATH_LEVELS = {"CHANNEL_MANAGER"}


@dataclass(frozen=True)
class EnabledArea:
    id: int
    level_type: str
    area_code: str
    area_name: str


class AreaStructureMismatchError(RuntimeError):
    error_type = "AREA_NOT_FOUND"
    phase = "VALIDATE_AREA"

    def __init__(self, result: dict[str, Any]):
        self.result = result
        unmatched = result["unmatched_areas"]
        preview = "；".join(
            f"{item['level_type']}/{item['area_code']}/{item['area_name']}"
            for item in unmatched[:10]
        )
        suffix = f" 等{len(unmatched)}个区域" if len(unmatched) > 10 else ""
        super().__init__(
            f"采集数据存在有名称但未匹配 area_id 的区域: {preview}{suffix}。"
            "当前批次不得写入指标表；请先同步区域结构，再创建新批次重采。"
        )


def load_enabled_area_map(engine: Engine) -> dict[tuple[str, str], EnabledArea]:
    """Load enabled areas with one database query per collection batch."""
    with Session(engine) as session:
        areas = session.scalars(
            select(Area).where(Area.enabled.is_(True))
        ).all()
    return {
        (area.level_type, area.area_code): EnabledArea(
            id=area.id,
            level_type=area.level_type,
            area_code=area.area_code,
            area_name=area.area_name,
        )
        for area in areas
    }


def validate_metric_rows(
    rows: Iterable[dict[str, Any]],
    area_map: dict[tuple[str, str], EnabledArea],
) -> dict[str, Any]:
    matched_rows = []
    skipped_rows = []
    unmatched_by_identity: dict[tuple[str, str], dict[str, Any]] = {}
    name_mismatches_by_identity: dict[tuple[str, str], dict[str, Any]] = {}

    for source_row in rows:
        row = dict(source_row)
        level_type = str(
            row.get("level_type") or row.get("node_type") or ""
        ).strip().upper()
        area_code = str(row.get("area_code") or row.get("areaCode") or "").strip()
        area_name = str(row.get("area_name") or row.get("areaName") or "").strip()
        parent_request_code = str(
            row.get("parent_request_code")
            or row.get("query_area_id")
            or row.get("__request_area_id")
            or ""
        ).strip()

        if level_type in IGNORED_PATH_LEVELS:
            continue
        if level_type not in FORMAL_AREA_LEVELS:
            skipped_rows.append(
                _anomaly(
                    "UNSUPPORTED_LEVEL",
                    level_type,
                    area_code,
                    area_name,
                    parent_request_code,
                )
            )
            continue
        if not area_name:
            skipped_rows.append(
                _anomaly(
                    "MISSING_AREA_NAME",
                    level_type,
                    area_code,
                    area_name,
                    parent_request_code,
                )
            )
            continue
        if not area_code:
            skipped_rows.append(
                _anomaly(
                    "MISSING_AREA_CODE",
                    level_type,
                    area_code,
                    area_name,
                    parent_request_code,
                )
            )
            continue

        identity = (level_type, area_code)
        area = area_map.get(identity)
        if area is None:
            unmatched_by_identity.setdefault(
                identity,
                _anomaly(
                    "AREA_NOT_FOUND",
                    level_type,
                    area_code,
                    area_name,
                    parent_request_code,
                ),
            )
            continue

        if area.area_name != area_name:
            name_mismatches_by_identity.setdefault(
                identity,
                {
                    **_anomaly(
                        "AREA_NAME_MISMATCH",
                        level_type,
                        area_code,
                        area_name,
                        parent_request_code,
                    ),
                    "database_area_name": area.area_name,
                },
            )

        row["area_id"] = area.id
        row["level_type"] = level_type
        row["area_code"] = area_code
        row["area_name"] = area_name
        matched_rows.append(row)

    result = {
        "matched_rows": matched_rows,
        "matched_row_count": len(matched_rows),
        "matched_area_count": len(
            {(row["level_type"], row["area_code"]) for row in matched_rows}
        ),
        "skipped_rows": skipped_rows,
        "skipped_row_count": len(skipped_rows),
        "unmatched_areas": list(unmatched_by_identity.values()),
        "unmatched_area_count": len(unmatched_by_identity),
        "name_mismatches": list(name_mismatches_by_identity.values()),
        "name_mismatch_count": len(name_mismatches_by_identity),
        "sync_required": bool(unmatched_by_identity),
        "next_action": (
            "运行区域结构同步并审核 area 变化，然后创建新批次重采"
            if unmatched_by_identity
            else None
        ),
        "sync_commands": (
            [
                "python scripts/dashboard/export_areas.py",
                "python scripts/dashboard/import_areas.py",
            ]
            if unmatched_by_identity
            else []
        ),
    }
    if unmatched_by_identity:
        raise AreaStructureMismatchError(result)
    return result


def execute_area_validation_phase(
    batch_no: str,
    rows: Iterable[dict[str, Any]],
    engine: Engine,
    run_store: CollectionRunStore,
    anomaly_directory: str | Path,
    now_provider=datetime.now,
) -> dict[str, Any]:
    run_store.update(
        batch_no,
        status="RUNNING",
        phase="VALIDATE_AREA",
    )
    area_map = load_enabled_area_map(engine)
    try:
        result = validate_metric_rows(rows, area_map)
    except AreaStructureMismatchError as exc:
        report_path = write_area_anomaly_report(
            anomaly_directory,
            batch_no,
            exc.result,
            now_provider=now_provider,
        )
        error_summary = {
            "message": str(exc),
            "unmatched_area_count": exc.result["unmatched_area_count"],
            "skipped_row_count": exc.result["skipped_row_count"],
            "report_path": str(report_path),
            "next_action": exc.result["next_action"],
            "sync_commands": exc.result["sync_commands"],
        }
        run_store.update(
            batch_no,
            status="FAILED",
            phase=exc.phase,
            finished_at=now_provider(),
            area_count=exc.result["matched_area_count"],
            row_count=exc.result["matched_row_count"],
            error_type=exc.error_type,
            error_message=json.dumps(error_summary, ensure_ascii=False),
        )
        raise

    report_path = None
    if result["skipped_rows"] or result["name_mismatches"]:
        report_path = write_area_anomaly_report(
            anomaly_directory,
            batch_no,
            result,
            now_provider=now_provider,
        )
    run_store.update(
        batch_no,
        status="RUNNING",
        phase="AREA_VALIDATED",
        area_count=result["matched_area_count"],
        row_count=result["matched_row_count"],
        error_type=None,
        error_message=None,
    )
    return {
        **result,
        "anomaly_report_path": str(report_path) if report_path else None,
    }


def write_area_anomaly_report(
    directory: str | Path,
    batch_no: str,
    result: dict[str, Any],
    now_provider=datetime.now,
) -> Path:
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{batch_no}.json"
    payload = {
        "batch_no": batch_no,
        "generated_at": now_provider().isoformat(timespec="milliseconds"),
        "summary": {
            key: result.get(key)
            for key in (
                "matched_row_count",
                "matched_area_count",
                "skipped_row_count",
                "unmatched_area_count",
                "name_mismatch_count",
                "sync_required",
                "next_action",
                "sync_commands",
            )
        },
        "skipped_rows": result.get("skipped_rows") or [],
        "unmatched_areas": result.get("unmatched_areas") or [],
        "name_mismatches": result.get("name_mismatches") or [],
    }
    temporary_path = path.with_suffix(".json.tmp")
    temporary_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary_path.replace(path)
    return path


def _anomaly(
    anomaly_type: str,
    level_type: str,
    area_code: str,
    area_name: str,
    parent_request_code: str,
) -> dict[str, str]:
    return {
        "type": anomaly_type,
        "level_type": level_type,
        "area_code": area_code,
        "area_name": area_name,
        "parent_request_code": parent_request_code,
    }
