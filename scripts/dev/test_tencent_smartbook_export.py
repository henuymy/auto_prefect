"""Manually export selected Tencent Smartbook subtables through the production service."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


PROJECT_DIR = Path(__file__).resolve().parents[2]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from services.tencent_smartbook_service import download_tencent_smartbook_report  # noqa: E402


DEFAULT_CONFIG_PATH = PROJECT_DIR / "config" / "modules" / "tencent_docs.json"


def parse_sheet_selector(value: str) -> dict[str, str]:
    kind, separator, target = str(value or "").partition(":")
    target = target.strip()
    if not separator or not target:
        raise ValueError("子表选择器必须是 id:<子表ID> 或 name:<子表名称>")
    if kind.strip().lower() == "id":
        return {"sheet_id": target}
    if kind.strip().lower() == "name":
        return {"sheet_name": target}
    raise ValueError("子表选择器必须以 id: 或 name: 开头")


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="导出腾讯文档智能表格中的指定子表到一个 XLSX 文件。")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--doc-url", help="腾讯文档智能表格链接；脚本会转换为 fileID。")
    source.add_argument("--file-id", help="腾讯文档 OpenAPI 的 Smartbook fileID。")
    parser.add_argument(
        "--sheet",
        action="append",
        default=[],
        metavar="id:<子表ID>|name:<子表名称>",
        help="指定一个要导出的子表；按命令行顺序写入工作簿，可重复传入。",
    )
    parser.add_argument("--output", default="smartbook_export.xlsx", help="输出 XLSX 路径。")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH), help="腾讯文档配置 JSON 路径。")
    parser.add_argument("--page-size", type=int, default=100, help="字段和记录接口每页读取数量，默认 100。")
    parser.add_argument("--self-test", action="store_true", help="运行不访问网络的内置自检。")
    return parser


def run_self_test() -> int:
    assert parse_sheet_selector("id:sheet-1") == {"sheet_id": "sheet-1"}
    assert parse_sheet_selector("name:汇总") == {"sheet_name": "汇总"}
    print("智能表格诊断脚本自检通过")
    return 0


def run_smartbook_export(args: argparse.Namespace) -> dict[str, object]:
    if args.page_size < 1:
        raise ValueError("--page-size 必须大于 0")
    output_path = Path(args.output)
    report: dict[str, object] = {
        "source": "tencent_smartbook",
        "name": "腾讯智能表格导出",
        "tencent_config_path": args.config,
        "output_filename": output_path.name,
        "sheets": [parse_sheet_selector(value) for value in args.sheet],
        "smartbook_page_size": args.page_size,
    }
    if args.file_id:
        report["file_id"] = args.file_id
    else:
        report["doc_url"] = args.doc_url
    return download_tencent_smartbook_report(
        report,
        output_path.parent,
        base_dir=PROJECT_DIR,
    )


def main(argv: list[str] | None = None) -> int:
    parser = build_argument_parser()
    args = parser.parse_args(argv)
    if args.self_test or (not args.doc_url and not args.file_id and not args.sheet):
        return run_self_test()
    if not (args.doc_url or args.file_id):
        parser.error("必须提供 --doc-url 或 --file-id")
    if not args.sheet:
        parser.error("至少提供一个 --sheet id:<子表ID> 或 --sheet name:<子表名称>")
    try:
        print(json.dumps(run_smartbook_export(args), ensure_ascii=False, indent=2))
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"导出失败: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
