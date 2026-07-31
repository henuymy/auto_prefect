"""Import reviewed V1 indicator settings and custom formulas into Dashboard V2."""

from __future__ import annotations

import argparse
import json
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any, Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import delete, func, select, text
from sqlalchemy.orm import Session

from infrastructure.dashboard_mysql import DashboardMySQLSettings, create_dashboard_engine
from models.dashboard_v2 import IndicatorFormulaComponent, IndicatorV2
from services.dashboard_v2_readiness import EXPECTED_REVISION
from services.runtime_paths import resolve_runtime_relative_path


def _read_list(path: str | Path, label: str) -> list[dict[str, Any]]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, list) or not all(isinstance(row, dict) for row in payload):
        raise ValueError(f"{label} JSON 顶层必须是对象数组")
    return payload


def import_indicator_config(
    *,
    indicator_settings_path: str | Path,
    custom_indicators_path: str | Path,
    expected_database: str,
) -> dict[str, int]:
    settings = DashboardMySQLSettings.from_env()
    if settings.database != expected_database:
        raise RuntimeError(
            f"拒绝写入非目标库: configured={settings.database!r}, "
            f"expected={expected_database!r}"
        )
    indicator_rows = _read_list(indicator_settings_path, "指标设置")
    custom_rows = _read_list(custom_indicators_path, "自建指标")
    engine = create_dashboard_engine(settings)
    try:
        with Session(engine) as session, session.begin():
            revision = session.scalar(text("SELECT version_num FROM alembic_version"))
            if revision != EXPECTED_REVISION:
                raise RuntimeError(
                    f"V2 migration revision 不一致: {revision!r} != {EXPECTED_REVISION!r}"
                )
            existing = {
                row.code: row for row in session.scalars(select(IndicatorV2)).all()
            }
            source_created = 0
            source_updated = 0
            source_rows = [
                row for row in indicator_rows
                if str(row.get("indicator_type") or "").upper() == "SOURCE"
            ]
            for source in source_rows:
                code = str(source.get("code") or "").strip()
                name = str(source.get("name") or "").strip()
                storage_mode = str(source.get("storage_mode") or "STORE").strip().upper()
                if not code or not name:
                    raise ValueError("源指标 code/name 不能为空")
                if storage_mode not in {"STORE", "COMPONENT"}:
                    raise ValueError(f"指标 {code} storage_mode 非法: {storage_mode}")
                indicator = existing.get(code)
                if indicator is None:
                    indicator = IndicatorV2(
                        code=code,
                        name=name,
                        indicator_type="SOURCE",
                        storage_mode=storage_mode,
                        enabled=bool(source.get("enabled", True)),
                        source_active=bool(source.get("source_active", True)),
                        sort_order=int(source.get("sort_order", 0) or 0),
                    )
                    session.add(indicator)
                    session.flush()
                    existing[code] = indicator
                    source_created += 1
                else:
                    if indicator.indicator_type != "SOURCE":
                        raise ValueError(f"源指标编码已被自建指标占用: {code}")
                    indicator.name = name
                    indicator.storage_mode = storage_mode
                    indicator.enabled = bool(source.get("enabled", True))
                    indicator.source_active = bool(source.get("source_active", True))
                    indicator.sort_order = int(source.get("sort_order", 0) or 0)
                    source_updated += 1

            custom_created = 0
            custom_updated = 0
            component_count = 0
            for custom in custom_rows:
                code = str(custom.get("code") or "").strip()
                name = str(custom.get("name") or "").strip()
                components = custom.get("components")
                if not code or not name or not isinstance(components, list) or not components:
                    raise ValueError(f"自建指标定义不完整: {code or '<empty>'}")
                indicator = existing.get(code)
                if indicator is None:
                    indicator = IndicatorV2(
                        code=code,
                        name=name,
                        indicator_type="CUSTOM",
                        storage_mode="STORE",
                        enabled=bool(custom.get("enabled", True)),
                        source_active=False,
                        sort_order=int(custom.get("sort_order", 0) or 0),
                    )
                    session.add(indicator)
                    session.flush()
                    existing[code] = indicator
                    custom_created += 1
                else:
                    if indicator.indicator_type != "CUSTOM":
                        raise ValueError(f"自建指标编码已被源指标占用: {code}")
                    indicator.name = name
                    indicator.enabled = bool(custom.get("enabled", True))
                    indicator.storage_mode = "STORE"
                    indicator.source_active = False
                    indicator.sort_order = int(custom.get("sort_order", 0) or 0)
                    custom_updated += 1
                session.execute(
                    delete(IndicatorFormulaComponent).where(
                        IndicatorFormulaComponent.custom_indicator_id == indicator.id
                    )
                )
                seen_sources: set[str] = set()
                for index, component in enumerate(components):
                    source_code = str(component.get("source_code") or "").strip()
                    source = existing.get(source_code)
                    if source is None or source.indicator_type != "SOURCE":
                        raise ValueError(f"自建指标 {code} 的源指标不存在: {source_code}")
                    if source_code in seen_sources:
                        raise ValueError(f"自建指标 {code} 重复引用源指标: {source_code}")
                    seen_sources.add(source_code)
                    legacy_source_mode = str(
                        component.get("source_storage_mode") or ""
                    ).strip().upper()
                    if legacy_source_mode and legacy_source_mode != source.storage_mode:
                        raise ValueError(
                            f"源指标 {source_code} 的 storage_mode 必须在指标设置中统一配置，"
                            f"不能由公式组件覆盖: {legacy_source_mode} != {source.storage_mode}"
                        )
                    session.add(
                        IndicatorFormulaComponent(
                            custom_indicator_id=indicator.id,
                            source_indicator_id=source.id,
                            coefficient=Decimal(str(component.get("coefficient", 1))),
                            sort_order=index * 10,
                        )
                    )
                    component_count += 1
            total = int(session.scalar(select(func.count(IndicatorV2.id))) or 0)
            return {
                "source_created": source_created,
                "source_updated": source_updated,
                "custom_created": custom_created,
                "custom_updated": custom_updated,
                "component_count": component_count,
                "indicator_total": total,
            }
    finally:
        engine.dispose()


def resolve_bundle_path(value: str | Path) -> Path:
    return resolve_runtime_relative_path(value)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="导入驾驶舱 V2 指标设置和自建公式")
    parser.add_argument("--bundle", default="modules/dashboard/output/v2_migration")
    parser.add_argument("--expected-database", default="dashboard_v2")
    return parser.parse_args(argv)


def main() -> int:
    args = parse_args()
    bundle = resolve_bundle_path(args.bundle)
    result = import_indicator_config(
        indicator_settings_path=bundle / "indicator_settings.json",
        custom_indicators_path=bundle / "custom_indicators.json",
        expected_database=args.expected_database,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
