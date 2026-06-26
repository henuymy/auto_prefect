from __future__ import annotations

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from models.dashboard_indicator import Indicator
from services.dashboard_indicator_sync import (
    _extract_indicator_records,
    sync_indicator_records,
)


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


def test_extract_indicator_records_reads_all_result_lists_and_deduplicates():
    payload = {
        "reCode": "0000",
        "result": {
            "colList": [
                {"indCode": "A", "indName": "指标 A"},
                {"indCode": "B", "indName": "指标 B"},
            ],
            "cityList": [
                {"indCode": "B", "indName": "指标 B 重复"},
                {"indCode": "C", "indName": "指标 C"},
            ],
            "list": [
                {"indCode": "D", "indName": "指标 D"},
                {"indCode": "", "indName": "空编码"},
            ],
            "notList": {"indCode": "E", "indName": "不是列表"},
        },
    }

    assert _extract_indicator_records(payload) == [
        ("A", "指标 A", 1),
        ("B", "指标 B", 2),
        ("C", "指标 C", 3),
        ("D", "指标 D", 4),
    ]


def test_extract_indicator_records_rejects_empty_result_lists():
    with pytest.raises(ValueError, match="result 中没有可用指标"):
        _extract_indicator_records(
            {"reCode": "0000", "result": {"list": [], "cityList": []}}
        )


def test_sync_does_not_archive_enabled_indicator_missing_from_source():
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE indicator (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    code VARCHAR(100) NOT NULL UNIQUE,
                    name VARCHAR(200) NOT NULL,
                    enabled BOOLEAN NOT NULL DEFAULT 1,
                    source_active BOOLEAN NOT NULL DEFAULT 1,
                    indicator_type VARCHAR(16) NOT NULL DEFAULT 'SOURCE',
                    storage_mode VARCHAR(16) NOT NULL DEFAULT 'STORE',
                    removed_at DATETIME,
                    sort_order INTEGER NOT NULL DEFAULT 0,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
        )
    with Session(engine) as session:
        session.add_all(
            [
                Indicator(
                    code="enabled-old",
                    name="仍在采集的旧指标",
                    enabled=True,
                    source_active=True,
                    sort_order=1,
                ),
                Indicator(
                    code="disabled-old",
                    name="已停用旧指标",
                    enabled=False,
                    source_active=True,
                    sort_order=2,
                ),
            ]
        )
        session.commit()

        result = sync_indicator_records(
            session,
            [("new-code", "新指标", 1)],
        )
        session.commit()

        rows = {
            row.code: row
            for row in session.scalars(select(Indicator)).all()
        }

    assert rows["enabled-old"].source_active is True
    assert rows["enabled-old"].removed_at is None
    assert rows["disabled-old"].source_active is False
    assert result["archived_missing_codes"] == ["disabled-old"]
