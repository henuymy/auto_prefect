import inspect

from infrastructure.excel_client import open_excel


def test_open_excel_does_not_cleanup_orphaned_processes_by_default():
    signature = inspect.signature(open_excel)

    assert signature.parameters["cleanup_orphaned"].default is False
