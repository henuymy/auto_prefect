from __future__ import annotations

from datetime import datetime

from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from models.dashboard_area import Area
from models.dashboard_request_target import RequestTarget
from services.dashboard_collection_service import CollectionTarget
from services.dashboard_structure_sync import refresh_grid_structure


def create_test_engine():
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
                    UNIQUE(level_type, area_code)
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE request_target (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    target_code VARCHAR(100) NOT NULL,
                    target_name VARCHAR(200) NOT NULL,
                    target_type VARCHAR(20) NOT NULL,
                    area_id INTEGER,
                    parent_target_id INTEGER,
                    enabled BOOLEAN NOT NULL DEFAULT 1,
                    sort_order INTEGER NOT NULL DEFAULT 0,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(target_type, target_code)
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
                    (1, 'G1', '网格1', 'GRID', 3, 1),
                    (2, 'C-OLD', '旧渠道', 'CHANNEL', 4, 1)
                """
            )
        )
        connection.execute(
            text("UPDATE area SET parent_id=1 WHERE id=2")
        )
        connection.execute(
            text(
                """
                INSERT INTO request_target
                    (id, target_code, target_name, target_type, area_id,
                     parent_target_id, enabled, sort_order)
                VALUES
                    (1, 'G1', '网格1', 'GRID', 1, NULL, 1, 10),
                    (2, 'M-OLD', '旧经理', 'CHANNEL_MANAGER', NULL, 1, 1, 10)
                """
            )
        )
    return engine


def test_refresh_grid_updates_area_before_request_targets():
    engine = create_test_engine()
    grid = CollectionTarget(1, "G1", "网格1", "GRID", 1, None, 10)

    def fetch(target):
        if target.target_type == "GRID":
            return {
                "reCode": "0000",
                "result": {
                    "tableData": [
                        {"areaCode": "G1", "areaName": "网格1"},
                        {"areaCode": "M-NEW", "areaName": "新经理"},
                    ]
                },
            }
        return {
            "reCode": "0000",
            "result": {
                "tableData": [
                    {"areaCode": "M-NEW", "areaName": "新经理"},
                    {"areaCode": "C-NEW", "areaName": "新渠道"},
                ]
            },
        }

    result = refresh_grid_structure(
        engine,
        grid,
        fetch,
        datetime(2026, 6, 11, 11, 30),
    )

    assert result["area"] == {"created": 1, "updated": 0, "disabled": 1}
    assert result["request_target"] == {
        "created": 1,
        "updated": 0,
        "disabled": 1,
    }
    with Session(engine) as session:
        new_channel = session.scalar(
            select(Area).where(Area.area_code == "C-NEW")
        )
        old_channel = session.scalar(
            select(Area).where(Area.area_code == "C-OLD")
        )
        new_manager = session.scalar(
            select(RequestTarget).where(
                RequestTarget.target_code == "M-NEW"
            )
        )
        old_manager = session.scalar(
            select(RequestTarget).where(
                RequestTarget.target_code == "M-OLD"
            )
        )
        assert new_channel.enabled is True
        assert old_channel.enabled is False
        assert new_manager.enabled is True
        assert old_manager.enabled is False
    engine.dispose()
