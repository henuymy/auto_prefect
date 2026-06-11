from __future__ import annotations

from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from models.dashboard_request_target import RequestTarget
from services.dashboard_request_target_import import (
    import_request_targets,
    normalize_request_targets,
)


def raw_rows():
    return [
        {
            "area_code": "A",
            "area_name": "郑州市",
            "level_type": "CITY",
            "parent_code": None,
        },
        {
            "area_code": "AQ",
            "area_name": "中原区",
            "level_type": "BRANCH",
            "parent_code": "A",
        },
        {
            "area_code": "AQ701",
            "area_name": "须水网格",
            "level_type": "GRID",
            "parent_code": "AQ",
        },
        {
            "area_code": "13800000000&AQ701",
            "area_name": "渠道经理",
            "level_type": "CHANNEL_MANAGER",
            "parent_code": "AQ701",
        },
        {
            "area_code": "AQ001",
            "area_name": "某渠道",
            "level_type": "CHANNEL",
            "parent_code": "13800000000&AQ701",
        },
    ]


def create_engine_with_tables():
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
                    (id, area_code, area_name, level_type, level_no)
                VALUES
                    (1, 'A', '郑州市', 'CITY', 1),
                    (2, 'AQ', '中原区', 'BRANCH', 2),
                    (3, 'AQ701', '须水网格', 'GRID', 3)
                """
            )
        )
    return engine


def test_normalize_excludes_channel_and_manager_has_no_area():
    rows = normalize_request_targets(raw_rows())

    assert [row.target_type for row in rows] == [
        "CITY",
        "BRANCH",
        "GRID",
        "CHANNEL_MANAGER",
    ]
    assert rows[-1].area_identity is None


def test_import_is_idempotent_and_builds_request_path():
    engine = create_engine_with_tables()
    rows = normalize_request_targets(raw_rows())

    first = import_request_targets(engine, rows)
    second = import_request_targets(engine, rows)

    assert first == {"total": 4, "created": 4, "updated": 0, "disabled": 0}
    assert second == {"total": 4, "created": 0, "updated": 4, "disabled": 0}
    with Session(engine) as session:
        targets = session.scalars(select(RequestTarget)).all()
        by_type = {target.target_type: target for target in targets}
        assert len(targets) == 4
        assert by_type["CITY"].area_id == 1
        assert by_type["CHANNEL_MANAGER"].area_id is None
        assert (
            by_type["CHANNEL_MANAGER"].parent_target_id
            == by_type["GRID"].id
        )
    engine.dispose()


def test_import_disables_target_missing_from_latest_hierarchy():
    engine = create_engine_with_tables()
    rows = normalize_request_targets(raw_rows())
    import_request_targets(engine, rows)

    result = import_request_targets(
        engine,
        [row for row in rows if row.target_type != "CHANNEL_MANAGER"],
    )

    assert result["disabled"] == 1
    with Session(engine) as session:
        manager = session.scalar(
            select(RequestTarget).where(
                RequestTarget.target_type == "CHANNEL_MANAGER"
            )
        )
        assert manager.enabled is False
    engine.dispose()
