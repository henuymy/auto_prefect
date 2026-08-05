from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from backend.app import app
from backend.routers import dashboard
from services.dashboard_v2_exclusion_service import (
    cancel_channel_indicator_exclusion,
    create_channel_indicator_exclusion,
    list_channel_indicator_exclusions,
    load_effective_channel_indicator_exclusions,
    preview_channel_indicator_exclusion,
    update_channel_indicator_exclusion,
)


def _engine():
    engine = create_engine("sqlite://")
    ddl = [
        """
        CREATE TABLE hierarchy_node (
            id INTEGER PRIMARY KEY, node_type TEXT NOT NULL,
            node_code TEXT NOT NULL, node_name TEXT NOT NULL,
            parent_id INTEGER, level_no INTEGER NOT NULL,
            request_enabled BOOLEAN NOT NULL DEFAULT 0,
            metric_enabled BOOLEAN NOT NULL DEFAULT 1,
            enabled BOOLEAN NOT NULL DEFAULT 1,
            sort_order INTEGER NOT NULL DEFAULT 0,
            last_seen_at DATETIME, missing_count INTEGER NOT NULL DEFAULT 0,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """,
        """
        CREATE TABLE indicator (
            id INTEGER PRIMARY KEY, code TEXT NOT NULL, name TEXT NOT NULL,
            indicator_type TEXT NOT NULL, storage_mode TEXT NOT NULL,
            enabled BOOLEAN NOT NULL DEFAULT 1,
            source_active BOOLEAN NOT NULL DEFAULT 1,
            sort_order INTEGER NOT NULL DEFAULT 0, removed_at DATETIME,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """,
        """
        CREATE TABLE channel_indicator_exclusion (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            channel_node_id INTEGER NOT NULL, indicator_id INTEGER NOT NULL,
            effective_from DATE NOT NULL, effective_to DATE,
            status TEXT NOT NULL DEFAULT 'ACTIVE', reason TEXT,
            created_by TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """,
    ]
    with engine.begin() as connection:
        for statement in ddl:
            connection.execute(text(statement))
        connection.execute(
            text(
                """
                INSERT INTO hierarchy_node
                    (id, node_type, node_code, node_name, parent_id, level_no)
                VALUES
                    (1, 'CITY', 'CITY', '市公司', NULL, 1),
                    (2, 'GRID', 'GRID', '测试网格', 1, 3),
                    (3, 'CHANNEL_MANAGER', 'MANAGER', '渠道经理', 2, 4),
                    (4, 'CHANNEL', 'CHANNEL', '测试渠道', 3, 5)
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO indicator
                    (id, code, name, indicator_type, storage_mode, enabled)
                VALUES
                    (10, 'development', '发展量', 'SOURCE', 'STORE', 1),
                    (11, 'component', '计算输入', 'SOURCE', 'COMPONENT', 1),
                    (12, 'disabled', '已关闭', 'SOURCE', 'STORE', 0)
                """
            )
        )
    return engine


def test_rule_crud_preview_and_effective_lookup():
    engine = _engine()
    saved = create_channel_indicator_exclusion(
        engine,
        channel_node_id=4,
        indicator_id=10,
        effective_from=date(2026, 8, 1),
        effective_to=date(2026, 8, 31),
        reason="  测试排除  ",
    )

    assert saved["channel_node_code"] == "CHANNEL"
    assert saved["indicator_code"] == "development"
    assert saved["reason"] == "测试排除"
    assert saved["status"] == "ACTIVE"

    listed = list_channel_indicator_exclusions(
        engine,
        active_on=date(2026, 8, 15),
    )
    assert [row["id"] for row in listed["exclusions"]] == [saved["id"]]

    with Session(engine) as session:
        effective = load_effective_channel_indicator_exclusions(
            session,
            date(2026, 8, 15),
        )
        assert set(effective) == {(4, 10)}
        assert effective[(4, 10)].id == saved["id"]
        assert not load_effective_channel_indicator_exclusions(
            session,
            date(2026, 9, 1),
        )

    preview = preview_channel_indicator_exclusion(
        engine,
        channel_node_id=4,
        indicator_id=10,
        effective_from=date(2026, 9, 1),
    )
    assert [node["node_id"] for node in preview["affected_nodes"]] == [4, 3, 2, 1]
    assert preview["affected_node_count"] == 4

    updated = update_channel_indicator_exclusion(
        engine,
        saved["id"],
        effective_to=None,
        reason=None,
    )
    assert updated["effective_to"] is None
    assert updated["reason"] is None

    assert cancel_channel_indicator_exclusion(engine, saved["id"]) == {
        "id": saved["id"],
        "deleted": True,
        "status": "CANCELLED",
    }
    with Session(engine) as session:
        assert not load_effective_channel_indicator_exclusions(
            session,
            date(2026, 8, 15),
        )


