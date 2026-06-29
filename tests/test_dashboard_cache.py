from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from threading import Lock
from time import sleep

import backend.routers.dashboard as dashboard_router


def _reset_cache() -> None:
    with dashboard_router._dashboard_cache_lock:
        dashboard_router._dashboard_cache.clear()
        dashboard_router._dashboard_cache_inflight.clear()
        dashboard_router._dashboard_cache_failures.clear()
        dashboard_router._dashboard_cache_total_bytes = 0
        for key in dashboard_router._dashboard_cache_stats:
            dashboard_router._dashboard_cache_stats[key] = 0
    dashboard_router._invalidate_version_state()


def test_same_cache_key_uses_single_loader_for_concurrent_requests():
    _reset_cache()
    calls = 0
    calls_lock = Lock()

    def loader():
        nonlocal calls
        with calls_lock:
            calls += 1
        sleep(0.05)
        return {"rows": [1, 2, 3]}

    def request():
        return dashboard_router._load_cached_response(
            namespace="history",
            resolved_key=("run-1", "matrix"),
            ttl_seconds=60,
            loader=loader,
        )

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(lambda _: request(), range(8)))

    assert calls == 1
    assert all(result == {"rows": [1, 2, 3]} for result in results)
    assert dashboard_router._dashboard_cache_stats["waits"] >= 1
    assert dashboard_router._dashboard_cache_stats["hits"] == 7
    assert dashboard_router._dashboard_cache_stats["loads"] == 1


def test_realtime_version_cleanup_keeps_history_entries():
    _reset_cache()
    history_calls = 0

    def history_loader():
        nonlocal history_calls
        history_calls += 1
        return {"kind": "history"}

    dashboard_router._load_cached_response(
        namespace="history",
        resolved_key=("history-v1", "matrix"),
        ttl_seconds=60,
        loader=history_loader,
    )
    dashboard_router._load_cached_response(
        namespace="realtime",
        resolved_key=("realtime-v1", "matrix"),
        ttl_seconds=60,
        loader=lambda: {"kind": "realtime-v1"},
    )
    dashboard_router._load_cached_response(
        namespace="realtime",
        resolved_key=("realtime-v2", "matrix"),
        ttl_seconds=60,
        loader=lambda: {"kind": "realtime-v2"},
    )
    result = dashboard_router._load_cached_response(
        namespace="history",
        resolved_key=("history-v1", "matrix"),
        ttl_seconds=60,
        loader=history_loader,
    )

    assert result == {"kind": "history"}
    assert history_calls == 1
    assert all(
        key[0] != "realtime" or key[1] == "realtime-v2"
        for key in dashboard_router._dashboard_cache
    )


def test_cache_hit_moves_entry_to_lru_tail():
    _reset_cache()
    for key in ("a", "b"):
        dashboard_router._load_cached_response(
            namespace="history",
            resolved_key=(key,),
            ttl_seconds=60,
            loader=lambda key=key: {"key": key},
        )

    dashboard_router._load_cached_response(
        namespace="history",
        resolved_key=("a",),
        ttl_seconds=60,
        loader=lambda: {"key": "unexpected"},
    )

    assert list(dashboard_router._dashboard_cache)[-1] == ("history", "a")


def test_history_time_with_timezone_is_converted_to_shanghai():
    resolved = dashboard_router._history_local_time(
        datetime(2026, 6, 27, 18, 0, tzinfo=timezone.utc)
    )

    assert resolved == datetime(2026, 6, 28, 2, 0)


def test_failed_single_flight_is_briefly_shared():
    _reset_cache()
    calls = 0

    def failing_loader():
        nonlocal calls
        calls += 1
        sleep(0.05)
        raise ValueError("temporary database failure")

    def request():
        try:
            dashboard_router._load_cached_response(
                namespace="history",
                resolved_key=("failed",),
                ttl_seconds=60,
                loader=failing_loader,
            )
        except ValueError as exc:
            return str(exc)
        return "unexpected success"

    with ThreadPoolExecutor(max_workers=6) as executor:
        results = list(executor.map(lambda _: request(), range(6)))

    assert calls == 1
    assert results == ["temporary database failure"] * 6
    assert dashboard_router._dashboard_cache_stats["failures"] == 1
