from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import json
from typing import Any, Sequence

from prefect.client.orchestration import get_client
from prefect.client.schemas.filters import (
    FlowRunFilter,
    FlowRunFilterState,
    FlowRunFilterStateType,
    WorkPoolFilter,
    WorkPoolFilterName,
    WorkerFilter,
    WorkerFilterStatus,
)
from prefect.client.schemas.objects import StateType


PAGE_SIZE = 200


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


async def _read_runs(client: Any, pool_name: str, state_types: list[StateType]) -> list[Any]:
    rows: list[Any] = []
    offset = 0
    while True:
        page = await client.read_flow_runs(
            flow_run_filter=FlowRunFilter(
                state=FlowRunFilterState(
                    type=FlowRunFilterStateType(any_=state_types)
                )
            ),
            work_pool_filter=WorkPoolFilter(
                name=WorkPoolFilterName(any_=[pool_name])
            ),
            limit=PAGE_SIZE,
            offset=offset,
        )
        rows.extend(page)
        if len(page) < PAGE_SIZE:
            return rows
        offset += len(page)


async def _read_deployment_names(client: Any) -> dict[Any, str]:
    result: dict[Any, str] = {}
    offset = 0
    while True:
        page = await client.read_deployments(limit=PAGE_SIZE, offset=offset)
        result.update({deployment.id: deployment.name for deployment in page})
        if len(page) < PAGE_SIZE:
            return result
        offset += len(page)


async def collect_pool_statuses(
    client: Any,
    pool_names: Sequence[str],
    *,
    now: datetime,
    overdue_seconds: int,
) -> dict[str, dict[str, Any]]:
    deployment_names = await _read_deployment_names(client)
    result: dict[str, dict[str, Any]] = {}
    now_utc = _as_utc(now)

    for pool_name in pool_names:
        pool = await client.read_work_pool(pool_name)
        workers = await client.read_workers_for_work_pool(
            pool_name,
            worker_filter=WorkerFilter(
                status=WorkerFilterStatus(any_=["ONLINE"])
            ),
            limit=PAGE_SIZE,
        )
        running = await _read_runs(client, pool_name, [StateType.RUNNING])
        queued = await _read_runs(
            client,
            pool_name,
            [StateType.SCHEDULED, StateType.PENDING],
        )
        overdue: list[dict[str, Any]] = []
        for run in queued:
            expected = getattr(run, "expected_start_time", None)
            if not getattr(run, "auto_scheduled", False) or expected is None:
                continue
            lateness = int((now_utc - _as_utc(expected)).total_seconds())
            if lateness <= overdue_seconds:
                continue
            overdue.append(
                {
                    "deployment": deployment_names.get(
                        getattr(run, "deployment_id", None), "unknown"
                    ),
                    "flow_run_id": str(run.id),
                    "expected_start_time": _as_utc(expected).isoformat(),
                    "lateness_seconds": lateness,
                }
            )
        result[pool_name] = {
            "online_workers": len(workers),
            "running": len(running),
            "queued": len(queued),
            "concurrency_limit": pool.concurrency_limit,
            "overdue_scheduled": overdue,
        }
    return result


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--overdue-seconds", type=int, default=600)
    parser.add_argument("pool_names", nargs="+")
    return parser.parse_args(argv)


async def _run(args: argparse.Namespace) -> dict[str, dict[str, Any]]:
    async with get_client() as client:
        return await collect_pool_statuses(
            client,
            args.pool_names,
            now=datetime.now(timezone.utc),
            overdue_seconds=args.overdue_seconds,
        )


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    print(json.dumps(asyncio.run(_run(args)), ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
