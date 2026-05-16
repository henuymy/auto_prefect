import pytest

from services.notify_service import send_package
from services.screenshot_service import item_output_name, package_has_only_text_items


def test_send_package_dry_run_preserves_order():
    package = {
        "items": [
            {
                "workbook": "wb",
                "report": "rp",
                "item_index": 1,
                "type": "image",
                "sheet": "通报",
                "image": {"base64": "x", "md5": "y"},
            },
            {
                "workbook": "wb",
                "report": "rp",
                "item_index": 2,
                "type": "text",
                "sheet": "通报语句",
                "text": "hello",
            },
        ]
    }

    results = send_package(package, "", dry_run=True)

    assert [item["type"] for item in results] == ["image", "text"]
    assert all(item["send"]["dry_run"] for item in results)


def test_send_package_blocks_placeholder_webhook():
    package = {"items": [{"type": "text", "text": "hello"}]}

    with pytest.raises(ValueError, match="webhook_url"):
        send_package(package, "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=YOUR_KEY")


def test_item_output_name_removes_invalid_filename_chars():
    name = item_output_name(1, 2, 3, 'work/book', 'report:name', 'sheet*name')

    assert name.startswith("01_02_03_work_book_report_name_sheet_name")
    assert name.endswith(".png")


def test_package_has_only_text_items():
    assert package_has_only_text_items({
        "workbooks": [{"reports": [{"items": [{"type": "text", "sheet": "文字"}]}]}]
    }) is True
    assert package_has_only_text_items({
        "workbooks": [{"reports": [{"items": [{"type": "image", "sheet": "通报"}]}]}]
    }) is False
