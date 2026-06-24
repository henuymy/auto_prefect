from __future__ import annotations

from decimal import Decimal

from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from models.dashboard_metric_target import MetricTarget
from services.dashboard_metric_target_import import (
    import_metric_targets,
    normalize_metric_targets,
)


def raw_rows():
    return [
        {
            "period_type": "DAY_ACC",
            "area_code": "AQ",
            "indicator_code": "broadband_rate",
            "target_value": 95.5,
        },
        {
            "period_type": "DAY_ACC",
            "area_code": "AQ701",
            "indicator_code": "broadband_rate",
            "target_value": 90,
        },
        {
            "period_type": "MONTH",
            "area_code": "AQ",
            "indicator_code": "broadband_rate",
            "target_value": 96,
        },
        {
            "period_type": "REALTIME",
            "area_code": "AQ",
            "indicator_code": "broadband_rate",
            "target_value": 94,
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
                CREATE TABLE indicator (
                    id INTEGER PRIMARY KEY,
                    code VARCHAR(100) NOT NULL,
                    name VARCHAR(200) NOT NULL,
                    enabled BOOLEAN NOT NULL DEFAULT 1,
                    source_active BOOLEAN NOT NULL DEFAULT 1,
                    removed_at DATETIME,
                    sort_order INTEGER NOT NULL DEFAULT 0,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(code)
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE metric_target (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    period_type VARCHAR(16) NOT NULL,
                    area_id INTEGER NOT NULL,
                    indicator_id INTEGER NOT NULL,
                    target_value DECIMAL(20, 4) NOT NULL,
                    enabled BOOLEAN NOT NULL DEFAULT 1,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(period_type, area_id, indicator_id)
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
                    (1, 'AQ', '中原区', 'BRANCH', 2),
                    (2, 'AQ701', '须水网格', 'GRID', 3)
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO indicator
                    (id, code, name)
                VALUES
                    (1, 'broadband_rate', '宽带完成率')
                """
            )
        )
    return engine


def test_normalize_validates_period_type():
    rows = normalize_metric_targets(raw_rows())
    assert len(rows) == 4
    assert rows[0].period_type == "DAY_ACC"
    assert rows[0].target_value == Decimal("95.5")


def test_normalize_rejects_invalid_period_type():
    import pytest

    bad_rows = [
        {
            "period_type": "INVALID",
            "area_code": "AQ",
            "indicator_code": "broadband_rate",
            "target_value": 90,
        },
    ]
    with pytest.raises(ValueError, match="period_type 只支持"):
        normalize_metric_targets(bad_rows)


def test_normalize_rejects_empty_fields():
    import pytest

    with pytest.raises(ValueError, match="area_code 不能为空"):
        normalize_metric_targets(
            [
                {
                    "period_type": "DAY_ACC",
                    "area_code": "",
                    "indicator_code": "broadband_rate",
                    "target_value": 90,
                }
            ]
        )


def test_normalize_rejects_duplicate_identity():
    import pytest

    dup_rows = [
        {
            "period_type": "DAY_ACC",
            "area_code": "AQ",
            "indicator_code": "broadband_rate",
            "target_value": 90,
        },
        {
            "period_type": "DAY_ACC",
            "area_code": "AQ",
            "indicator_code": "broadband_rate",
            "target_value": 95,
        },
    ]
    with pytest.raises(ValueError, match="重复记录"):
        normalize_metric_targets(dup_rows)


def test_import_creates_new_targets():
    engine = create_engine_with_tables()
    rows = normalize_metric_targets(raw_rows())

    result = import_metric_targets(engine, rows)

    assert result == {"total": 4, "created": 4, "updated": 0, "disabled": 0}
    with Session(engine) as session:
        targets = session.scalars(select(MetricTarget)).all()
        assert len(targets) == 4
        day_acc_aq = next(
            t for t in targets
            if t.period_type == "DAY_ACC" and t.area_id == 1
        )
        assert day_acc_aq.target_value == Decimal("95.5000")
        assert day_acc_aq.enabled is True
    engine.dispose()


def test_import_is_idempotent_and_updates_value():
    engine = create_engine_with_tables()
    rows = normalize_metric_targets(raw_rows())

    first = import_metric_targets(engine, rows)
    assert first["created"] == 4

    # Re-import with a changed value
    changed_rows = list(raw_rows())
    changed_rows[0]["target_value"] = 98.0
    second = import_metric_targets(
        engine, normalize_metric_targets(changed_rows)
    )

    assert second == {"total": 4, "created": 0, "updated": 4, "disabled": 0}
    with Session(engine) as session:
        day_acc_aq = session.scalar(
            select(MetricTarget).where(
                MetricTarget.period_type == "DAY_ACC",
                MetricTarget.area_id == 1,
            )
        )
        assert day_acc_aq.target_value == Decimal("98.0000")
    engine.dispose()


def test_import_disables_absent_targets():
    engine = create_engine_with_tables()
    all_rows = normalize_metric_targets(raw_rows())
    import_metric_targets(engine, all_rows)

    # Import only DAY_ACC rows — REALTIME and MONTH should be disabled
    day_acc_only = [r for r in raw_rows() if r["period_type"] == "DAY_ACC"]
    result = import_metric_targets(
        engine, normalize_metric_targets(day_acc_only)
    )

    assert result["disabled"] == 2
    with Session(engine) as session:
        disabled_targets = session.scalars(
            select(MetricTarget).where(MetricTarget.enabled.is_(False))
        ).all()
        disabled_periods = {t.period_type for t in disabled_targets}
        assert disabled_periods == {"REALTIME", "MONTH"}
    engine.dispose()


def test_import_rejects_unknown_area_code():
    import pytest

    engine = create_engine_with_tables()
    bad_rows = normalize_metric_targets(
        [
            {
                "period_type": "DAY_ACC",
                "area_code": "UNKNOWN",
                "indicator_code": "broadband_rate",
                "target_value": 90,
            }
        ]
    )
    with pytest.raises(ValueError, match="找不到启用的区域"):
        import_metric_targets(engine, bad_rows)
    engine.dispose()


def test_import_rejects_unknown_indicator_code():
    import pytest

    engine = create_engine_with_tables()
    bad_rows = normalize_metric_targets(
        [
            {
                "period_type": "DAY_ACC",
                "area_code": "AQ",
                "indicator_code": "nonexistent",
                "target_value": 90,
            }
        ]
    )
    with pytest.raises(ValueError, match="找不到启用的指标"):
        import_metric_targets(engine, bad_rows)
    engine.dispose()
