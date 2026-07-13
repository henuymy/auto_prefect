import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import UUID

from prefect.client.schemas.objects import StateType
import pytest

from scripts.lib import prefect_startup_reconcile
from scripts.lib.prefect_startup_reconcile import (
    DeploymentPolicy,
    ReconcileResult,
    classify_deployment,
    reconcile_client,
    select_runs_to_cancel,
    should_cancel_run,
)


NOW = datetime(2026, 7, 12, 4, 0, tzinfo=timezone.utc)
ACTIVE_ID = UUID("00000000-0000-0000-0000-000000000001")
OPERATOR_PAUSED_ID = UUID("00000000-0000-0000-0000-000000000002")
UNMANAGED_ID = UUID("00000000-0000-0000-0000-000000000003")
NOTIFY_ID = UUID("00000000-0000-0000-0000-000000000004")
SESSION_ID = UUID("00000000-0000-0000-0000-000000000005")


def scheduled_run(
    *,
    run_id="run-1",
    deployment_id="deployment-1",
    minutes_late=1,
    auto_scheduled=True,
    state="SCHEDULED",
    work_pool_name="windows-notify-pool",
):
    return SimpleNamespace(
        id=run_id,
        deployment_id=deployment_id,
        state=SimpleNamespace(type=SimpleNamespace(value=state)),
        expected_start_time=NOW - timedelta(minutes=minutes_late),
        auto_scheduled=auto_scheduled,
        work_pool_name=work_pool_name,
    )


def test_classify_deployment_uses_managed_policy_sets():
    assert classify_deployment("session-keeper") is DeploymentPolicy.SESSION
    assert (
        classify_deployment("dashboard-collection")
        is DeploymentPolicy.DASHBOARD_HIGH_FREQUENCY
    )
    assert (
        classify_deployment("dashboard-monthly")
        is DeploymentPolicy.DASHBOARD_SINGLETON
    )
    assert classify_deployment("notify-daily") is DeploymentPolicy.NOTIFY
    assert classify_deployment("notify-regional") is DeploymentPolicy.NOTIFY
    assert classify_deployment("unmanaged") is None


def test_notify_more_than_ten_minutes_late_is_cancelled():
    cancel, reason = should_cancel_run(
        scheduled_run(minutes_late=11), DeploymentPolicy.NOTIFY, NOW, 600
    )

    assert cancel is True
    assert reason == "scheduled_notify_expired"


def test_notify_at_or_within_ten_minutes_is_preserved():
    for minutes_late in (9, 10):
        assert should_cancel_run(
            scheduled_run(minutes_late=minutes_late),
            DeploymentPolicy.NOTIFY,
            NOW,
            600,
        )[0] is False


def test_manual_notify_is_never_cancelled_by_lateness():
    assert should_cancel_run(
        scheduled_run(minutes_late=120, auto_scheduled=False),
        DeploymentPolicy.NOTIFY,
        NOW,
        600,
    )[0] is False


def test_expired_session_and_high_frequency_dashboard_runs_are_cancelled():
    run = scheduled_run(minutes_late=1)

    assert should_cancel_run(run, DeploymentPolicy.SESSION, NOW, 600) == (
        True,
        "scheduled_session_expired",
    )
    assert should_cancel_run(
        run, DeploymentPolicy.DASHBOARD_HIGH_FREQUENCY, NOW, 600
    ) == (True, "scheduled_dashboard_high_frequency_expired")


def test_future_runs_are_preserved_for_every_policy():
    run = scheduled_run(minutes_late=-1)

    for policy in DeploymentPolicy:
        assert should_cancel_run(run, policy, NOW, 600)[0] is False


def test_singleton_keeps_newest_overdue_auto_run_and_cancels_older_duplicates():
    runs = [
        scheduled_run(run_id="oldest", minutes_late=90),
        scheduled_run(run_id="newest", minutes_late=5),
        scheduled_run(run_id="middle", minutes_late=30),
    ]

    selected = select_runs_to_cancel(
        runs,
        {"deployment-1": DeploymentPolicy.DASHBOARD_SINGLETON},
        NOW,
        600,
    )

    assert [(run.id, reason) for run, reason in selected] == [
        ("middle", "scheduled_dashboard_singleton_duplicate"),
        ("oldest", "scheduled_dashboard_singleton_duplicate"),
    ]


