from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import sys
from typing import Any, Iterable, Mapping, Sequence

from prefect.client.orchestration import get_client
from prefect.client.schemas.actions import DeploymentUpdate
from prefect.client.schemas.filters import (
    FlowRunFilter,
    FlowRunFilterDeploymentId,
    FlowRunFilterState,
    FlowRunFilterStateType,
)
from prefect.client.schemas.objects import StateType
from prefect.states import Cancelled


PAGE_SIZE = 200
SESSION_DEPLOYMENTS = {"session-keeper"}
DASHBOARD_HIGH_FREQUENCY_DEPLOYMENTS = {"dashboard-collection"}
DASHBOARD_SINGLETON_DEPLOYMENTS = {
    "dashboard-daily-acc",
    "dashboard-monthly",
    "dashboard-indicator-sync",
    "dashboard-v2-partition-maintenance",
}


class DeploymentPolicy(Enum):
    SESSION = "session"
    DASHBOARD_HIGH_FREQUENCY = "dashboard_high_frequency"
    DASHBOARD_SINGLETON = "dashboard_singleton"
    NOTIFY = "notify"


@dataclass(frozen=True)
class ReconcileResult:
    cancelled: list[str]
    blockers: list[str]
    migrated: list[str]
    legacy_notify_pools: list[str]
    legacy_notify_runs: list[str]


def classify_deployment(name: str) -> DeploymentPolicy | None:
    if name in SESSION_DEPLOYMENTS:
        return DeploymentPolicy.SESSION
    if name in DASHBOARD_HIGH_FREQUENCY_DEPLOYMENTS:
        return DeploymentPolicy.DASHBOARD_HIGH_FREQUENCY
    if name in DASHBOARD_SINGLETON_DEPLOYMENTS:
        return DeploymentPolicy.DASHBOARD_SINGLETON
    if name == "notify-daily" or name.startswith("notify-"):
        return DeploymentPolicy.NOTIFY
    return None


def _state_type(run: Any) -> str:
    state = getattr(run, "state", None)
    state_type = getattr(state, "type", None)
    value = getattr(state_type, "value", state_type)
    if value:
        return str(value)
    direct = getattr(run, "state_type", "")
    return str(getattr(direct, "value", direct) or "")


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _is_overdue(run: Any, now: datetime) -> bool:
    expected_start = getattr(run, "expected_start_time", None)
    return expected_start is not None and _as_utc(expected_start) < _as_utc(now)


def should_cancel_run(
    run: Any,
    now: datetime,
) -> tuple[bool, str]:
    expected_start = getattr(run, "expected_start_time", None)
    if expected_start is None or not _is_overdue(run, now):
        return False, "scheduled_run_preserved"
    return True, "startup_overdue_run"


def select_runs_to_cancel(
    runs: Iterable[Any],
    deployment_policies: Mapping[Any, DeploymentPolicy],
    now: datetime,
) -> list[tuple[Any, str]]:
    return [
        (run, "startup_overdue_run")
        for run in runs
        if getattr(run, "deployment_id", None) in deployment_policies
        and should_cancel_run(run, now)[0]
    ]


async def _read_deployments(client: Any) -> list[Any]:
    rows: list[Any] = []
    offset = 0
    while True:
        page = await client.read_deployments(limit=PAGE_SIZE, offset=offset)
        rows.extend(page)
        if len(page) < PAGE_SIZE:
            return rows
        offset += len(page)


async def _read_runs(
    client: Any,
    deployment_ids: Sequence[Any],
    state_types: set[StateType],
) -> list[Any]:
    if not deployment_ids:
        return []
    flow_run_filter = FlowRunFilter(
        deployment_id=FlowRunFilterDeploymentId(any_=list(deployment_ids)),
        state=FlowRunFilterState(
            type=FlowRunFilterStateType(any_=list(state_types))
        ),
    )
    rows: list[Any] = []
    offset = 0
    while True:
        page = await client.read_flow_runs(
            flow_run_filter=flow_run_filter,
            limit=PAGE_SIZE,
            offset=offset,
        )
        rows.extend(page)
        if len(page) < PAGE_SIZE:
            return rows
        offset += len(page)


