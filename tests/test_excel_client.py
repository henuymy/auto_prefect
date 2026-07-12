import inspect
import json

import pytest

from infrastructure import excel_client
from infrastructure.excel_client import open_excel


def test_open_excel_does_not_cleanup_orphaned_processes_by_default():
    signature = inspect.signature(open_excel)

    assert signature.parameters["cleanup_orphaned"].default is False


def test_excel_lock_does_not_evict_live_owner(monkeypatch, tmp_path):
    lock_path = tmp_path / "excel.lock"
    lock_path.write_text(
        json.dumps(
            {"pid": 321, "process_started_at": "2026-07-12T10:00:00+08:00"}
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        excel_client, "process_is_running", lambda pid, started_at=None: True
    )

    assert excel_client.FileLock(lock_path, stale_seconds=1).is_stale() is False


def test_excel_lock_evicts_dead_owner(monkeypatch, tmp_path):
    lock_path = tmp_path / "excel.lock"
    lock_path.write_text(json.dumps({"pid": 321}), encoding="utf-8")
    monkeypatch.setattr(
        excel_client, "process_is_running", lambda pid, started_at=None: False
    )

    assert excel_client.FileLock(lock_path, stale_seconds=9999).is_stale() is True


def test_excel_lock_payload_records_process_identity(monkeypatch, tmp_path):
    lock_path = tmp_path / "excel.lock"
    monkeypatch.setattr(excel_client, "_current_pid", lambda: 321)
    monkeypatch.setattr(
        excel_client,
        "current_process_started_at",
        lambda: "2026-07-12T02:00:00+00:00",
    )

    lock = excel_client.FileLock(lock_path).acquire()
    try:
        payload = json.loads(lock_path.read_text(encoding="utf-8"))
        assert payload["pid"] == 321
        assert payload["process_started_at"] == "2026-07-12T02:00:00+00:00"
        assert payload["owner_token"]
        assert payload["acquired_at"]
    finally:
        lock.release()


def test_excel_lock_default_path_tracks_runtime_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTO_NOTIFY_RUNTIME_ROOT", str(tmp_path / "first"))
    assert excel_client.FileLock().path == (
        tmp_path / "first" / "locks" / "excel_com.lock"
    ).resolve()

    monkeypatch.setenv("AUTO_NOTIFY_RUNTIME_ROOT", str(tmp_path / "second"))
    assert excel_client.FileLock().path == (
        tmp_path / "second" / "locks" / "excel_com.lock"
    ).resolve()


def test_process_identity_rejects_reused_pid(monkeypatch):
    completed = type(
        "Completed",
        (),
        {
            "returncode": 0,
            "stdout": "2026-07-12T02:00:00.0000000Z\n",
        },
    )()
    monkeypatch.setattr(excel_client.sys, "platform", "win32")
    monkeypatch.setattr(excel_client.subprocess, "run", lambda *_args, **_kwargs: completed)

    assert (
        excel_client.process_is_running(
            321, started_at="2026-07-12T10:00:00+08:00"
        )
        is True
    )
    assert (
        excel_client.process_is_running(
            321, started_at="2026-07-12T10:00:01+08:00"
        )
        is False
    )


def test_excel_lock_release_preserves_replacement_owner(monkeypatch, tmp_path):
    lock_path = tmp_path / "excel.lock"
    monkeypatch.setattr(excel_client, "current_process_started_at", lambda: "start")
    lock = excel_client.FileLock(lock_path).acquire()
    lock_path.write_text(
        json.dumps({"pid": 999, "owner_token": "replacement"}), encoding="utf-8"
    )

    lock.release()

    assert json.loads(lock_path.read_text(encoding="utf-8"))["owner_token"] == "replacement"


def test_excel_lock_acquire_failure_removes_partial_lock(monkeypatch, tmp_path):
    lock_path = tmp_path / "excel.lock"
    monkeypatch.setattr(
        excel_client,
        "current_process_started_at",
        lambda: (_ for _ in ()).throw(RuntimeError("start time unavailable")),
    )

    with pytest.raises(RuntimeError, match="start time unavailable"):
        excel_client.FileLock(lock_path).acquire()

    assert lock_path.exists() is False
