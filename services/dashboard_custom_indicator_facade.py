"""Schema-version dispatch for custom indicator APIs."""

from __future__ import annotations

from typing import Any

from services.dashboard_query_facade import get_dashboard_schema_version


def _service():
    if get_dashboard_schema_version() == 2:
        from services import dashboard_v2_custom_indicator_service as service
    else:
        from services import dashboard_custom_indicator_service as service
    return service


def list_custom_indicators(*args: Any, **kwargs: Any) -> Any:
    return _service().list_custom_indicators(*args, **kwargs)


def upsert_custom_indicator(*args: Any, **kwargs: Any) -> Any:
    return _service().upsert_custom_indicator(*args, **kwargs)


def delete_custom_indicator(*args: Any, **kwargs: Any) -> Any:
    return _service().delete_custom_indicator(*args, **kwargs)


def update_indicator_settings(*args: Any, **kwargs: Any) -> Any:
    return _service().update_indicator_settings(*args, **kwargs)
