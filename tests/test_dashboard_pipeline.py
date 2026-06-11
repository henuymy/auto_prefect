from __future__ import annotations

import pytest

from services.dashboard_collection_service import CollectionTarget
from services.dashboard_collection_orchestrator import (
    AreaCoverageError,
    affected_grid_targets,
    ensure_complete_area_coverage,
    recover_missing_channels,
)
from services.dashboard_area_validation import EnabledArea


def test_complete_area_coverage_accepts_exact_unique_rows():
    ensure_complete_area_coverage(
        {
            "matched_area_count": 4128,
            "matched_row_count": 4128,
        },
        4128,
    )


@pytest.mark.parametrize(
    ("area_count", "row_count"),
    [
        (4127, 4127),
        (4128, 4129),
    ],
)
def test_complete_area_coverage_rejects_missing_or_duplicate_rows(
    area_count,
    row_count,
):
    with pytest.raises(AreaCoverageError):
        ensure_complete_area_coverage(
            {
                "matched_area_count": area_count,
                "matched_row_count": row_count,
            },
            4128,
        )


def test_missing_channel_is_directly_recovered():
    area_map = {
        ("CITY", "A"): EnabledArea(1, "CITY", "A", "郑州市"),
        ("CHANNEL", "C1"): EnabledArea(2, "CHANNEL", "C1", "渠道1"),
    }

    def fetch(item):
        return {
            "reCode": "0000",
            "result": {
                "tableData": [
                    {
                        "areaCode": item.target_code,
                        "areaName": item.target_name,
                        "metric": "3",
                    }
                ]
            },
        }

    result = recover_missing_channels(
        [
            {
                "level_type": "CITY",
                "area_code": "A",
                "area_name": "郑州市",
                "metric": "5",
            }
        ],
        area_map,
        fetch,
        ["metric"],
        max_workers=24,
        hard_limit=32,
        max_fallback_requests=50,
    )

    assert result["fallback_request_count"] == 1
    assert result["rows"][1]["area_code"] == "C1"


def test_new_channel_and_failed_manager_locate_parent_grid():
    grid = CollectionTarget(10, "G1", "网格1", "GRID", 10, 2, 10)
    manager = CollectionTarget(
        20,
        "M1",
        "经理1",
        "CHANNEL_MANAGER",
        None,
        10,
        10,
    )

    result = affected_grid_targets(
        [grid, manager],
        {
            "recoverable_errors": [
                {"target_type": "CHANNEL_MANAGER", "target_code": "M1"}
            ],
            "rows": [
                {
                    "level_type": "CHANNEL",
                    "area_code": "C-NEW",
                    "parent_request_code": "M1",
                }
            ],
        },
        {},
    )

    assert result == [grid]


def test_changed_manager_list_locates_grid_from_grid_observation():
    grid = CollectionTarget(10, "G1", "网格1", "GRID", 10, 2, 10)
    old_manager = CollectionTarget(
        20,
        "M-OLD",
        "旧经理",
        "CHANNEL_MANAGER",
        None,
        10,
        10,
    )

    result = affected_grid_targets(
        [grid, old_manager],
        {
            "recoverable_errors": [],
            "rows": [],
            "structure_observations": [
                {
                    "parent_type": "GRID",
                    "parent_code": "G1",
                    "child_type": "CHANNEL_MANAGER",
                    "child_code": "M-NEW",
                    "child_name": "新经理",
                }
            ],
        },
        {},
    )

    assert result == [grid]
