import json
import shutil
from pathlib import Path
from uuid import uuid4

import pytest

from services.template_service import update_template


def write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def make_work_dir():
    work_dir = Path("runtime/test_work") / uuid4().hex
    work_dir.mkdir(parents=True, exist_ok=True)
    return work_dir


def test_update_template_skips_when_compare_same():
    work_dir = make_work_dir()
    try:
        compare_result_path = work_dir / "compare_result.json"
        source_report_path = work_dir / "report.xlsx"
        template_path = work_dir / "template.xlsx"
        manifest_path = work_dir / "update_manifest.json"
        source_report_path.write_text("report", encoding="utf-8")
        template_path.write_text("template", encoding="utf-8")
        write_json(compare_result_path, {"result": "same", "sheets": []})

        manifest = update_template(
            {
                "compare_result_path": str(compare_result_path),
                "source_report_path": str(source_report_path),
                "template_path": str(template_path),
                "manifest_path": str(manifest_path),
            }
        )

        assert manifest["status"] == "skipped"
        assert manifest["reason"] == "compare_result_same"
        assert json.loads(manifest_path.read_text(encoding="utf-8"))["status"] == "skipped"
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def test_update_template_blocks_when_compare_invalid():
    work_dir = make_work_dir()
    try:
        compare_result_path = work_dir / "compare_result.json"
        source_report_path = work_dir / "report.xlsx"
        template_path = work_dir / "template.xlsx"
        source_report_path.write_text("report", encoding="utf-8")
        template_path.write_text("template", encoding="utf-8")
        write_json(compare_result_path, {"result": "invalid", "sheets": []})

        with pytest.raises(RuntimeError, match="比对结果为 invalid"):
            update_template(
                {
                    "compare_result_path": str(compare_result_path),
                    "source_report_path": str(source_report_path),
                    "template_path": str(template_path),
                    "manifest_path": str(work_dir / "update_manifest.json"),
                }
            )
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def test_update_template_allows_same_when_configured(monkeypatch):
    work_dir = make_work_dir()
    try:
        compare_result_path = work_dir / "compare_result.json"
        source_report_path = work_dir / "report.xlsx"
        template_path = work_dir / "template.xlsx"
        manifest_path = work_dir / "update_manifest.json"
        source_report_path.write_text("report", encoding="utf-8")
        template_path.write_text("template", encoding="utf-8")
        write_json(
            compare_result_path,
            {
                "result": "same",
                "sheets": [
                    {
                        "name": "通报",
                        "new_sheet_name": "通报",
                        "template_sheet_name": "通报",
                        "result": "same",
                        "source_report_path": str(source_report_path),
                    }
                ],
            },
        )

        def fake_copy_multi(source_report_path, template_path, output_path, sheet_results, write_sheets, visible=False):
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text("updated", encoding="utf-8")
            return [{"name": "通报", "compare_result": "same"}]

        monkeypatch.setattr("services.template_service.update_template_copy_multi", fake_copy_multi)

        manifest = update_template(
            {
                "compare_result_path": str(compare_result_path),
                "source_report_path": str(source_report_path),
                "template_path": str(template_path),
                "manifest_path": str(manifest_path),
                "allow_same_update": True,
                "write_sheets": "all_compared",
            }
        )

        assert manifest["status"] == "updated"
        assert manifest["updated_sheets"][0]["compare_result"] == "same"
        assert Path(manifest["output_path"]).exists()
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)