def test_rule_validation_rejects_invalid_targets_ranges_and_active_overlaps():
    engine = _engine()
    with pytest.raises(ValueError, match="CHANNEL"):
        create_channel_indicator_exclusion(
            engine,
            channel_node_id=2,
            indicator_id=10,
            effective_from=date(2026, 8, 1),
        )
    with pytest.raises(ValueError, match="STORE"):
        create_channel_indicator_exclusion(
            engine,
            channel_node_id=4,
            indicator_id=11,
            effective_from=date(2026, 8, 1),
        )
    with pytest.raises(ValueError, match="已启用"):
        create_channel_indicator_exclusion(
            engine,
            channel_node_id=4,
            indicator_id=12,
            effective_from=date(2026, 8, 1),
        )
    with pytest.raises(ValueError, match="不能早于"):
        create_channel_indicator_exclusion(
            engine,
            channel_node_id=4,
            indicator_id=10,
            effective_from=date(2026, 8, 2),
            effective_to=date(2026, 8, 1),
        )

    original = create_channel_indicator_exclusion(
        engine,
        channel_node_id=4,
        indicator_id=10,
        effective_from=date(2026, 8, 1),
        effective_to=date(2026, 8, 10),
    )
    with pytest.raises(ValueError, match="不能重叠"):
        create_channel_indicator_exclusion(
            engine,
            channel_node_id=4,
            indicator_id=10,
            effective_from=date(2026, 8, 10),
        )

    cancel_channel_indicator_exclusion(engine, original["id"])
    replacement = create_channel_indicator_exclusion(
        engine,
        channel_node_id=4,
        indicator_id=10,
        effective_from=date(2026, 8, 2),
    )
    with pytest.raises(ValueError, match="不能重叠"):
        update_channel_indicator_exclusion(
            engine,
            original["id"],
            status="ACTIVE",
        )
    assert replacement["status"] == "ACTIVE"


class _FakeEngine:
    def dispose(self) -> None:
        pass


def test_exclusion_routes_forward_payloads_and_invalidate_cached_views(monkeypatch):
    dashboard._dashboard_cache.clear()
    dashboard._dashboard_cache_failures.clear()
    dashboard._dashboard_cache_total_bytes = 12
    dashboard._dashboard_cache[("realtime", "old")] = (0.0, {}, 12)
    dashboard._version_state_cache = {"data_version": "old"}
    dashboard._version_state_cached_at = 1.0
    monkeypatch.setattr(dashboard, "get_dashboard_engine", _FakeEngine)
    captured: dict[str, object] = {}
    payload = dashboard.ChannelIndicatorExclusionPayload(
        channel_node_id=4,
        indicator_id=10,
        effective_from=date(2026, 8, 1),
        reason="原因",
    )
    monkeypatch.setattr(
        dashboard,
        "list_channel_indicator_exclusions",
        lambda engine, **kwargs: {"exclusions": [kwargs]},
    )
    monkeypatch.setattr(
        dashboard,
        "create_channel_indicator_exclusion",
        lambda engine, **kwargs: captured.setdefault("create", kwargs) or {"id": 1},
    )
    monkeypatch.setattr(
        dashboard,
        "preview_channel_indicator_exclusion",
        lambda engine, **kwargs: captured.setdefault("preview", kwargs) or {"affected_nodes": []},
    )
    monkeypatch.setattr(
        dashboard,
        "update_channel_indicator_exclusion",
        lambda engine, exclusion_id, **kwargs: captured.setdefault(
            "update", (exclusion_id, kwargs)
        ) or {"id": exclusion_id},
    )
    monkeypatch.setattr(
        dashboard,
        "cancel_channel_indicator_exclusion",
        lambda engine, exclusion_id: captured.setdefault("delete", exclusion_id)
        or {"id": exclusion_id, "deleted": True, "status": "CANCELLED"},
    )

    assert dashboard.dashboard_channel_indicator_exclusions(
        status="ACTIVE",
        active_on=date(2026, 8, 1),
        channel_node_id=4,
        indicator_id=10,
    ) == {
        "exclusions": [
            {
                "status": "ACTIVE",
                "active_on": date(2026, 8, 1),
                "channel_node_id": 4,
                "indicator_id": 10,
            }
        ]
    }
    dashboard.create_dashboard_channel_indicator_exclusion(payload)
    dashboard.preview_dashboard_channel_indicator_exclusion(payload)
    dashboard.update_dashboard_channel_indicator_exclusion(
        7,
        dashboard.ChannelIndicatorExclusionUpdatePayload(reason=None),
    )
    dashboard.delete_dashboard_channel_indicator_exclusion(7)

    assert captured["create"] == payload.model_dump()
    assert captured["preview"] == payload.model_dump()
    assert captured["update"] == (7, {"reason": None})
    assert captured["delete"] == 7
    assert not dashboard._dashboard_cache
    assert dashboard._dashboard_cache_total_bytes == 0
    assert dashboard._version_state_cache is None


def test_exclusion_api_paths_are_exposed():
    paths = app.openapi()["paths"]
    assert {"get", "post"} <= set(paths["/api/dashboard/channel-indicator-exclusions"])
    assert "post" in paths["/api/dashboard/channel-indicator-exclusions/preview"]
    detail = paths["/api/dashboard/channel-indicator-exclusions/{exclusion_id}"]
    assert {"patch", "delete"} <= set(detail)
