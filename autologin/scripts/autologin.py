"""Command line entry point for the Selenium login flow."""

import argparse

try:
    from autologin.scripts.login_flow import AutoLogin
except ModuleNotFoundError:
    from login_flow import AutoLogin


def main():
    parser = argparse.ArgumentParser(description="内网登录与 Cookie 工具。")
    parser.add_argument("--config", default=None, help="配置文件路径，默认使用脚本目录下的 config.json")
    args = parser.parse_args()

    login = AutoLogin(args.config)
    login.run()


if __name__ == "__main__":
    main()
