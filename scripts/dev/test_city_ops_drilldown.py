"""Run a real city-ops drilldown request and convert the JSON result to Excel.

Prerequisite:
    Run auto login first so runtime/session/cookie_dump.json contains city_ops.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from services.method_service import download_reports


def main():
    config = {
        "cookie_dump_path": "runtime/session/cookie_dump.json",
        "output_dir": "runtime/modules/city_ops_drilldown/output",
        "manifest_path": "runtime/modules/city_ops_drilldown/output/manifest.json",
        "request_timeout_seconds": 120,
        "verify_ssl": False,
        "trust_env": False,
        "proxies": {},
        "reports": [
            {
                "enabled": True,
                "name": "地市作战爱家亲情网下钻明细",
                "stage": "city_ops",
                "method": "POST",
                "url": "https://usm.ha.cmcc:19011/dszzCombat/dszzRestful/combatreal/getDetailByAreaAndIndex",
                "headers": {
                    "Accept": "*/*",
                    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6",
                    "Cache-Control": "no-cache",
                    "Content-Type": "application/json",
                    "Origin": "https://usm.ha.cmcc:19011",
                    "Pragma": "no-cache",
                    "Referer": "https://usm.ha.cmcc:19011/dszzCombat/dszzWeb/h5combatplatform/",
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/148.0.0.0 Safari/537.36 Edg/148.0.0.0"
                    ),
                },
                "headers_from_cookie_string": {
                    "uapToken": "*"
                },
                "body_type": "json",
                "data": {
                    "indCodes": "sgs_ajvwdz",
                    "areaId": "AQ",
                    "diyCodes": "sgs_ajvwdz",
                    "areaType": None,
                    "queryDate": "20260511",
                },
                "response_mode": "json_drilldown_to_excel",
                "drilldown": {
                    "data_path": "result.tableData",
                    "request_area_field": "areaId",
                    "next_area_field": "areaCode",
                    "levels": ["区县", "网格", "渠道/门店", "人员"],
                    "max_requests": 1000,
                },
                "excel": {
                    "sheet_name": "地市作战明细",
                    "columns": [
                        {"field": "__level_name", "header": "层级"},
                        {"field": "__parent_area_id", "header": "父级areaId"},
                        {"field": "__request_area_id", "header": "请求areaId"},
                        {"field": "areaName", "header": "名称"},
                        {"field": "areaCode", "header": "编码"},
                        {"field": "sgs_ajvwdz", "header": "爱家亲情网(V网版)"},
                    ],
                },
            }
        ],
    }
    manifest = download_reports(config)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
