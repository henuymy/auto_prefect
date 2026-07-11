"""Import the exported dashboard hierarchy into the independent MySQL database."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[2]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from infrastructure.dashboard_mysql import create_dashboard_engine
from services.dashboard_area_import import (
    import_areas,
    normalize_import_rows,
    read_hierarchy_workbook,
)


DEFAULT_INPUT = PROJECT_DIR / "docs" / "数据驾驶舱区域层级.xlsx"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="导入驾驶舱区域层级；CHANNEL_MANAGER 仅作路径，不写入 area"
    )
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT, help="区域层级 Excel")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    input_path = args.input.resolve()
    if not input_path.exists():
        raise FileNotFoundError(f"区域层级 Excel 不存在: {input_path}")

    raw_rows = read_hierarchy_workbook(input_path)
    rows = normalize_import_rows(raw_rows)
    engine = create_dashboard_engine()
    try:
        result = import_areas(engine, rows)
    finally:
        engine.dispose()

    print(f"导入完成: {input_path}")
    print(f"有效区域: {result['total']}")
    print(f"新增: {result['created']}")
    print(f"更新: {result['updated']}")
    print("CHANNEL_MANAGER: 未写入 area")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
