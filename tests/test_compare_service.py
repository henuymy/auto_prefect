import json
import shutil
from pathlib import Path
from uuid import uuid4

from openpyxl import Workbook

from services.compare_service import compare_report, compare_tables


def make_work_dir():
    work_dir = Path("runtime/test_work") / uuid4().hex
    work_dir.mkdir(parents=True, exist_ok=True)
    return work_dir


def create_workbook(path, sheets):
    workbook = Workbook()
    workbook.remove(workbook.active)
    for sheet_name, rows in sheets.items():
        sheet = workbook.create_sheet(sheet_name)
        for row_index, row in enumerate(rows, start=1):
            for column_index, value in enumerate(row, start=1):
                sheet.cell(row=row_index, column=column_index).value = value
    workbook.save(path)
    workbook.close()


def test_compare_tables_same():
    table = [
        ["name", "count"],
        ["A", 1],
        ["B", "2"],
    ]

    result = compare_tables(table, table)

    assert result.result == "same"
    assert result.new_row_count == 2
    assert result.old_row_count == 2


def test_compare_tables_changed_without_key():
    old_table = [
        ["name", "count"],
        ["A", 1],
    ]
    new_table = [
        ["name", "count"],
        ["A", 2],
        ["B", 1],
    ]

    result = compare_tables(new_table, old_table)

    assert result.result == "changed"
    assert result.diff_summary["added_rows"] == 1
    assert result.diff_summary["changed_rows"] == 2


def test_compare_tables_invalid_header_mismatch():
    old_table = [
        ["name", "count"],
        ["A", 1],
    ]
    new_table = [
        ["name", "total"],
        ["A", 1],
    ]

    result = compare_tables(new_table, old_table)

    assert result.result == "invalid"
    assert result.message == "新报表和模板旧数据字段不一致"


def test_compare_report_uses_openpyxl_engine_by_default():
    work_dir = make_work_dir()
    try:
        new_report_path = work_dir / "new.xlsx"
        template_path = work_dir / "template.xlsx"
        output_path = work_dir / "compare_result.json"
        create_workbook(new_report_path, {"明细": [["name", "count"], ["A", 2]]})
        create_workbook(template_path, {"明细": [["name", "count"], ["A", 1]]})

        result = compare_report(
            {
                "new_report_path": str(new_report_path),
                "template_path": str(template_path),
                "output_path": str(output_path),
                "sheet_mappings": ["明细"],
            }
        )

        assert result["result"] == "changed"
        assert result["summary"]["changed"] == 1
        assert json.loads(output_path.read_text(encoding="utf-8"))["result"] == "changed"
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def test_compare_report_stream_hash_fast_path_for_same_sheet():
    work_dir = make_work_dir()
    try:
        new_report_path = work_dir / "new.xlsx"
        template_path = work_dir / "template.xlsx"
        rows = [["name", "count"], ["A", 1], ["B", 2]]
        create_workbook(new_report_path, {"明细": rows})
        create_workbook(template_path, {"明细": rows})

        result = compare_report(
            {
                "new_report_path": str(new_report_path),
                "template_path": str(template_path),
                "sheet_mappings": ["明细"],
            }
        )

        assert result["result"] == "same"
        assert result["sheets"][0]["diff_summary"]["fast_path"] == "stream_hash_equal"
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def test_compare_report_openpyxl_parallel_multiple_sheets():
    work_dir = make_work_dir()
    try:
        new_report_path = work_dir / "new.xlsx"
        template_path = work_dir / "template.xlsx"
        create_workbook(
            new_report_path,
            {
                "一": [["name", "count"], ["A", 1]],
                "二": [["name", "count"], ["B", 3]],
                "三": [["name", "count"], ["C", 5]],
            },
        )
        create_workbook(
            template_path,
            {
                "一": [["name", "count"], ["A", 1]],
                "二": [["name", "count"], ["B", 2]],
                "三": [["name", "count"], ["C", 5]],
            },
        )

        result = compare_report(
            {
                "new_report_path": str(new_report_path),
                "template_path": str(template_path),
                "sheet_mappings": ["一", "二", "三"],
                "max_workers": 2,
            }
        )

        assert result["result"] == "changed"
        assert result["summary"]["same"] == 2
        assert result["summary"]["changed"] == 1
        assert result["summary"]["max_workers"] == 2
        assert result["max_workers"] == 2
        assert [item["name"] for item in result["sheets"]] == ["一", "二", "三"]
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)
