from __future__ import annotations

import pytest

from services.dashboard_indicator_sync import _extract_indicator_records


def test_extract_indicator_records_reads_result_list():
    payload = {
        "reCode": "0000",
        "result": {
            "list": [
                {"indCode": "A", "indName": "指标 A"},
                {"indCode": "B", "indName": "指标 B"},
            ]
        },
    }

    assert _extract_indicator_records(payload) == [
        ("A", "指标 A", 1),
        ("B", "指标 B", 2),
    ]


def test_extract_indicator_records_rejects_empty_list():
    with pytest.raises(ValueError, match="result.list 为空"):
        _extract_indicator_records(
            {"reCode": "0000", "result": {"list": []}}
        )
