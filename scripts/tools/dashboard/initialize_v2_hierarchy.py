"""Initialize the approved CITY/BRANCH/GRID hierarchy in Dashboard V2."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import text
from sqlalchemy.orm import Session

from infrastructure.dashboard_mysql import (
    DashboardMySQLSettings,
    create_dashboard_engine,
)
from services.dashboard_v2_trigger import now_shanghai
from services.dashboard_v2_hierarchy import (
    initialize_base_hierarchy_in_session,
    load_bootstrap_manifest,
)
from services.dashboard_v2_readiness import EXPECTED_REVISION


def initialize(manifest_path: str | Path, *, expected_database: str) -> dict[str, int]:
    settings = DashboardMySQLSettings.from_env()
    if settings.database != expected_database:
        raise RuntimeError(
            f"拒绝初始化非目标库: configured={settings.database!r}, "
            f"expected={expected_database!r}"
        )
    manifest = load_bootstrap_manifest(manifest_path)
    engine = create_dashboard_engine(settings)
    try:
        with Session(engine) as session, session.begin():
            actual_database = session.scalar(text("SELECT DATABASE()"))
            revision = session.scalar(text("SELECT version_num FROM alembic_version"))
            if actual_database != expected_database:
                raise RuntimeError(f"实际连接库错误: {actual_database!r}")
            if revision != EXPECTED_REVISION:
                raise RuntimeError(
                    f"V2 migration revision 不一致: {revision!r} != {EXPECTED_REVISION!r}"
                )
            return initialize_base_hierarchy_in_session(
                session,
                manifest,
                initialized_at=now_shanghai().replace(tzinfo=None),
            )
    finally:
        engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description="初始化驾驶舱 V2 基础组织树")
    parser.add_argument("manifest", help="已审批的 JSON/CSV 基础结构清单")
    parser.add_argument(
        "--expected-database",
        default="dashboard",
        help="二次确认目标库名，默认 dashboard",
    )
    args = parser.parse_args()
    result = initialize(args.manifest, expected_database=args.expected_database)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
