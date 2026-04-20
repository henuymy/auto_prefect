"""Main entry point: build an Excel message package, then send it to WeCom."""

import argparse
import json
from pathlib import Path

try:
    from excel_sender_wx.scripts.build_message_package import (  # type: ignore
        DEFAULT_CONFIG_PATH,
        build_message_package,
        load_config,
    )
    from excel_sender_wx.scripts.send_wecom_package import (
        cleanup_outputs,
        get_result_file,
        save_results,
        send_package,
        successful_send,
    )
except ModuleNotFoundError:
    from build_message_package import DEFAULT_CONFIG_PATH, build_message_package, load_config
    from send_wecom_package import cleanup_outputs, get_result_file, save_results, send_package, successful_send


def main():
    parser = argparse.ArgumentParser(description="Build Excel report messages and send them to WeCom.")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH))
    parser.add_argument("--dry-run", action="store_true", help="Build the package, then simulate sending.")
    parser.add_argument("--visible", action="store_true", help="Show Excel while building the package.")
    parser.add_argument("--timeout", type=int, default=30)
    args = parser.parse_args()

    config, config_path = load_config(args.config)
    package, package_file = build_message_package(config, config_path, visible=args.visible)

    webhook_url = config.get("wecom", {}).get("webhook_url", "")
    results = send_package(package, webhook_url, dry_run=args.dry_run, timeout=args.timeout)
    result_file = get_result_file(config, Path(config_path).parent)
    save_results(result_file, results)
    if not args.dry_run and successful_send(results):
        cleanup_outputs(config, Path(config_path).parent, package_file=package_file)

    summary = {
        "package_file": str(package_file),
        "result_file": str(result_file),
        "item_count": len(package.get("items", [])),
        "sent_count": len(results),
        "dry_run": args.dry_run,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
