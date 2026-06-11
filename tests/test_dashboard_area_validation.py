from __future__ import annotations

import json
from datetime import datetime

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool

from infrastructure.dashboard_run_store import JsonCollectionRunStore
from services import dashboard_area_validation
from services.dashboard_area_validation import (
    AreaStructureMismatchError,
    EnabledArea,
    execute_area_validation_phase,
    load_enabled_area_map,
    validate_metric_rows,
)


def area_map():
    return {
        ("CITY", "A"): EnabledArea(1, "CITY", "A", "郑州市"),
        ("BRANCH", "AQ"): EnabledArea(2, "BRANCH", "AQ", "中原区"),
        ("GRID", "AQ701"): EnabledArea(3, "GRID", "AQ701", "须水网格"),
        ("CHANNEL", "AQ001"): EnabledArea(4, "CHANNEL", "AQ001", "某渠道"),
    }


def test_validate_maps_area_id_and_skips_path_and_nameless_rows():
    result = validate_metric_rows(
        [
            {
                "node_type": "CITY",
                "area_code": "A",
                "area_name": "郑州市",
                "raw_value": "10",
            },
            {
                "node_type": "CHANNEL_MANAGER",
                "area_code": "13800000000&AQ701",
                "area_name": "渠道经理",
            },
            {
                "node_type": "CHANNEL",
                "area_code": "AQ099",
                "area_name": "",
                "query_area_id": "13800000000&AQ701",
            },
        ],
        area_map(),
    )

    assert result["matched_rows"][0]["area_id"] == 1
    assert result["matched_area_count"] == 1
    assert result["skipped_row_count"] == 1
    assert result["skipped_rows"][0]["type"] == "MISSING_AREA_NAME"
    assert result["sync_required"] is False


def test_named_unmatched_area_fails_with_sync_instruction():
    with pytest.raises(AreaStructureMismatchError) as exc_info:
        validate_metric_rows(
            [
                {
                    "node_type": "CHANNEL",
                    "area_code": "AQNEW",
                    "area_name": "新渠道",
                    "query_area_id": "manager-code",
                }
            ],
            area_map(),
        )

    result = exc_info.value.result
    assert result["unmatched_area_count"] == 1
    assert result["sync_required"] is True
    assert "新批次重采" in result["next_action"]
    assert result["sync_commands"] == [
        "python scripts/dashboard/export_areas.py",
        "python scripts/dashboard/import_areas.py",
    ]


def test_name_mismatch_is_mapped_but_reported():
    result = validate_metric_rows(
        [
            {
                "node_type": "GRID",
                "area_code": "AQ701",
                "area_name": "须水新名称",
            }
        ],
        area_map(),
    )

    assert result["matched_rows"][0]["area_id"] == 3
    assert result["name_mismatch_count"] == 1


def test_load_enabled_area_map_uses_enabled_rows_only():
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE area (
                    id INTEGER PRIMARY KEY,
                    area_code VARCHAR(100) NOT NULL,
                    area_name VARCHAR(200) NOT NULL,
                    level_type VARCHAR(20) NOT NULL,
                    level_no INTEGER NOT NULL,
                    parent_id INTEGER,
                    enabled BOOLEAN NOT NULL,
                    sort_order INTEGER NOT NULL DEFAULT 0,
                    last_seen_at DATETIME,
                    missing_count INTEGER NOT NULL DEFAULT 0,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO area
                    (id, area_code, area_name, level_type, level_no, enabled)
                VALUES
                    (1, 'A', '郑州市', 'CITY', 1, 1),
                    (2, 'OLD', '旧区域', 'BRANCH', 2, 0)
                """
            )
        )

    result = load_enabled_area_map(engine)

    assert list(result) == [("CITY", "A")]
    engine.dispose()


def test_validation_failure_updates_run_and_writes_report(monkeypatch, tmp_path):
    fixed_now = datetime(2026, 6, 10, 23, 30)
    run_store = JsonCollectionRunStore(tmp_path / "runs", lambda: fixed_now)
    run_store.create("batch-area-failed", "MANUAL")
    monkeypatch.setattr(
        dashboard_area_validation,
        "load_enabled_area_map",
        lambda engine: engine.values,
    )

    with pytest.raises(AreaStructureMismatchError):
        execute_area_validation_phase(
            batch_no="batch-area-failed",
            rows=[
                {
                    "node_type": "CHANNEL",
                    "area_code": "AQNEW",
                    "area_name": "新渠道",
                }
            ],
            engine=_FakeAreaEngine(area_map()),
            run_store=run_store,
            anomaly_directory=tmp_path / "anomalies",
            now_provider=lambda: fixed_now,
        )

    record = run_store.get("batch-area-failed")
    assert record["status"] == "FAILED"
    assert record["phase"] == "VALIDATE_AREA"
    assert record["error_type"] == "AREA_NOT_FOUND"
    error_message = json.loads(record["error_message"])
    assert "新批次重采" in error_message["next_action"]
    report_path = tmp_path / "anomalies" / "batch-area-failed.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["summary"]["unmatched_area_count"] == 1


class _FakeAreaEngine:
    def __init__(self, values):
        self.values = values
