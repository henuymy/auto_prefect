from pathlib import Path

import pytest

from services.screenshot_service import (
    capture_range_to_png,
    prepare_excel_for_capture,
    render_pdf_to_png,
    used_range_bounds,
)


class Collection:
    def __init__(self, items):
        self._items = items
        self.Count = len(items)

    def Item(self, index):
        return self._items[index - 1]


class Area:
    def __init__(self, row, column, rows, columns):
        self.Row = row
        self.Column = column
        self.Rows = Collection([None] * rows)
        self.Columns = Collection([None] * columns)


class UsedRange:
    Row = 1
    Column = 1

    def __init__(self):
        self.Rows = Collection([None] * 3)
        self.Columns = Collection([None] * 5)


class RangeValues:
    Value = (("title", None, None, None, None), ("data", 1, None, None, None), (None,) * 5)


class Worksheet:
    Name = "test"
    UsedRange = UsedRange()

    def Range(self, *_args):
        return RangeValues()

    def Cells(self, row, column):
        cell = type("Cell", (), {})()
        cell.MergeCells = row == 1 and column == 1
        cell.MergeArea = Area(1, 1, 1, 5)
        return cell


def test_used_range_bounds_preserves_full_merged_cells():
    assert used_range_bounds(Worksheet(), shrink_empty_edges=True) == (1, 1, 2, 5)


def test_copy_picture_engine_is_no_longer_supported(tmp_path: Path):
    with pytest.raises(ValueError, match="仅支持 pdf_render"):
        capture_range_to_png(object(), tmp_path / "output.png", {"engine": "copy_picture"})


def test_prepare_excel_for_capture_normalizes_application_state():
    excel = type(
        "Excel",
        (),
        {
            "Ready": True,
            "CalculationState": 0,
            "ActivePrinter": "Microsoft Print to PDF on PORTPROMPT:",
        },
    )()
    excel.CalculateUntilAsyncQueriesDone = lambda: None
    workbook = type("Workbook", (), {"Activate": lambda self: None})()
    sheet = type(
        "Sheet",
        (),
        {
            "Application": excel,
            "Parent": workbook,
            "Activate": lambda self: None,
            "Name": "test",
        },
    )()

    prepare_excel_for_capture(sheet, {})

    assert excel.PrintCommunication is True
    assert excel.DisplayAlerts is False
    assert excel.EnableEvents is False


@pytest.mark.parametrize("page_size", [(200, 100), (100, 200)])
def test_render_pdf_to_png_rejects_all_multi_page_layouts(tmp_path: Path, page_size):
    fitz = pytest.importorskip("fitz")
    pytest.importorskip("PIL")
    pdf_path = tmp_path / "multi.pdf"
    document = fitz.open()
    for _ in range(2):
        document.new_page(width=page_size[0], height=page_size[1])
    document.save(pdf_path)
    document.close()

    with pytest.raises(RuntimeError, match="只允许单页"):
        render_pdf_to_png(
            pdf_path,
            tmp_path / "output.png",
            {"pdf_dpi": 96, "pdf_crop_whitespace": False},
        )
