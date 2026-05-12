"""Convert NiceGUI form state to the existing flow JSON shape."""

from __future__ import annotations

import copy
from typing import Any

from .models import normalize_config, response_mode_value


def _clean_empty(value: Any) -> Any:
    if isinstance(value, dict):
        cleaned = {}
        for key, item in value.items():
            cleaned_item = _clean_empty(item)
            if cleaned_item not in ("", {}, [], None):
                cleaned[key] = cleaned_item
        return cleaned
    if isinstance(value, list):
        return [_clean_empty(item) for item in value if _clean_empty(item) not in ("", {}, [], None)]
    return value


def _apply_auth_preset(item: dict[str, Any]) -> dict[str, Any]:
    preset = item.get("auth_preset") or "无"
    if preset == "SSR Cookie":
        item["headers_from_cookies"] = {"ssr-token": "ssr-token"}
        item["csrf_headers_from_cookies"] = {"ssr-header": "ssr-token"}
        item.pop("headers_from_session_storage", None)
        item.pop("headers_from_local_storage", None)
    elif preset == "智慧运营 User-Info":
        item["headers_from_session_storage"] = {"User-Info": "zhyyptInfo.accessToken"}
        item.pop("headers_from_cookies", None)
        item.pop("csrf_headers_from_cookies", None)
        item.pop("headers_from_local_storage", None)
    elif preset == "无":
        item.pop("headers_from_cookies", None)
        item.pop("csrf_headers_from_cookies", None)
        item.pop("headers_from_session_storage", None)
        item.pop("headers_from_local_storage", None)
    return item


def download_to_config(item: dict[str, Any]) -> dict[str, Any]:
    item = copy.deepcopy(item)
    item["response_mode"] = response_mode_value(item.get("response_mode", "file"))
    item["method"] = (item.get("method") or "POST").upper()
    item["body_type"] = (item.get("body_type") or "form").lower()

    if item.get("auth_preset") != "自定义高级":
        item = _apply_auth_preset(item)

    if item["body_type"] == "raw":
        item.pop("data", None)
    else:
        item.pop("raw_body", None)

    if item["response_mode"] == "file":
        item.pop("excel", None)
        item.pop("drilldown", None)
    elif item["response_mode"] == "json_to_excel":
        item.pop("drilldown", None)
    elif item["response_mode"] == "json_drilldown_to_excel":
        item.setdefault("drilldown", {})
        item["drilldown"].setdefault("data_path", item.get("excel", {}).get("data_path", "result.tableData"))

    return _clean_empty(item)


def config_to_save_payload(config: dict[str, Any]) -> dict[str, Any]:
    normalized = normalize_config(config)
    downloads = [download_to_config(item) for item in normalized.get("downloads", [])]
    payload = copy.deepcopy(normalized)
    payload["downloads"] = downloads
    payload["download"] = downloads[0] if downloads else {}
    return _clean_empty(payload)

