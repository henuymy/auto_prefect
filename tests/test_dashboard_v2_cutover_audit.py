from __future__ import annotations

import asyncio
import json
from uuid import uuid4

import pytest
from prefect.client.schemas.objects import StateType

from scripts.tools.dashboard import v2_cutover_audit
from scripts.tools.dashboard.v2_cutover_audit import (
    ACTIVE_FLOW_STATE_TYPES,
    _approval_check,
    _bundle_check,
    _read_target_flow_runs,
)


def test_cutover_audit_runtime_inputs_use_runtime_root(monkeypatch, tmp_path):
    runtime_root = tmp_path / "shared-runtime"
    monkeypatch.setenv("AUTO_NOTIFY_RUNTIME_ROOT", str(runtime_root))

    args = v2_cutover_audit.parse_args([])

    assert v2_cutover_audit.resolve_runtime_artifact_path(args.bundle) == (
        runtime_root / "modules/dashboard/output/v2_migration"
    ).resolve()
    assert v2_cutover_audit.resolve_runtime_artifact_path(args.approvals) == (
        runtime_root / "modules/dashboard/output/v2_migration/cutover_approvals.json"
    ).resolve()


@pytest.mark.parametrize(
    "value",
    [
        "runtime/modules/dashboard/output/v2_migration",
        "../outside",
        "C:/outside",
    ],
)
def test_cutover_audit_rejects_non_runtime_relative_artifact(value):
    with pytest.raises(ValueError):
        v2_cutover_audit.resolve_runtime_artifact_path(value)


def test_cutover_audit_bundle_requires_pk_only_when_requested(tmp_path):
    for name in (
        "hierarchy.csv",
        "indicator_settings.json",
        "custom_indicators.json",
        "normal_day_target.json",
        "normal_month_target.json",
    ):
        (tmp_path / name).write_text("[]", encoding="utf-8")
    (tmp_path / "summary.json").write_text(json.dumps({
        "hierarchy_node_count": 163,
        "indicator_count": 522,
        "normal_day_target_count": 124,
        "normal_month_target_count": 112,
        "pk_day_target_count": 0,
        "pk_month_target_count": 0,
    }), encoding="utf-8")

    assert _bundle_check(tmp_path, require_pk=False)["ok"] is True
    required = _bundle_check(tmp_path, require_pk=True)
    assert required["ok"] is False
    assert required["pk_day_target_count"] == 0


def test_cutover_audit_approval_contains_no_password_requirement(tmp_path):
    path = tmp_path / "approvals.json"
    path.write_text(json.dumps({
        "root_password_rotated_at": "2026-07-01T12:00:00+08:00",
        "backup_restore_verified_at": "2026-07-01T13:00:00+08:00",
        "backup_restore_database": "dashboard_restore_verify",
        "approved_by": "operator",
    }), encoding="utf-8")

    result = _approval_check(path)

    assert result["ok"] is True
    assert "password" not in result


def test_cutover_audit_reads_all_target_active_runs_with_server_filter():
    deployment_ids = {uuid4(), uuid4()}

    class FakeClient:
        def __init__(self):
            self.calls = []

        async def read_flow_runs(self, **kwargs):
            self.calls.append(kwargs)
            if kwargs["offset"] == 0:
                return list(range(200))
            if kwargs["offset"] == 200:
                return ["last"]
            raise AssertionError(f"unexpected offset: {kwargs['offset']}")

    client = FakeClient()
    rows = asyncio.run(_read_target_flow_runs(client, deployment_ids))

    assert len(rows) == 201
    assert [call["offset"] for call in client.calls] == [0, 200]
    assert all(call["limit"] == 200 for call in client.calls)
    flow_filter = client.calls[0]["flow_run_filter"]
    assert set(flow_filter.deployment_id.any_) == deployment_ids
    assert set(flow_filter.state.type.any_) == ACTIVE_FLOW_STATE_TYPES
    assert StateType.PAUSED in ACTIVE_FLOW_STATE_TYPES
