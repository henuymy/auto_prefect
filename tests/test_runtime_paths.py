from pathlib import Path

from services.runtime_paths import resolve_runtime_path, runtime_path, runtime_root


def test_runtime_relative_paths_rebase_to_external_root(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTO_NOTIFY_RUNTIME_ROOT", str(tmp_path / "shared"))

    assert resolve_runtime_path(
        "runtime/cookies/cookie_dump.json", project_dir=tmp_path
    ) == (tmp_path / "shared" / "cookies" / "cookie_dump.json").resolve()


def test_non_runtime_relative_paths_remain_project_relative(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTO_NOTIFY_RUNTIME_ROOT", str(tmp_path / "shared"))

    assert resolve_runtime_path("config/tasks/demo.json", project_dir=tmp_path) == (
        tmp_path / "config" / "tasks" / "demo.json"
    ).resolve()


def test_absolute_paths_are_preserved(monkeypatch, tmp_path):
    target = (tmp_path / "absolute.json").resolve()
    monkeypatch.setenv("AUTO_NOTIFY_RUNTIME_ROOT", str(tmp_path / "shared"))

    assert resolve_runtime_path(target, project_dir=tmp_path) == target


def test_runtime_root_uses_project_runtime_without_override(monkeypatch):
    monkeypatch.delenv("AUTO_NOTIFY_RUNTIME_ROOT", raising=False)

    assert runtime_root() == (Path(__file__).resolve().parents[1] / "runtime").resolve()


def test_runtime_path_is_resolved_lazily(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTO_NOTIFY_RUNTIME_ROOT", str(tmp_path / "first"))
    assert runtime_path("locks/excel.lock") == (
        tmp_path / "first" / "locks" / "excel.lock"
    ).resolve()

    monkeypatch.setenv("AUTO_NOTIFY_RUNTIME_ROOT", str(tmp_path / "second"))
    assert runtime_path("locks/excel.lock") == (
        tmp_path / "second" / "locks" / "excel.lock"
    ).resolve()