def test_singleton_preserves_manual_and_future_runs():
    future = scheduled_run(run_id="future", minutes_late=-60)
    manual = scheduled_run(
        run_id="manual", minutes_late=120, auto_scheduled=False
    )
    overdue = scheduled_run(run_id="overdue", minutes_late=15)

    selected = select_runs_to_cancel(
        [future, manual, overdue],
        {"deployment-1": DeploymentPolicy.DASHBOARD_SINGLETON},
        NOW,
        600,
    )

    assert selected == []


def test_singleton_selection_is_scoped_per_deployment():
    runs = [
        scheduled_run(run_id="a-old", deployment_id="a", minutes_late=30),
        scheduled_run(run_id="a-new", deployment_id="a", minutes_late=10),
        scheduled_run(run_id="b-old", deployment_id="b", minutes_late=40),
        scheduled_run(run_id="b-new", deployment_id="b", minutes_late=20),
    ]

    selected = select_runs_to_cancel(
        runs,
        {
            "a": DeploymentPolicy.DASHBOARD_SINGLETON,
            "b": DeploymentPolicy.DASHBOARD_SINGLETON,
        },
        NOW,
        600,
    )

    assert {run.id for run, _ in selected} == {"a-old", "b-old"}


class FakePrefectClient:
    def __init__(
        self,
        *,
        deployments,
        queued=(),
        blockers=(),
        resume_failures=None,
    ):
        self.deployments = list(deployments)
        self.queued = list(queued)
        self.blockers = list(blockers)
        self.paused = []
        self.resumed = []
        self.cancelled = []
        self.updated = []
        self.read_flow_run_offsets = []
        self.resume_failures = resume_failures or {}

    async def read_deployments(self, *, limit, offset):
        return self.deployments[offset : offset + limit]

    async def pause_deployment(self, deployment_id):
        self.paused.append(deployment_id)

    async def resume_deployment(self, deployment_id):
        self.resumed.append(deployment_id)
        if error := self.resume_failures.get(deployment_id):
            raise error

    async def read_flow_runs(self, *, flow_run_filter, limit, offset):
        state_types = {
            value.value if hasattr(value, "value") else value
            for value in flow_run_filter.state.type.any_
        }
        rows = (
            self.blockers
            if state_types
            & {"RUNNING", "CANCELLING", "PAUSED"}
            else self.queued
        )
        self.read_flow_run_offsets.append((frozenset(state_types), offset))
        return rows[offset : offset + limit]

    async def set_flow_run_state(self, flow_run_id, state, force):
        self.cancelled.append((flow_run_id, state.message, force))

    async def update_deployment(self, deployment_id, deployment):
        self.updated.append((deployment_id, deployment))


def deployment(
    deployment_id,
    name,
    *,
    paused=False,
    work_pool_name="windows-notify-pool",
):
    return SimpleNamespace(
        id=deployment_id,
        name=name,
        paused=paused,
        work_pool_name=work_pool_name,
    )


def test_reconcile_migrates_existing_dynamic_notify_deployments_to_target_pool():
    client = FakePrefectClient(
        deployments=[
            deployment(
                NOTIFY_ID,
                "notify-regional",
                work_pool_name="default-agent-pool",
            ),
            deployment(ACTIVE_ID, "session-keeper", work_pool_name="windows-session-pool"),
        ]
    )

    result = asyncio.run(
        reconcile_client(client, NOW, 600, notify_work_pool="windows-notify-pool")
    )

    assert result.migrated == [
        "notify-regional:default-agent-pool->windows-notify-pool"
    ]
    assert len(client.updated) == 1
    deployment_id, update = client.updated[0]
    assert deployment_id == NOTIFY_ID
    assert update.work_pool_name == "windows-notify-pool"


def test_reconcile_does_not_rewrite_notify_deployment_already_on_target_pool():
    client = FakePrefectClient(
        deployments=[deployment(NOTIFY_ID, "notify-regional")]
    )

    result = asyncio.run(
        reconcile_client(client, NOW, 600, notify_work_pool="windows-notify-pool")
    )

    assert result.migrated == []
    assert client.updated == []


