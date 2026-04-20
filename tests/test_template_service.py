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
