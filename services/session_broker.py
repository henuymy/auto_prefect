"""Stage-oriented compatibility layer over the existing session manager."""

from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from services.session_manager import file_lock, load_cookie_snapshot_if_exists, prepare_session, resolve_path


class StageSessionBroker:
    """Prepare requested stages and persist their health independently."""

    def __init__(self, preparer=prepare_session, *, base_dir: Path | str):
        self.preparer = preparer
        self.base_dir = Path(base_dir)

    def ensure(self, config: dict, **kwargs) -> dict:
        required_stages = [str(stage) for stage in config.get("required_stages") or []]
        stage_dir = self._resolve(config.get("stage_session_dir", "stages"))
        health_path = self._resolve(config.get("stage_health_path", "stage_health.json"))
        legacy_cookie_dump_path = self._resolve(
            config.get("cookie_dump_path", "runtime/session/cookie_dump.json")
        )
        broker_lock_path = self._resolve(
            config.get("stage_broker_lock_path", str(stage_dir / ".broker.lock"))
        )
        with file_lock(broker_lock_path, lock_label="Stage SessionBroker 锁"):
            cookie_dump_path = stage_dir / f".broker-{uuid4().hex}.json"
            health = self._read_json(health_path, default={})
            self._rebuild_compatibility_snapshot(cookie_dump_path, stage_dir, health)
            broker_config = deepcopy(config)
            broker_config["cookie_dump_path"] = str(cookie_dump_path)
            broker_config["legacy_cookie_dump_path"] = None
            try:
                result = self.preparer(broker_config, base_dir=self.base_dir, **kwargs)
                if result.get("status") not in {"reused", "reused_after_lock", "refreshed"}:
                    return result

                cookie_dump, _ = load_cookie_snapshot_if_exists(cookie_dump_path)
                if not cookie_dump:
                    raise RuntimeError("SessionBroker 未找到已验证的 Cookie 快照")

                captured_at = datetime.now().astimezone().isoformat()
                stages_by_name = {
                    str(item.get("stage")): item
                    for item in cookie_dump.get("stages") or []
                    if isinstance(item, dict) and item.get("stage")
                }

                if result["status"] == "refreshed":
                    for stage in stages_by_name:
                        if stage not in required_stages:
                            health[stage] = {"status": "unknown", "updated_at": captured_at}

                for stage in required_stages:
                    stage_data = stages_by_name.get(stage)
                    if stage_data is None:
                        raise RuntimeError(f"SessionBroker 缺少已验证 stage={stage}")
                    self._write_json(stage_dir / f"{stage}.json", {"stage": stage, "data": stage_data})
                    health[stage] = {
                        "status": "healthy",
                        "last_probe_at": captured_at,
                        "last_refresh_at": captured_at if result["status"] == "refreshed" else health.get(stage, {}).get("last_refresh_at"),
                    }

                self._write_json(health_path, health)
                legacy_cookie_dump_path.unlink(missing_ok=True)
                return {
                    **result,
                    "cookie_dump_path": None,
                    "stage_health_path": str(health_path),
                    "stages": required_stages,
                    "stage_data": {stage: stages_by_name[stage] for stage in required_stages},
                }
            finally:
                cookie_dump_path.unlink(missing_ok=True)

    def _rebuild_compatibility_snapshot(self, cookie_dump_path: Path, stage_dir: Path, health: dict) -> None:
        stages = []
        for stage_file in sorted(stage_dir.glob("*.json")) if stage_dir.exists() else []:
            stage_snapshot = self._read_json(stage_file, default={})
            stage_name = str(stage_snapshot.get("stage") or "")
            stage_data = stage_snapshot.get("data")
            if not stage_name or not isinstance(stage_data, dict):
                continue
            if health.get(stage_name, {}).get("status") == "healthy":
                stages.append(stage_data)
        self._write_json(cookie_dump_path, {"stages": stages})

    def _resolve(self, value: str) -> Path:
        path = Path(value)
        if path.parts and path.parts[0].lower() == "runtime":
            return resolve_path(value, self.base_dir)
        return path if path.is_absolute() else self.base_dir / path

    @staticmethod
    def _read_json(path: Path, *, default):
        if not path.exists():
            return default
        return json.loads(path.read_text(encoding="utf-8"))

    @staticmethod
    def _write_json(path: Path, payload) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        try:
            temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