def test_reconcile_reports_legacy_pool_needed_for_preserved_manual_notify_run():
    manual = scheduled_run(
        run_id="manual-run",
        deployment_id=NOTIFY_ID,
        minutes_late=120,
        auto_scheduled=False,
        work_pool_name="default-agent-pool",
    )
    client = FakePrefectClient(
        deployments=[
            deployment(
                NOTIFY_ID,
                "notify-regional",
                work_pool_name="default-agent-pool",
            )
        ],
        queued=[manual],
    )

    result = asyncio.run(
        reconcile_client(client, NOW, 600, notify_work_pool="windows-notify-pool")
    )

    assert client.cancelled == []
    assert result.legacy_notify_pools == ["default-agent-pool"]
    assert result.legacy_notify_runs == [
        "notify-regional:manual-run:SCHEDULED:default-agent-pool"
    ]


def test_reconcile_reports_legacy_pending_notify_as_explicit_startup_blocker():
    pending = scheduled_run(
        run_id="pending-run",
        deployment_id=NOTIFY_ID,
        minutes_late=1,
        auto_scheduled=False,
        state="PENDING",
        work_pool_name="default-agent-pool",
    )
    client = FakePrefectClient(
        deployments=[
            deployment(
                NOTIFY_ID,
                "notify-regional",
                work_pool_name="default-agent-pool",
            )
        ],
        queued=[pending],
    )

    result = asyncio.run(
        reconcile_client(client, NOW, 600, notify_work_pool="windows-notify-pool")
    )

    assert result.legacy_notify_runs == [
        "notify-regional:pending-run:PENDING:default-agent-pool"
    ]


def test_reconcile_pauses_only_active_managed_deployments_and_restores_them():
    client = FakePrefectClient(
        deployments=[
            deployment(ACTIVE_ID, "session-keeper"),
            deployment(
                OPERATOR_PAUSED_ID, "dashboard-collection", paused=True
            ),
            deployment(UNMANAGED_ID, "other"),
        ]
    )

    result = asyncio.run(reconcile_client(client, NOW, 600))

    assert result.blockers == []
    assert client.paused == [ACTIVE_ID]
    assert client.resumed == [ACTIVE_ID]


def test_reconcile_cancels_with_auditable_cancelled_states():
    run = scheduled_run(deployment_id=NOTIFY_ID, minutes_late=11)
    client = FakePrefectClient(
        deployments=[deployment(NOTIFY_ID, "notify-daily")], queued=[run]
    )

    asyncio.run(reconcile_client(client, NOW, 600))

    assert client.cancelled == [(run.id, "scheduled_notify_expired", True)]


def test_reconcile_reports_in_flight_blockers_and_still_restores_deployments():
    blocker = scheduled_run(
        run_id="running-run", deployment_id=SESSION_ID, state="RUNNING"
    )
    client = FakePrefectClient(
        deployments=[deployment(SESSION_ID, "session-keeper")], blockers=[blocker]
    )

    result = asyncio.run(reconcile_client(client, NOW, 600))

    assert result.blockers == ["session-keeper:running-run:RUNNING"]
    assert client.resumed == [SESSION_ID]


def test_reconcile_restores_deployments_when_cancellation_fails():
    run = scheduled_run(deployment_id=NOTIFY_ID, minutes_late=11)
    client = FakePrefectClient(
        deployments=[deployment(NOTIFY_ID, "notify-daily")], queued=[run]
    )

    async def fail_cancellation(flow_run_id, state, force):
        raise RuntimeError("api unavailable")

    client.set_flow_run_state = fail_cancellation

    with pytest.raises(RuntimeError, match="api unavailable"):
        asyncio.run(reconcile_client(client, NOW, 600))

    assert client.resumed == [NOTIFY_ID]


def test_reconcile_attempts_every_resume_then_raises_aggregate_error():
    first_error = RuntimeError("first resume failed")
    second_error = RuntimeError("second resume failed")
    client = FakePrefectClient(
        deployments=[
            deployment(ACTIVE_ID, "session-keeper"),
            deployment(NOTIFY_ID, "notify-daily"),
        ],
        resume_failures={
            ACTIVE_ID: first_error,
            NOTIFY_ID: second_error,
        },
    )

    with pytest.raises(BaseExceptionGroup) as caught:
        asyncio.run(reconcile_client(client, NOW, 600))

    assert client.resumed == [ACTIVE_ID, NOTIFY_ID]
    assert caught.value.message == "failed to restore Prefect deployments"
    assert caught.value.exceptions == (first_error, second_error)


