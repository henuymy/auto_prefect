"""Utilities for writing staged Selenium cookies to JSON."""

import json
from datetime import datetime
from pathlib import Path


class CookieRecorder:
    def __init__(self, output_path, config_path=None):
        self.output_path = Path(output_path)
        self.config_path = str(config_path) if config_path else None
        self.stages = []

    def capture(self, driver, stage_name):
        stage = {
            "stage": stage_name,
            "captured_at": datetime.now().isoformat(),
            "url": driver.current_url,
            "title": driver.title,
            "cookies": driver.get_cookies(),
        }
        for index, existing_stage in enumerate(self.stages):
            if existing_stage.get("stage") == stage_name:
                self.stages[index] = stage
                break
        else:
            self.stages.append(stage)
        self.save()
        print(f"[INFO] 已写入 {stage_name} Cookie: {self.output_path}")
        return stage

    def save(self):
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "generated_at": datetime.now().isoformat(),
            "config_path": self.config_path,
            "stages": self.stages,
        }
        with self.output_path.open("w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)


def resolve_cookie_dump_path(base_dir, config):
    cookie_dump_config = config.get("cookie_dump", {})
    output_file = cookie_dump_config.get("output_file", "runtime/cookie_dump.json")
    output_path = Path(output_file)
    if not output_path.is_absolute():
        output_path = Path(base_dir) / output_path
    return output_path
