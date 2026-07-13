"""Benchmark read-only city-ops collection concurrency over request_target."""

from __future__ import annotations

import argparse
import copy
import json
import math
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import requests
from sqlalchemy import select
from sqlalchemy.orm import Session


PROJECT_DIR = Path(__file__).resolve().parents[3]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from infrastructure.dashboard_mysql import create_dashboard_engine
from models.dashboard_request_target import RequestTarget
from services.json_excel_service import extract_rows
from services.method_service import (
    build_cookie_jar,
    find_stage,
    load_json,
    request_report,
    response_json_with_context,
)
from services.runtime_paths import runtime_path


DEFAULT_CONFIG = PROJECT_DIR / "config" / "reports" / "家客和存量通报.json"
DEFAULT_COOKIE_DUMP = runtime_path("session/cookie_dump.json")
DEFAULT_OUTPUT = runtime_path("temp/dashboard/concurrency_benchmark.json")
DEFAULT_LEVELS = [4, 8, 16, 24, 32]


@dataclass(frozen=True)
class Target:
    code: str
    target_type: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="测试地市平台采集并发上限，只读不写库")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--cookie-dump", type=Path, default=DEFAULT_COOKIE_DUMP)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--levels", default=",".join(map(str, DEFAULT_LEVELS)))
    parser.add_argument("--cooldown-seconds", type=float, default=3)
    parser.add_argument("--timeout-seconds", type=int, default=30)
    return parser.parse_args()


def load_report(config_path: Path) -> dict:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    for report in config.get("downloads") or []:
        if (
            report.get("enabled", True)
            and report.get("stage") == "city_ops"
            and report.get("response_mode") == "json_drilldown_to_excel"
        ):
            return copy.deepcopy(report)
    raise RuntimeError(f"未找到 city_ops 下探配置: {config_path}")


def load_targets() -> list[Target]:
    engine = create_dashboard_engine()
    try:
        with Session(engine) as session:
            rows = session.execute(
                select(RequestTarget.target_code, RequestTarget.target_type)
                .where(RequestTarget.enabled.is_(True))
                .order_by(RequestTarget.target_type, RequestTarget.sort_order)
            ).all()
        return [Target(code=row.target_code, target_type=row.target_type) for row in rows]
    finally:
        engine.dispose()


def percentile(values: list[float], percentile_value: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, math.ceil(len(ordered) * percentile_value) - 1)
    return ordered[index]


def benchmark_level(
    concurrency: int,
    targets: list[Target],
    report: dict,
    stage: dict,
    timeout_seconds: int,
) -> dict:
    thread_local = threading.local()

    def worker(target: Target) -> dict:
        started = time.perf_counter()
        session = getattr(thread_local, "session", None)
        if session is None:
            session = requests.Session()
            session.trust_env = False
            session.cookies.update(
                build_cookie_jar(stage, cookie_names=report.get("cookie_names"))
            )
            session.headers.update({"User-Agent": "dashboard-benchmark/1.0"})
            thread_local.session = session

        target_report = copy.deepcopy(report)
        target_report["data"] = copy.deepcopy(report.get("data") or {})
        target_report["data"].update(
            {
                "areaId": target.code,
                "queryDate": datetime.now().strftime("%Y%m%d"),
                "indCodes": "sgs_ajvwdz",
                "diyCodes": "sgs_ajvwdz",
            }
        )
        target_report["request_retries"] = 0
        response = request_report(
            session,
            target_report,
            stage,
            timeout_seconds,
            False,
            None,
            retry={"retries": 0},
        )
        payload = response_json_with_context(response)
        rows = extract_rows(payload, "result.tableData")
        return {
            "ok": response.status_code == 200,
            "status_code": response.status_code,
            "latency_seconds": time.perf_counter() - started,
            "row_count": len(rows),
            "target_code": target.code,
            "target_type": target.target_type,
        }

    started = time.perf_counter()
    results = []
    errors = []
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = {executor.submit(worker, target): target for target in targets}
        for future in as_completed(futures):
            target = futures[future]
            try:
                result = future.result()
                results.append(result)
                if not result["ok"]:
                    errors.append(
                        {
                            "target_code": target.code,
                            "target_type": target.target_type,
                            "error": f"HTTP {result['status_code']}",
                        }
                    )
            except Exception as exc:
                errors.append(
                    {
                        "target_code": target.code,
                        "target_type": target.target_type,
                        "error": f"{type(exc).__name__}: {str(exc)[:300]}",
                    }
                )
    elapsed = time.perf_counter() - started
    latencies = [result["latency_seconds"] for result in results]
    return {
        "concurrency": concurrency,
        "target_count": len(targets),
        "success_count": len(results) - sum(not result["ok"] for result in results),
        "error_count": len(errors),
        "elapsed_seconds": round(elapsed, 3),
        "throughput_rps": round(len(results) / elapsed, 3) if elapsed else 0,
        "latency_avg_seconds": round(sum(latencies) / len(latencies), 3) if latencies else 0,
        "latency_p50_seconds": round(percentile(latencies, 0.50), 3),
        "latency_p95_seconds": round(percentile(latencies, 0.95), 3),
        "latency_max_seconds": round(max(latencies), 3) if latencies else 0,
        "returned_row_count": sum(result["row_count"] for result in results),
        "errors": errors[:20],
    }


def main() -> int:
    args = parse_args()
    levels = [
        int(part.strip())
        for part in str(args.levels).split(",")
        if part.strip()
    ]
    if not levels or any(level <= 0 or level > 64 for level in levels):
        raise ValueError("并发档位必须在1到64之间")

    targets = load_targets()
    report = load_report(args.config.resolve())
    cookie_dump = load_json(args.cookie_dump.resolve())
    stage = find_stage(cookie_dump, "city_ops")

    benchmark = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "endpoint": report["url"],
        "target_count": len(targets),
        "levels": [],
    }
    previous_throughput = None
    for index, level in enumerate(levels):
        result = benchmark_level(
            level,
            targets,
            report,
            stage,
            args.timeout_seconds,
        )
        benchmark["levels"].append(result)
        print(
            f"并发={level}: success={result['success_count']}, "
            f"errors={result['error_count']}, elapsed={result['elapsed_seconds']}s, "
            f"rps={result['throughput_rps']}, p95={result['latency_p95_seconds']}s"
        )
        if result["error_count"] > 0:
            benchmark["stop_reason"] = "出现请求错误，停止继续加压"
            break
        if (
            previous_throughput is not None
            and result["throughput_rps"] < previous_throughput * 0.90
        ):
            benchmark["stop_reason"] = "吞吐量下降超过10%，停止继续加压"
            break
        previous_throughput = result["throughput_rps"]
        if index < len(levels) - 1 and args.cooldown_seconds > 0:
            time.sleep(args.cooldown_seconds)

    output_path = args.output.resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(benchmark, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"结果文件: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
