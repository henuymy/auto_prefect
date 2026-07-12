import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from prefect.client.schemas.objects import StateType

from scripts.lib.prefect_status import collect_pool_statuses


NOW = datetime(2026, 7, 12, 4, 0, tzinfo=timezone.utc)


class FakeClient:
    def __init__(self):
        self.pool = SimpleNamespace(concurrency_limit=2)
        self.deployment = SimpleNamespace(id="deployment-1", name="notify-regional")
        self.runs = [
            SimpleNamespace(
                id="late-run",
                deployment_id="deployment-1",
                expected_start_time=NOW - timedelta(minutes=11),
                auto_scheduled=True,
                state=SimpleNamespace(type=StateType.SCHEDULED),
            ),
            SimpleNamespace(
                id="manual-run",
                deployment_id="deployment-1",
                expected_start_time=NOW - timedelta(hours=1),
                auto_scheduled=False,
                state=SimpleNamespace(type=StateType.SCHEDULED),
            ),
        ]

    async def read_work_pool(self, work_pool_name):
        return self.pool

    async def read_workers_for_work_pool(self, *args, **kwargs):
        return [SimpleNamespace(status="ONLINE")]

    async def read_flow_runs(self, *, flow_run_filter, work_pool_filter, limit, offset):
        state_types = set(flow_run_filter.state.type.any_)
        rows = [
            run
            for run in self.runs
            if run.state.type in state_types
        ]
        return rows[offset : offset + limit]

    async def read_deployments(self, *, limit, offset):
        rows = [self.deployment]
        return rows[offset : offset + limit]


def test_status_reads_actual_pool_limit_and_identifies_overdue_scheduled_runs():
    result = asyncio.run(
        collect_pool_statuses(
            FakeClient(),
            ["windows-notify-pool"],
            now=NOW,
            overdue_seconds=600,
        )
    )

    notify = result["windows-notify-pool"]
    assert notify["concurrency_limit"] == 2
    assert notify["online_workers"] == 1
    assert notify["queued"] == 2
    assert notify["overdue_scheduled"] == [
        {
            "deployment": "notify-regional",
            "flow_run_id": "late-run",
            "expected_start_time": (NOW - timedelta(minutes=11)).isoformat(),
            "lateness_seconds": 660,
        }
    ]
