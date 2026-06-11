from __future__ import annotations

from datetime import datetime

from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from models.dashboard_area import Area
from services.dashboard_area_import import import_areas, normalize_import_rows


def raw_rows():
    collected_at = datetime(2026, 6, 10, 15, 0)
    return [
        {
            "area_code": "A",
            "area_name": "郑州市",
            "level_type": "CITY",
            "parent_code": None,
            "collected_at": collected_at,
        },
        {
            "area_code": "AQ",
            "area_name": "中原区",
            "level_type": "BRANCH",
            "parent_code": "A",
            "collected_at": collected_at,
        },
        {
            "area_code": "Aq",
            "area_name": "大小写不同的区县",
            "level_type": "BRANCH",
            "parent_code": "A",
            "collected_at": collected_at,
        },
        {
            "area_code": "AQ701",
            "area_name": "须水网格",
            "level_type": "GRID",
            "parent_code": "AQ",
            "collected_at": collected_at,
        },
        {
            "area_code": "13800138000&AQ701",
            "area_name": "渠道经理",
            "level_type": "CHANNEL_MANAGER",
            "parent_code": "AQ701",
            "collected_at": collected_at,
        },
        {
            "area_code": "AQ008E",
            "area_name": "某渠道",
            "level_type": "CHANNEL",
            "parent_code": "13800138000&AQ701",
            "collected_at": collected_at,
        },
    ]


def test_normalize_skips_manager_and_connects_channel_to_grid():
    rows = normalize_import_rows(raw_rows())

    assert [row.level_type for row in rows] == [
        "CITY",
        "BRANCH",
        "BRANCH",
        "GRID",
        "CHANNEL",
    ]
    channel = rows[-1]
    assert channel.parent_code == "AQ701"


def test_import_is_idempotent_and_channel_parent_is_grid():
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
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    area_code VARCHAR(100) NOT NULL,
                    area_name VARCHAR(200) NOT NULL,
                    level_type VARCHAR(20) NOT NULL,
                    level_no INTEGER NOT NULL,
                    parent_id INTEGER,
                    enabled BOOLEAN NOT NULL DEFAULT 1,
                    sort_order INTEGER NOT NULL DEFAULT 0,
                    last_seen_at DATETIME,
                    missing_count INTEGER NOT NULL DEFAULT 0,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(level_type, area_code),
                    FOREIGN KEY(parent_id) REFERENCES area(id)
                )
                """
            )
        )
    rows = normalize_import_rows(raw_rows())

    first = import_areas(engine, rows)
    second = import_areas(engine, rows)

    assert first == {"total": 5, "created": 5, "updated": 0}
    assert second == {"total": 5, "created": 0, "updated": 5}
    with Session(engine) as session:
        areas = session.scalars(select(Area)).all()
        by_type = {area.level_type: area for area in areas}
        assert len(areas) == 5
        assert by_type["CHANNEL"].parent_id == by_type["GRID"].id
        assert all(area.level_type != "CHANNEL_MANAGER" for area in areas)
    engine.dispose()
