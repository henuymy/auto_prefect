from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor

import pytest

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
    monkeypatch.setattr(config_store, "CONFIG_ORDER_PATH", tmp_path / "config" / "report_order.json")


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


def test_list_configs_uses_saved_order(monkeypatch, tmp_path):
    _patch_dirs(monkeypatch, tmp_path)
    _write(config_store.REPORTS_DIR / "甲.json", "甲")
    _write(config_store.REPORTS_DIR / "乙.json", "乙")
    _write(config_store.REPORTS_DIR / "丙.json", "丙")

    saved = config_store.save_config_order(["丙", "甲", "乙"])
    configs = config_store.list_configs()

    assert saved == ["丙", "甲", "乙"]
    assert [item["id"] for item in configs] == ["丙", "甲", "乙"]


def test_config_order_ignores_unknown_and_duplicate_ids(monkeypatch, tmp_path):
    _patch_dirs(monkeypatch, tmp_path)
    _write(config_store.REPORTS_DIR / "甲.json", "甲")

    saved = config_store.save_config_order(["不存在", "甲", "甲"])

    assert saved == ["甲"]
    assert json.loads(config_store.CONFIG_ORDER_PATH.read_text(encoding="utf-8")) == {"ids": ["甲"]}


def test_rename_and_delete_maintain_saved_order(monkeypatch, tmp_path):
    _patch_dirs(monkeypatch, tmp_path)
    _write(config_store.REPORTS_DIR / "甲.json", "甲")
    _write(config_store.REPORTS_DIR / "乙.json", "乙")
    config_store.save_config_order(["甲", "乙"])

    config_store.save_config("甲", {"name": "新甲", "template_path": "templates/a.xlsx"})
    assert config_store._read_config_order() == ["新甲", "乙"]

    config_store.delete_config("新甲")
    assert config_store._read_config_order() == ["乙"]


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


def test_atomic_write_preserves_previous_file_when_replace_fails(
    monkeypatch, tmp_path
):
    path = tmp_path / "config.json"
    path.write_text('{"value": "old"}', encoding="utf-8")
    monkeypatch.setattr(
        config_store.os,
        "replace",
        lambda *_args: (_ for _ in ()).throw(OSError("replace failed")),
    )

    with pytest.raises(OSError, match="replace failed"):
        config_store._write_json(path, {"value": "new"})

    assert json.loads(path.read_text(encoding="utf-8")) == {"value": "old"}
    assert list(tmp_path.glob("*.tmp")) == []


def test_concurrent_config_saves_never_produce_partial_json(monkeypatch, tmp_path):
    _patch_dirs(monkeypatch, tmp_path)

    def save(index: int):
        return config_store.save_config(
            "并发配置",
            {"name": "并发配置", "description": f"version-{index}"},
        )

    with ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(save, range(8)))

    path = config_store.REPORTS_DIR / "并发配置.json"
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["description"].startswith("version-")
    assert list(path.parent.glob("*.tmp")) == []
