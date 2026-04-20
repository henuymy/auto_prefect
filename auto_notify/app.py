"""Application entry point for the auto notify system."""

import argparse

from auto_notify.handlers.login_handler import login


def mask_secret(value, visible=6):
    if not value:
        return ""
    if len(value) <= visible * 2:
        return "*" * len(value)
    return f"{value[:visible]}...{value[-visible:]}"


def main():
    parser = argparse.ArgumentParser(description="自动化通报系统主入口。")
    subparsers = parser.add_subparsers(dest="command", required=True)

    login_parser = subparsers.add_parser("login", help="执行登录并导出 Cookie JSON")
    login_parser.add_argument("--config", default=None, help="登录配置文件路径")

    args = parser.parse_args()

    if args.command == "login":
        session = login(config_path=args.config)
        print("[SUCCESS] 登录完成")
        print(f"[INFO] Cookie JSON: {session.cookie_dump}")
        if session.jsessionid:
            print(f"[INFO] JSESSIONID: {mask_secret(session.jsessionid)}")


if __name__ == "__main__":
    main()
