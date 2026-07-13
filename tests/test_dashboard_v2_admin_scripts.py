from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from sqlalchemy import select
from sqlalchemy.orm import Session

from models.dashboard_v2 import TargetPlan
from scripts.tools.dashboard import import_v2_target_plan, v2_cutover_audit
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


def test_cutover_audit_reads_workers_only_from_dashboard_pool(monkeypatch):
    monkeypatch.setenv("PREFECT_DASHBOARD_POOL_NAME", "dashboard-test-pool")
    deployments = [
        SimpleNamespace(name=name, id=f"deployment-{index}", paused=True)
        for index, name in enumerate(sorted(v2_cutover_audit.REQUIRED_DEPLOYMENTS))
    ]

    class FakeClient:
        def __init__(self):
            self.worker_pool_reads = []

        async def read_deployments(self):
            return deployments

        async def read_workers_for_work_pool(self, pool_name):
            self.worker_pool_reads.append(pool_name)
            return [SimpleNamespace(name="dashboard-worker", status="ONLINE")]

    client = FakeClient()

    class ClientContext:
        async def __aenter__(self):
            return client

        async def __aexit__(self, exc_type, exc, traceback):
            return False

    async def _empty_runs():
        return []

    monkeypatch.setattr(v2_cutover_audit, "get_client", ClientContext)
    monkeypatch.setattr(v2_cutover_audit, "_read_target_flow_runs", lambda client, deployment_ids: _empty_runs())

    async def _run_check():
        return await v2_cutover_audit._prefect_state_check("http://prefect.test/api", require_worker=True)

    result = asyncio.run(_run_check())

    assert result["ok"] is True
    assert result["online_workers"] == ["dashboard-worker"]
    assert client.worker_pool_reads == ["dashboard-test-pool"]
