"""Import metric target values into the independent dashboard MySQL."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[2]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from infrastructure.dashboard_mysql import create_dashboard_engine
from services.dashboard_metric_target_import import (
    import_metric_targets,
    normalize_metric_targets,
    read_metric_target_rows,
)


DEFAULT_INPUT = PROJECT_DIR / "docs" / "指标目标值.xlsx"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="导入驾驶舱指标目标值"
    )
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    input_path = args.input.resolve()
    if not input_path.exists():
        raise FileNotFoundError(f"指标目标值 Excel 不存在: {input_path}")

    rows = normalize_metric_targets(read_metric_target_rows(input_path))
    engine = create_dashboard_engine()
    try:
        result = import_metric_targets(engine, rows)
    finally:
        engine.dispose()

    print(f"导入完成: {input_path}")
    print(f"目标值: {result['total']}")
    print(f"新增: {result['created']}")
    print(f"更新: {result['updated']}")
    print(f"停用: {result['disabled']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
