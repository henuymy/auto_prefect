from __future__ import annotations

from datetime import date
from io import BytesIO

import pytest
from openpyxl import Workbook, load_workbook

from services.dashboard_v2_target_admin_service import (
    activate_target_plan,
    build_target_template,
    create_target_plan,
    get_target_values,
    import_target_template,
    list_target_plans,
    save_target_values,
)
from services.dashboard_v2_query_service import get_current_wide_table
from tests.test_dashboard_v2_query_service import _engine


def _xlsx(rows: list[tuple[object, ...]]) -> bytes:
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "目标值"
    worksheet.append([
        "scenario",
        "period_type",
        "effective_from",
        "node_type",
        "node_code",
        "indicator_code",
        "target_value",
    ])
    for row in rows:
        worksheet.append(row)
    output = BytesIO()
    workbook.save(output)
    return output.getvalue()


def test_create_edit_activate_target_plan_and_current_uses_target():
    engine = _engine()

    plan = create_target_plan(
        engine,
        plan_name="界面日目标",
        scenario="NORMAL",
        period_type="DAY",
        effective_from=date(2026, 1, 1),
        priority=20,
    )
    assert plan["status"] == "DRAFT"

    saved = save_target_values(
        engine,
        plan_id=plan["id"],
        values=[
            {"node_id": 2, "indicator_id": 1, "target_value": "111"},
            {"node_id": 3, "indicator_id": 1, "target_value": 55},
        ],
    )
    assert saved == {"plan_id": plan["id"], "saved": 2, "created": 2, "updated": 0}

    grid = get_target_values(engine, plan_id=plan["id"], node_type="BRANCH")
    assert grid["row_count"] == 1
    assert grid["rows"][0]["target_value"] == 111.0

    activated = activate_target_plan(engine, plan["id"])
    assert activated["status"] == "ACTIVE"

    current = get_current_wide_table(
        engine,
        node_type="BRANCH",
        indicator_codes=["channel_count"],
    )
    row = current["rows"][0]
    assert row["metrics"]["channel_count"] == 100
    assert row["targets"]["channel_count"] == 111


def test_template_download_contains_expected_sheets():
    workbook = load_workbook(BytesIO(build_target_template(_engine())), read_only=True)
    try:
        assert workbook.sheetnames == ["目标值", "区域参照", "指标参照"]
        assert workbook["目标值"]["A1"].value == "scenario"
        assert workbook["区域参照"]["A1"].value == "node_type"
        assert workbook["指标参照"]["A1"].value == "indicator_code"
    finally:
        workbook.close()


def test_import_template_replaces_draft_values():
    engine = _engine()
    plan = create_target_plan(
        engine,
        plan_name="导入日目标",
        scenario="NORMAL",
        period_type="DAY",
        effective_from=date(2026, 7, 1),
    )

    result = import_target_template(
        engine,
        plan_id=plan["id"],
        content=_xlsx([
            ("NORMAL", "DAY", "2026-07-01", "BRANCH", "AQ", "channel_count", 120),
            ("NORMAL", "DAY", "2026-07-01", "GRID", "AQ701", "channel_count", 60),
        ]),
    )

    assert result["imported"] == 2
    values = get_target_values(engine, plan_id=plan["id"], indicator_code="channel_count")
    assert {row["target_value"] for row in values["rows"] if row["target_value"]} == {120.0, 60.0}


def test_import_template_rejects_invalid_rows():
    engine = _engine()
    plan = create_target_plan(
        engine,
        plan_name="错误导入",
        scenario="NORMAL",
        period_type="DAY",
        effective_from=date(2026, 7, 1),
    )

    with pytest.raises(ValueError, match="period_type 只支持 DAY/MONTH"):
        import_target_template(
            engine,
            plan_id=plan["id"],
            content=_xlsx([
                ("NORMAL", "DAY_ACC", "2026-07-01", "BRANCH", "AQ", "channel_count", 120),
            ]),
        )


def test_list_target_plans_returns_value_counts():
    engine = _engine()
    plans = list_target_plans(engine)
    assert plans["plans"]
    assert all("value_count" in plan for plan in plans["plans"])
