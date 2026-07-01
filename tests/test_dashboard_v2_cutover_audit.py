from __future__ import annotations

import json

from scripts.dashboard.v2_cutover_audit import _approval_check, _bundle_check


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
