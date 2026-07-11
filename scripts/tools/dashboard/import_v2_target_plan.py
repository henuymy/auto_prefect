"""Import one immutable-version Dashboard V2 target plan from JSON."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import select
from sqlalchemy.orm import Session

from infrastructure.dashboard_mysql import DashboardMySQLSettings, create_dashboard_engine
from models.dashboard_v2 import HierarchyNode, IndicatorV2, TargetPlan
from services.dashboard_v2_target_service import (
    activate_v2_target_plan_in_session,
    replace_draft_target_values_in_session,
)


def _load_payload(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("values"), list):
        raise ValueError("目标方案 JSON 必须是对象，且 values 必须是数组")
    return payload


def import_target_plan(
    path: str | Path,
    *,
    activate: bool = False,
    expected_database: str | None = None,
) -> dict[str, Any]:
    payload = _load_payload(path)
    scenario = str(payload.get("scenario") or "").strip().upper()
    period_type = str(payload.get("period_type") or "").strip().upper()
    if scenario not in {"NORMAL", "PK"}:
        raise ValueError("scenario 只支持 NORMAL/PK")
    if period_type not in {"DAY", "MONTH"}:
        raise ValueError("period_type 只支持 DAY/MONTH")
    effective_from = date.fromisoformat(str(payload.get("effective_from")))
    effective_to = (
        date.fromisoformat(str(payload["effective_to"]))
        if payload.get("effective_to") else None
    )
    settings = DashboardMySQLSettings.from_env() if expected_database else None
    if settings and settings.database != expected_database:
        raise RuntimeError(
            f"拒绝写入非目标库: configured={settings.database!r}, "
            f"expected={expected_database!r}"
        )
    engine = create_dashboard_engine(settings) if settings else create_dashboard_engine()
    try:
        with Session(engine) as session, session.begin():
            rows = payload["values"]
            node_keys = {
                (
                    str(row.get("node_type") or "").strip().upper(),
                    str(row.get("node_code") or "").strip(),
                )
                for row in rows
            }
            indicator_codes = {
                str(row.get("indicator_code") or "").strip() for row in rows
            }
            nodes = list(session.scalars(select(HierarchyNode).where(
                HierarchyNode.node_type.in_({key[0] for key in node_keys}),
                HierarchyNode.node_code.in_({key[1] for key in node_keys}),
            )))
            node_by_key = {(row.node_type, row.node_code): row.id for row in nodes}
            indicators = list(session.scalars(select(IndicatorV2).where(
                IndicatorV2.code.in_(indicator_codes)
            )))
            indicator_by_code = {row.code: row.id for row in indicators}
            missing_nodes = sorted(node_keys - node_by_key.keys())
            missing_indicators = sorted(indicator_codes - indicator_by_code.keys())
            if missing_nodes:
                raise ValueError(f"目标值节点不存在: {missing_nodes[:10]}")
            if missing_indicators:
                raise ValueError(f"目标值指标不存在: {missing_indicators[:10]}")
            existing_version = session.scalar(
                select(TargetPlan.version_no)
                .where(
                    TargetPlan.scenario == scenario,
                    TargetPlan.period_type == period_type,
                    TargetPlan.plan_name == str(payload["plan_name"]).strip(),
                )
                .order_by(TargetPlan.version_no.desc())
                .limit(1)
            )
            plan = TargetPlan(
                plan_name=str(payload["plan_name"]).strip(),
                scenario=scenario,
                period_type=period_type,
                effective_from=effective_from,
                effective_to=effective_to,
                priority=int(payload.get("priority", 0) or 0),
                version_no=int(existing_version or 0) + 1,
                status="DRAFT",
                supersedes_plan_id=payload.get("supersedes_plan_id"),
            )
            session.add(plan)
            session.flush()
            value_count = replace_draft_target_values_in_session(
                session,
                plan_id=plan.id,
                values=[
                    {
                        "node_id": node_by_key[(
                            str(row["node_type"]).strip().upper(),
                            str(row["node_code"]).strip(),
                        )],
                        "indicator_id": indicator_by_code[
                            str(row["indicator_code"]).strip()
                        ],
                        "target_value": row["target_value"],
                    }
                    for row in rows
                ],
            )
            if activate:
                activate_v2_target_plan_in_session(
                    session,
                    plan_id=plan.id,
                    activated_at=datetime.now(),
                )
            return {
                "plan_id": plan.id,
                "scenario": scenario,
                "period_type": period_type,
                "version_no": plan.version_no,
                "status": plan.status,
                "value_count": value_count,
            }
    finally:
        engine.dispose()


def activate_target_plan(
    plan_id: int,
    *,
    expected_database: str | None = None,
) -> dict[str, Any]:
    settings = DashboardMySQLSettings.from_env() if expected_database else None
    if settings and settings.database != expected_database:
        raise RuntimeError(
            f"拒绝写入非目标库: configured={settings.database!r}, "
            f"expected={expected_database!r}"
        )
    engine = create_dashboard_engine(settings) if settings else create_dashboard_engine()
    try:
        with Session(engine) as session, session.begin():
            plan = activate_v2_target_plan_in_session(
                session,
                plan_id=plan_id,
                activated_at=datetime.now(),
            )
            return {
                "plan_id": plan.id,
                "scenario": plan.scenario,
                "period_type": plan.period_type,
                "version_no": plan.version_no,
                "status": plan.status,
            }
    finally:
        engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description="导入驾驶舱 V2 目标方案")
    parser.add_argument("json_file", nargs="?")
    parser.add_argument(
        "--activate",
        action="store_true",
        help="导入校验后立即激活；默认仅创建 DRAFT",
    )
    parser.add_argument(
        "--activate-plan-id",
        type=int,
        help="激活数据库中已核对的 DRAFT，不重复导入 JSON",
    )
    parser.add_argument("--expected-database", default="dashboard_v2")
    args = parser.parse_args()
    if args.activate_plan_id is not None:
        if args.json_file or args.activate:
            parser.error("--activate-plan-id 不能与 json_file/--activate 同时使用")
        result = activate_target_plan(
            args.activate_plan_id,
            expected_database=args.expected_database,
        )
    else:
        if not args.json_file:
            parser.error("导入目标方案时必须提供 json_file")
        result = import_target_plan(
            args.json_file,
            activate=args.activate,
            expected_database=args.expected_database,
        )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
