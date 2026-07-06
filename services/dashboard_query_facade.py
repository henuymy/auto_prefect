"""Schema-version dispatch for dashboard read APIs."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, Callable

from services.dashboard_trigger import DEFAULT_CONFIG_PATH, load_dashboard_config


@lru_cache(maxsize=4)
def _module_for_version(version: int):
    if version == 1:
        from services import dashboard_query_service as module
    elif version == 2:
        from services import dashboard_v2_query_service as module
    else:
        raise ValueError(f"schema_version 只支持 1/2: {version!r}")
    return module


def get_dashboard_schema_version(
    config_path: str | Path = DEFAULT_CONFIG_PATH,
) -> int:
    config, _ = load_dashboard_config(config_path)
    return int(config.get("schema_version", 1) or 1)


def _adapt_kwargs(version: int, kwargs: dict[str, Any]) -> dict[str, Any]:
    adapted = dict(kwargs)
    if version == 1:
        value_mode = str(adapted.pop("value_mode", "REALTIME")).strip().upper()
        if value_mode != "REALTIME":
            raise ValueError("实时累计模式仅支持 Dashboard V2")
        adapted.pop("tree_mode", None)
        if "node_type" in adapted:
            adapted["level_type"] = adapted.pop("node_type")
        if "parent_node_type" in adapted:
            adapted["parent_level"] = adapted.pop("parent_node_type")
    else:
        if "level_type" in adapted:
            adapted["node_type"] = adapted.pop("level_type")
        if "parent_level" in adapted:
            adapted["parent_node_type"] = adapted.pop("parent_level")
    return adapted


def _dispatch(name: str, *args: Any, **kwargs: Any) -> Any:
    version = get_dashboard_schema_version()
    function: Callable[..., Any] = getattr(_module_for_version(version), name)
    return function(*args, **_adapt_kwargs(version, kwargs))


def get_indicator_catalog(*args: Any, **kwargs: Any) -> Any:
    return _dispatch("get_indicator_catalog", *args, **kwargs)


def get_latest_dashboard_run(*args: Any, **kwargs: Any) -> Any:
    result = _dispatch("get_latest_dashboard_run", *args, **kwargs)
    return {**result, "schema_version": get_dashboard_schema_version()}


def get_history_range(*args: Any, **kwargs: Any) -> Any:
    return _dispatch("get_history_range", *args, **kwargs)


def get_history_options(*args: Any, **kwargs: Any) -> Any:
    return _dispatch("get_history_options", *args, **kwargs)


def get_historical_run_id(*args: Any, **kwargs: Any) -> Any:
    return _dispatch("get_historical_run_id", *args, **kwargs)


def get_historical_with_changes(*args: Any, **kwargs: Any) -> Any:
    return _dispatch("get_historical_with_changes", *args, **kwargs)


def get_historical_matrix_page(*args: Any, **kwargs: Any) -> Any:
    return _dispatch("get_historical_matrix_page", *args, **kwargs)


def get_dashboard_matrix_page(*args: Any, **kwargs: Any) -> Any:
    return _dispatch("get_dashboard_matrix_page", *args, **kwargs)


def get_dashboard_overview(*args: Any, **kwargs: Any) -> Any:
    return _dispatch("get_dashboard_overview", *args, **kwargs)


def get_drill_down(*args: Any, **kwargs: Any) -> Any:
    return _dispatch("get_drill_down", *args, **kwargs)


def get_current_wide_table(*args: Any, **kwargs: Any) -> Any:
    return _dispatch("get_current_wide_table", *args, **kwargs)


def get_current_with_changes(*args: Any, **kwargs: Any) -> Any:
    return _dispatch("get_current_with_changes", *args, **kwargs)


def get_acc_wide_table(*args: Any, **kwargs: Any) -> Any:
    return _dispatch("get_acc_wide_table", *args, **kwargs)


def parse_change_window_minutes(value: str | None) -> list[int] | None:
    # Validation rules are schema-independent.
    from services.dashboard_query_service import parse_change_window_minutes as parse

    return parse(value)