def test_reconcile_preserves_primary_error_and_attaches_restoration_errors():
    primary_error = RuntimeError("flow-run cancellation failed")
    restoration_error = RuntimeError("first resume failed")
    run = scheduled_run(deployment_id=NOTIFY_ID, minutes_late=11)
    client = FakePrefectClient(
        deployments=[
            deployment(ACTIVE_ID, "session-keeper"),
            deployment(NOTIFY_ID, "notify-daily"),
        ],
        queued=[run],
        resume_failures={ACTIVE_ID: restoration_error},
    )

    async def fail_cancellation(flow_run_id, state, force):
        raise primary_error

    client.set_flow_run_state = fail_cancellation

    with pytest.raises(RuntimeError) as caught:
        asyncio.run(reconcile_client(client, NOW, 600))

    assert caught.value is primary_error
    assert client.resumed == [ACTIVE_ID, NOTIFY_ID]
    assert primary_error.restoration_errors == (restoration_error,)
    assert any(
        "first resume failed" in note for note in primary_error.__notes__
    )


def test_reconcile_reads_queued_runs_in_pages_of_200():
    queued = [
        scheduled_run(run_id=f"run-{index}", deployment_id=NOTIFY_ID, minutes_late=11)
        for index in range(201)
    ]
    client = FakePrefectClient(
        deployments=[deployment(NOTIFY_ID, "notify-daily")], queued=queued
    )

    asyncio.run(reconcile_client(client, NOW, 600))

    queued_offsets = [
        offset
        for state_types, offset in client.read_flow_run_offsets
        if state_types == frozenset({StateType.SCHEDULED.value, StateType.PENDING.value})
    ]
    assert queued_offsets == [0, 200]


class FakeClientContext:
    async def __aenter__(self):
        return object()

    async def __aexit__(self, exc_type, exc, traceback):
        return False


def test_cli_returns_two_and_lists_blockers(monkeypatch, capsys):
    async def fake_reconcile(client, now, notify_grace_seconds, notify_work_pool):
        assert notify_grace_seconds == 600
        assert notify_work_pool == "windows-notify-pool"
        return ReconcileResult(
            cancelled=[],
            blockers=["session-keeper:run-1:RUNNING"],
            migrated=["notify-regional:default-agent-pool->windows-notify-pool"],
            legacy_notify_pools=["default-agent-pool"],
            legacy_notify_runs=[
                "notify-regional:manual-run:SCHEDULED:default-agent-pool"
            ],
        )

    monkeypatch.setattr(
        prefect_startup_reconcile, "get_client", FakeClientContext
    )
    monkeypatch.setattr(
        prefect_startup_reconcile, "reconcile_client", fake_reconcile
    )

    exit_code = asyncio.run(
        prefect_startup_reconcile._run_cli(600, "windows-notify-pool")
    )

    assert exit_code == 2
    captured = capsys.readouterr()
    assert "migrated:notify-regional:default-agent-pool->windows-notify-pool" in captured.out
    assert "legacy_notify_pool:default-agent-pool" in captured.out
    assert (
        "legacy_notify_run:notify-regional:manual-run:SCHEDULED:default-agent-pool"
        in captured.out
    )
    assert "blocker:session-keeper:run-1:RUNNING" in captured.err


def test_main_returns_one_when_reconciliation_fails(monkeypatch, capsys):
    async def fail_reconciliation(notify_grace_seconds, notify_work_pool):
        assert notify_work_pool == "windows-notify-pool"
        primary_error = RuntimeError("api unavailable")
        restoration_error = RuntimeError("resume deployment failed")
        restoration_error.add_note(
            "while resuming deployment notify-daily (deployment-id)"
        )
        primary_error.restoration_errors = (restoration_error,)
        raise primary_error

    monkeypatch.setattr(
        prefect_startup_reconcile, "_run_cli", fail_reconciliation
    )

    exit_code = prefect_startup_reconcile.main(
        ["--notify-grace-seconds", "600"]
    )

    assert exit_code == 1
    stderr = capsys.readouterr().err
    assert "reconciliation_failed:RuntimeError:api unavailable" in stderr
    assert (
        "restoration_failed:RuntimeError:resume deployment failed:"
        "while resuming deployment notify-daily (deployment-id)" in stderr
    )
