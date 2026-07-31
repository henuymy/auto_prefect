from __future__ import annotations

import json

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from infrastructure.dashboard_mysql import DashboardMySQLSettings
from models.dashboard_v2 import IndicatorFormulaComponent, IndicatorV2
from scripts.tools.dashboard import import_v2_indicator_config
from tests.test_dashboard_v2_query_service import _engine


def test_indicator_import_bundle_uses_runtime_root(monkeypatch, tmp_path):
    runtime_root = tmp_path / "shared-runtime"
    monkeypatch.setenv("AUTO_NOTIFY_RUNTIME_ROOT", str(runtime_root))

    args = import_v2_indicator_config.parse_args([])

    assert args.bundle == "modules/dashboard/output/v2_migration"
    assert import_v2_indicator_config.resolve_bundle_path(args.bundle) == (
        runtime_root / "modules/dashboard/output/v2_migration"
    ).resolve()


@pytest.mark.parametrize(
    "value",
    [
        "runtime/modules/dashboard/output/v2_migration",
        "../outside",
        "C:/outside",
    ],
)
def test_indicator_import_rejects_non_runtime_relative_bundle(value):
    with pytest.raises(ValueError):
        import_v2_indicator_config.resolve_bundle_path(value)


def test_import_v2_indicator_settings_and_custom_formulas(monkeypatch, tmp_path):
    engine = _engine()
    monkeypatch.setattr(engine, "dispose", lambda: None)
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE alembic_version (version_num TEXT)"))
        connection.execute(text(
            "INSERT INTO alembic_version VALUES (:revision)"
        ), {
            "revision": import_v2_indicator_config.EXPECTED_REVISION,
        })
    settings = DashboardMySQLSettings(
        host="db.internal",
        port=3306,
        database="dashboard_v2",
        user="dashboard_app",
        password="unused",
    )
    monkeypatch.setattr(
        import_v2_indicator_config.DashboardMySQLSettings,
        "from_env",
        lambda: settings,
    )
    monkeypatch.setattr(
        import_v2_indicator_config,
        "create_dashboard_engine",
        lambda _settings: engine,
    )
    indicator_path = tmp_path / "indicator_settings.json"
    custom_path = tmp_path / "custom_indicators.json"
    indicator_path.write_text(json.dumps([
        {
            "code": "channel_count",
            "name": "渠道数量（新）",
            "indicator_type": "SOURCE",
            "storage_mode": "COMPONENT",
            "enabled": False,
            "source_active": True,
            "sort_order": 20,
        },
        {
            "code": "new_source",
            "name": "新增源指标",
            "indicator_type": "SOURCE",
            "storage_mode": "STORE",
            "enabled": True,
            "source_active": True,
            "sort_order": 30,
        },
    ], ensure_ascii=False), encoding="utf-8")
    custom_path.write_text(json.dumps([
        {
            "code": "custom_total",
            "name": "组合指标",
            "enabled": True,
            "sort_order": 40,
            "components": [
                {
                    "source_code": "channel_count",
                    "source_storage_mode": "COMPONENT",
                    "coefficient": 2,
                },
                {
                    "source_code": "new_source",
                    "source_storage_mode": "STORE",
                    "coefficient": 1,
                },
            ],
        }
    ], ensure_ascii=False), encoding="utf-8")

    result = import_v2_indicator_config.import_indicator_config(
        indicator_settings_path=indicator_path,
        custom_indicators_path=custom_path,
        expected_database="dashboard_v2",
    )

    assert result == {
        "source_created": 1,
        "source_updated": 1,
        "custom_created": 1,
        "custom_updated": 0,
        "component_count": 2,
        "indicator_total": 3,
    }
    with Session(engine) as session:
        indicators = {
            row.code: row for row in session.scalars(select(IndicatorV2)).all()
        }
        component_count = len(
            session.scalars(select(IndicatorFormulaComponent.id)).all()
        )
    assert indicators["channel_count"].enabled is False
    assert indicators["custom_total"].indicator_type == "CUSTOM"
    assert component_count == 2


def test_import_rejects_displayed_component_source(monkeypatch, tmp_path):
    engine = _engine()
    monkeypatch.setattr(engine, "dispose", lambda: None)
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE alembic_version (version_num TEXT)"))
        connection.execute(text(
            "INSERT INTO alembic_version VALUES (:revision)"
        ), {
            "revision": import_v2_indicator_config.EXPECTED_REVISION,
        })
    settings = DashboardMySQLSettings(
        host="db.internal",
        port=3306,
        database="dashboard_v2",
        user="dashboard_app",
        password="unused",
    )
    monkeypatch.setattr(
        import_v2_indicator_config.DashboardMySQLSettings,
        "from_env",
        lambda: settings,
    )
    monkeypatch.setattr(
        import_v2_indicator_config,
        "create_dashboard_engine",
        lambda _settings: engine,
    )
    indicator_path = tmp_path / "indicator_settings.json"
    custom_path = tmp_path / "custom_indicators.json"
    indicator_path.write_text(json.dumps([{
        "code": "channel_count",
        "name": "渠道数量",
        "indicator_type": "SOURCE",
        "storage_mode": "COMPONENT",
        "enabled": True,
    }], ensure_ascii=False), encoding="utf-8")
    custom_path.write_text("[]", encoding="utf-8")

    with pytest.raises(ValueError, match="独立展示时必须使用结果落库"):
        import_v2_indicator_config.import_indicator_config(
            indicator_settings_path=indicator_path,
            custom_indicators_path=custom_path,
            expected_database="dashboard_v2",
        )
