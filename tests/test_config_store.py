from __future__ import annotations

import json

import pytest

from backend.services import config_store


def _write(path, name, enabled=True):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "name": name,
                "template_path": f"templates/{name}.xlsx",
                "enabled": enabled,
                "downloads": [],
                "compare_sources": [],
                "send": {"webhook_url": "", "workbook_name": name, "items": []},
                "template_update": {"update_condition": "any_changed", "write_sheets": "all_compared", "send_when_same": True},
                "wait_for_change": {"enabled": False, "poll_interval_seconds": 300, "max_wait_minutes": 180},
                "deployment": {"enabled": True, "cron": "0 9 * * *", "timezone": "Asia/Shanghai"},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def _patch_dirs(monkeypatch, tmp_path):
    monkeypatch.setattr(config_store, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(config_store, "REPORTS_DIR", tmp_path / "config" / "reports")
    monkeypatch.setattr(config_store, "TASKS_DIR", tmp_path / "config" / "tasks")
    monkeypatch.setattr(config_store, "DRAFTS_DIR", tmp_path / "runtime" / "drafts")
    monkeypatch.setattr(config_store, "VERSIONS_DIR", tmp_path / "runtime" / "config_versions")


def test_list_configs_reads_published_only(monkeypatch, tmp_path):
    _patch_dirs(monkeypatch, tmp_path)
    _write(config_store.REPORTS_DIR / "正式.json", "正式")

    configs = config_store.list_configs()

    assert [(item["id"], item["source"], item["has_draft"]) for item in configs] == [("正式", "published", False)]


def test_list_configs_reads_draft_only(monkeypatch, tmp_path):
    _patch_dirs(monkeypatch, tmp_path)
    _write(config_store.DRAFTS_DIR / "草稿.json", "草稿")

    configs = config_store.list_configs()

    assert [(item["id"], item["source"], item["has_draft"]) for item in configs] == [("草稿", "draft", True)]


def test_list_configs_marks_published_with_draft(monkeypatch, tmp_path):
    _patch_dirs(monkeypatch, tmp_path)
    _write(config_store.REPORTS_DIR / "同名.json", "同名")
    _write(config_store.DRAFTS_DIR / "同名.json", "同名", enabled=False)

    configs = config_store.list_configs()

    assert len(configs) == 1
    assert configs[0]["source"] == "published"
    assert configs[0]["has_draft"] is True


def test_save_config_removes_same_name_draft(monkeypatch, tmp_path):
    _patch_dirs(monkeypatch, tmp_path)
    _write(config_store.DRAFTS_DIR / "同名.json", "同名")

    saved = config_store.save_config("同名", {"name": "同名", "template_path": "templates/a.xlsx"})

    assert saved["source"] == "published"
    assert saved["has_draft"] is False
    assert (config_store.REPORTS_DIR / "同名.json").exists()
    assert not (config_store.DRAFTS_DIR / "同名.json").exists()


def test_create_config_rejects_duplicate_name(monkeypatch, tmp_path):
    _patch_dirs(monkeypatch, tmp_path)
    _write(config_store.REPORTS_DIR / "日报.json", "日报")

    with pytest.raises(FileExistsError, match="配置已存在: 日报"):
        config_store.create_config({"name": "日报", "template_path": "templates/a.xlsx"})


def test_create_config_saves_new_name(monkeypatch, tmp_path):
    _patch_dirs(monkeypatch, tmp_path)

    saved = config_store.create_config({"name": "日报-副本", "template_path": "templates/a.xlsx", "enabled": False})

    assert saved["id"] == "日报-副本"
    assert saved["enabled"] is False
    assert (config_store.REPORTS_DIR / "日报-副本.json").exists()


def test_normalize_config_fills_compare_defaults():
    normalized = config_store.normalize_config(
        {
            "name": "日报",
            "template_path": "templates/a.xlsx",
            "downloads": [{"name": "下载"}],
            "compare_sources": [{"download_name": "下载", "sheet_mappings": []}],
        }
    )

    source = normalized["compare_sources"][0]
    assert source["engine"] == "openpyxl"
    assert source["max_workers"] == 4
