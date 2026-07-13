"""Import platform request targets into the independent dashboard MySQL."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[3]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from infrastructure.dashboard_mysql import create_dashboard_engine
from services.dashboard_request_target_import import (
    import_request_targets,
    normalize_request_targets,
    read_hierarchy_rows,
)


DEFAULT_INPUT = PROJECT_DIR / "docs" / "数据驾驶舱区域层级.xlsx"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="导入驾驶舱请求目标；CHANNEL 不进入 request_target"
    )
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    input_path = args.input.resolve()
    if not input_path.exists():
        raise FileNotFoundError(f"区域层级 Excel 不存在: {input_path}")

    rows = normalize_request_targets(read_hierarchy_rows(input_path))
    engine = create_dashboard_engine()
    try:
        result = import_request_targets(engine, rows)
    finally:
        engine.dispose()

    print(f"导入完成: {input_path}")
    print(f"请求目标: {result['total']}")
    print(f"新增: {result['created']}")
    print(f"更新: {result['updated']}")
    print(f"停用: {result['disabled']}")
    print("CHANNEL: 未写入 request_target")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
