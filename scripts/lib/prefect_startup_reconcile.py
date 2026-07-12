from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import sys
from typing import Any, Iterable, Mapping, Sequence

from prefect.client.orchestration import get_client
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


def is_manual_run(run: Any) -> bool:
    return not bool(getattr(run, "auto_scheduled", False))


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
    policy: DeploymentPolicy,
    now: datetime,
    notify_grace_seconds: int,
) -> tuple[bool, str]:
    expected_start = getattr(run, "expected_start_time", None)
    if expected_start is None or not _is_overdue(run, now):
        return False, "scheduled_run_preserved"

    if policy is DeploymentPolicy.NOTIFY:
        if is_manual_run(run):
            return False, "manual_notify_preserved"
        lateness = (_as_utc(now) - _as_utc(expected_start)).total_seconds()
        if lateness > notify_grace_seconds:
            return True, "scheduled_notify_expired"
        return False, "scheduled_notify_within_grace"

    if policy is DeploymentPolicy.SESSION:
        return True, "scheduled_session_expired"
    if policy is DeploymentPolicy.DASHBOARD_HIGH_FREQUENCY:
        return True, "scheduled_dashboard_high_frequency_expired"

    return False, "scheduled_dashboard_singleton_candidate"


def select_runs_to_cancel(
    runs: Iterable[Any],
    deployment_policies: Mapping[Any, DeploymentPolicy],
    now: datetime,
    notify_grace_seconds: int,
) -> list[tuple[Any, str]]:
    selected: list[tuple[Any, str]] = []
    singleton_runs: dict[Any, list[Any]] = {}

    for run in runs:
        policy = deployment_policies.get(getattr(run, "deployment_id", None))
        if policy is None:
            continue
        if policy is DeploymentPolicy.DASHBOARD_SINGLETON:
            if not is_manual_run(run) and _is_overdue(run, now):
                singleton_runs.setdefault(run.deployment_id, []).append(run)
            continue

        cancel, reason = should_cancel_run(
            run, policy, now, notify_grace_seconds
        )
        if cancel:
            selected.append((run, reason))

    for candidates in singleton_runs.values():
        newest_first = sorted(
            candidates,
            key=lambda run: _as_utc(run.expected_start_time),
            reverse=True,
        )
        selected.extend(
            (run, "scheduled_dashboard_singleton_duplicate")
            for run in newest_first[1:]
        )

    return selected


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
    notify_grace_seconds: int,
) -> ReconcileResult:
    deployments = await _read_deployments(client)
    managed = {
        deployment.id: (deployment, policy)
        for deployment in deployments
        if (policy := classify_deployment(deployment.name)) is not None
    }
    paused_by_invocation: list[Any] = []
    cancelled: list[str] = []
    result: ReconcileResult | None = None
    primary_error: BaseException | None = None
    primary_traceback = None

    try:
        for deployment, _ in managed.values():
            if not bool(getattr(deployment, "paused", False)):
                await client.pause_deployment(deployment.id)
                paused_by_invocation.append(deployment)

        queued = await _read_runs(
            client,
            list(managed),
            {StateType.SCHEDULED, StateType.PENDING},
        )
        policies = {
            deployment_id: policy
            for deployment_id, (_, policy) in managed.items()
        }
        for run, reason in select_runs_to_cancel(
            queued, policies, now, notify_grace_seconds
        ):
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
        blockers = [
            f"{managed[run.deployment_id][0].name}:{run.id}:{_state_type(run)}"
            for run in in_flight
        ]
        result = ReconcileResult(cancelled=cancelled, blockers=blockers)
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


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Reconcile stale managed Prefect runs before Worker startup."
    )
    parser.add_argument(
        "--notify-grace-seconds",
        type=_positive_int,
        default=600,
    )
    return parser.parse_args(argv)


async def _run_cli(notify_grace_seconds: int) -> int:
    async with get_client() as client:
        result = await reconcile_client(
            client,
            datetime.now(timezone.utc),
            notify_grace_seconds,
        )

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
        return asyncio.run(_run_cli(args.notify_grace_seconds))
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
