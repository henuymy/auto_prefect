"""Dashboard V2 change-window validation shared by API query helpers."""

from __future__ import annotations


SPARSE_SNAPSHOT_BASELINE_LOOKBACK_DAYS = 2
CHANGE_WINDOW_TOLERANCE_MINUTES = 3
CHANGE_WINDOW_MINUTES_MIN = 5
CHANGE_WINDOW_MINUTES_MAX = 1440
CHANGE_WINDOW_MINUTES_STEP = 5
CHANGE_WINDOW_LIMIT = 4


def parse_change_window_minutes(value: str | None) -> list[int] | None:
    if not value:
        return None
    windows: list[int] = []
    for item in value.split(","):
        item = item.strip()
        if not item:
            continue
        try:
            minutes = int(item)
        except ValueError as exc:
            raise ValueError(
                f"change_windows 只支持逗号分隔分钟数: {value!r}"
            ) from exc
        if not _is_valid_change_window_minutes(minutes):
            raise ValueError("change_windows 只支持 5 分钟粒度，范围 5-1440")
        if minutes not in windows:
            windows.append(minutes)
        if len(windows) >= CHANGE_WINDOW_LIMIT:
            break
    return windows or None


def _is_valid_change_window_minutes(minutes: int) -> bool:
    return (
        CHANGE_WINDOW_MINUTES_MIN <= minutes <= CHANGE_WINDOW_MINUTES_MAX
        and minutes % CHANGE_WINDOW_MINUTES_STEP == 0
    )