async def reconcile_client(
    client: Any,
    now: datetime,
    notify_work_pool: str = "windows-notify-pool",
    cancel_in_flight: bool = False,
) -> ReconcileResult:
    deployments = await _read_deployments(client)
    managed = {
        deployment.id: (deployment, policy)
        for deployment in deployments
        if (policy := classify_deployment(deployment.name)) is not None
    }
    paused_by_invocation: list[Any] = []
    cancelled: list[str] = []
    migrated: list[str] = []
    result: ReconcileResult | None = None
    primary_error: BaseException | None = None
    primary_traceback = None

    try:
        for deployment, _ in managed.values():
            if not bool(getattr(deployment, "paused", False)):
                await client.pause_deployment(deployment.id)
                paused_by_invocation.append(deployment)

        for deployment, policy in managed.values():
            if policy is not DeploymentPolicy.NOTIFY:
                continue
            current_pool = getattr(deployment, "work_pool_name", None)
            if current_pool == notify_work_pool:
                continue
            await client.update_deployment(
                deployment.id,
                DeploymentUpdate(work_pool_name=notify_work_pool),
            )
            migrated.append(
                f"{deployment.name}:{current_pool or 'unassigned'}->{notify_work_pool}"
            )

        queued = await _read_runs(
            client,
            list(managed),
            {StateType.SCHEDULED, StateType.PENDING},
        )
        policies = {
            deployment_id: policy
            for deployment_id, (_, policy) in managed.items()
        }
        selected = select_runs_to_cancel(queued, policies, now)
        selected_ids = {run.id for run, _ in selected}
        for run, reason in selected:
            await client.set_flow_run_state(
                run.id,
                Cancelled(message=reason),
                force=True,
            )
            deployment_name = managed[run.deployment_id][0].name
            cancelled.append(f"{deployment_name}:{run.id}:{reason}")

        in_flight = await _read_runs(
            client,
            list(managed),
            {StateType.RUNNING, StateType.CANCELLING, StateType.PAUSED},
        )
        if cancel_in_flight:
            for run in in_flight:
                await client.set_flow_run_state(
                    run.id,
                    Cancelled(message="startup_force_restart"),
                    force=True,
                )
                deployment_name = managed[run.deployment_id][0].name
                cancelled.append(
                    f"{deployment_name}:{run.id}:startup_force_restart"
                )
            in_flight = []
        blockers = [
            f"{managed[run.deployment_id][0].name}:{run.id}:{_state_type(run)}"
            for run in in_flight
        ]
        legacy_notify_pools = sorted(
            {
                str(run.work_pool_name)
                for run in queued
                if policies.get(run.deployment_id) is DeploymentPolicy.NOTIFY
                and run.id not in selected_ids
                and getattr(run, "work_pool_name", None)
                and run.work_pool_name != notify_work_pool
            }
        )
        legacy_notify_runs = sorted(
            f"{managed[run.deployment_id][0].name}:{run.id}:"
            f"{_state_type(run)}:{run.work_pool_name}"
            for run in queued
            if policies.get(run.deployment_id) is DeploymentPolicy.NOTIFY
            and run.id not in selected_ids
            and getattr(run, "work_pool_name", None)
            and run.work_pool_name != notify_work_pool
        )
        result = ReconcileResult(
            cancelled=cancelled,
            blockers=blockers,
            migrated=migrated,
            legacy_notify_pools=legacy_notify_pools,
            legacy_notify_runs=legacy_notify_runs,
        )
    except BaseException as exc:
        primary_error = exc
        primary_traceback = exc.__traceback__

    restoration_errors: list[BaseException] = []
    for deployment in paused_by_invocation:
        try:
            await client.resume_deployment(deployment.id)
        except BaseException as exc:
            exc.add_note(
                f"while resuming deployment {deployment.name} ({deployment.id})"
            )
            restoration_errors.append(exc)

    if restoration_errors:
        if primary_error is not None:
            primary_error.restoration_errors = tuple(restoration_errors)
            for error in restoration_errors:
                primary_error.add_note(
                    f"deployment restoration failed: {type(error).__name__}: {error}"
                )
        else:
            aggregate = BaseExceptionGroup(
                "failed to restore Prefect deployments", restoration_errors
            )
            aggregate.restoration_errors = tuple(restoration_errors)
            raise aggregate

    if primary_error is not None:
        raise primary_error.with_traceback(primary_traceback)

    assert result is not None
    return result


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Reconcile stale managed Prefect runs before Worker startup."
    )
    parser.add_argument(
        "--notify-work-pool",
        default="windows-notify-pool",
    )
    parser.add_argument(
        "--cancel-in-flight",
        action="store_true",
        help="Cancel active managed runs after their local Workers were stopped.",
    )
    return parser.parse_args(argv)


async def _run_cli(
    notify_work_pool: str,
    cancel_in_flight: bool = False,
) -> int:
    async with get_client() as client:
        result = await reconcile_client(
            client,
            datetime.now(timezone.utc),
            notify_work_pool,
            cancel_in_flight,
        )

    for migrated in result.migrated:
        print(f"migrated:{migrated}")
    for pool_name in result.legacy_notify_pools:
        print(f"legacy_notify_pool:{pool_name}")
    for run in result.legacy_notify_runs:
        print(f"legacy_notify_run:{run}")
    for cancelled in result.cancelled:
        print(f"cancelled:{cancelled}")
    if result.blockers:
        for blocker in result.blockers:
            print(f"blocker:{blocker}", file=sys.stderr)
        return 2
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        return asyncio.run(
            _run_cli(
                args.notify_work_pool,
                args.cancel_in_flight,
            )
        )
    except Exception as exc:
        print(f"reconciliation_failed:{type(exc).__name__}:{exc}", file=sys.stderr)
        for restoration_error in getattr(exc, "restoration_errors", ()):
            notes = ":".join(getattr(restoration_error, "__notes__", ()))
            context = f":{notes}" if notes else ""
            print(
                "restoration_failed:"
                f"{type(restoration_error).__name__}:{restoration_error}"
                f"{context}",
                file=sys.stderr,
            )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
