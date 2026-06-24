from __future__ import annotations

from models.dashboard_indicator import Indicator


def test_indicator_uses_minimal_first_version_schema():
    assert set(Indicator.__table__.columns.keys()) == {
        "id",
        "code",
        "name",
        "enabled",
        "source_active",
        "removed_at",
        "sort_order",
        "created_at",
        "updated_at",
    }
    assert Indicator.__table__.c.code.type.collation == "utf8mb4_bin"
