from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from models.dashboard_v2 import TargetPlan
from scripts.tools.dashboard import import_v2_target_plan
from tests.test_dashboard_v2_query_service import _engine


def test_target_import_creates_draft_then_activates_existing_plan(
    monkeypatch,
    tmp_path,
):
    engine = _engine()
    monkeypatch.setattr(engine, "dispose", lambda: None)
    monkeypatch.setattr(
        import_v2_target_plan,
        "create_dashboard_engine",
        lambda: engine,
    )
    payload_path = tmp_path / "target.json"
    payload_path.write_text(
        json.dumps(
            {
                "plan_name": "正常时期日目标",
                "scenario": "NORMAL",
                "period_type": "DAY",
                "effective_from": "2026-07-01",
                "priority": 10,
                "values": [
                    {
                        "node_type": "CHANNEL_MANAGER",
                        "node_code": "M001&AQ701",
                        "indicator_code": "channel_count",
                        "target_value": 100,
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    imported = import_v2_target_plan.import_target_plan(payload_path)
    activated = import_v2_target_plan.activate_target_plan(imported["plan_id"])

    assert imported["status"] == "DRAFT"
    assert activated["status"] == "ACTIVE"
    with Session(engine) as session:
        plans = list(session.scalars(select(TargetPlan).order_by(TargetPlan.id)))
    assert len(plans) == 2  # fixture plan + newly imported immutable version
    assert plans[-1].status == "ACTIVE"
