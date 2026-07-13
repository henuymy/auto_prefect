"""Run one complete dashboard collection batch."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from services.dashboard_v2_trigger import load_dashboard_config
from services.dashboard_v2_pipeline import execute_dashboard_v2_pipeline


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="执行一次完整驾驶舱采集批次")
    parser.add_argument("--config", default="config/dashboard/session.json")
    parser.add_argument(
        "--mode",
        choices=["REALTIME", "DAY_ACC", "MONTH"],
        default="REALTIME",
    )
    parser.add_argument(
        "--trigger-type",
        choices=["MANUAL", "SCHEDULED"],
        default="MANUAL",
    )
    parser.add_argument("--force-refresh", action="store_true")
    parser.add_argument("--stat-date", type=date.fromisoformat)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    load_dashboard_config(args.config)
    common = {
        "config_path": args.config,
        "trigger_type": args.trigger_type,
        "force_refresh": args.force_refresh,
        "stat_date": args.stat_date,
    }
    result = execute_dashboard_v2_pipeline(**common, period_type=args.mode)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
