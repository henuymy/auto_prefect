from pathlib import Path

import pytest
from services import screenshot_service

from services.screenshot_service import (
    capture_range_to_png,
    prepare_excel_for_capture,
    render_pdf_to_png,
    resolve_pdf_dpi,
    used_range_bounds,
)


@pytest.mark.parametrize(
    ("capture", "expected"),
    [
        ({}, 300),
        ({"pdf_dpi": 600}, 600),
        ({"pdf_dpi": 1}, 96),
        ({"pdf_dpi": 999}, 600),
        ({"render_dpi": 96}, 300),
        ({"pdf_dpi": 300, "export_scale": 1}, 300),
        ({"pdf_dpi": 300, "export_scale": 2}, 300),
    ],
)
def test_resolve_pdf_dpi_uses_only_pdf_dpi(capture, expected):
    assert resolve_pdf_dpi(capture) == expected


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


def test_prepare_excel_for_capture_rejects_unexpected_printer_without_mutation():
    class Excel:
        Ready = True
        CalculationState = 0

        def __init__(self):
            self._active_printer = "RustDesk Printer on Ne02:"
            self.set_calls = []

        @property
        def ActivePrinter(self):
            return self._active_printer

        @ActivePrinter.setter
        def ActivePrinter(self, value):
            self.set_calls.append(value)
            self._active_printer = value

        def CalculateUntilAsyncQueriesDone(self):
            return None

    excel = Excel()
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

    with pytest.raises(RuntimeError, match="无法为 Excel 设置稳定打印机"):
        prepare_excel_for_capture(sheet, {"excel_active_printer": "Microsoft Print to PDF on PORTPROMPT:"})

    assert excel.set_calls == []


def test_open_excel_switches_default_printer_before_starting_excel():
    events = []
    excel_instance = object()

    class PrinterApi:
        def __init__(self):
            self.current = "RustDesk Printer"

        def GetDefaultPrinter(self):
            return self.current

        def SetDefaultPrinter(self, printer):
            events.append(("set", printer))
            self.current = printer

    def open_excel(*, visible, cleanup_orphaned):
        events.append(("open", visible, cleanup_orphaned))
        return excel_instance

    excel = screenshot_service.open_excel_after_setting_default_printer(
        PrinterApi(),
        "Microsoft Print to PDF",
        open_excel,
        visible=False,
    )

    assert excel is excel_instance
    assert events == [
        ("set", "Microsoft Print to PDF"),
        ("open", False, True),
    ]


def test_switch_default_printer_only_when_target_is_not_current():
    class PrinterApi:
        def __init__(self, current):
            self.current = current
            self.set_calls = []

        def GetDefaultPrinter(self):
            return self.current

        def SetDefaultPrinter(self, printer):
            self.set_calls.append(printer)
            self.current = printer

    target = "Microsoft Print to PDF"
    already_target = PrinterApi(target)
    switched = PrinterApi("RustDesk Printer")

    assert screenshot_service.switch_default_printer_if_needed(already_target, target) is False
    assert already_target.set_calls == []
    assert screenshot_service.switch_default_printer_if_needed(switched, target) is True
    assert switched.set_calls == [target]


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
