from __future__ import annotations

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]

EXPECTED_SYSTEM_SCHEDULES = {
    "dashboard-collection": (("*/5 * * * *", "Asia/Shanghai", False),),
    "dashboard-daily-acc": (),
    "dashboard-indicator-sync": (("10 8 * * *", "Asia/Shanghai", True),),
    "dashboard-monthly": (),
    "dashboard-v2-partition-maintenance": (("20 2 * * *", "Asia/Shanghai", True),),
    "session-keeper": (("*/10 * * * *", "Asia/Shanghai", False),),
}

EXPECTED_NOTIFY_SCHEDULES = {
    "notify-PK赛通报": (
        ("0 9-13 * * *", "Asia/Shanghai", False),
        ("0,30 14-15 * * *", "Asia/Shanghai", False),
        ("*/10 16 * * *", "Asia/Shanghai", False),
        ("0,30 17-18 * * *", "Asia/Shanghai", False),
        ("0 19 * * *", "Asia/Shanghai", False),
    ),
    "notify-升档市公司通报": (),
    "notify-家客和存量通报": (("0 11,15,17,18 * * *", "Asia/Shanghai", False),),
    "notify-日常进度": (
        ("0 9-13 * * *", "Asia/Shanghai", False),
        ("0,30 14-15 * * *", "Asia/Shanghai", False),
        ("*/10 16 * * *", "Asia/Shanghai", False),
        ("0,30 17-18 * * *", "Asia/Shanghai", False),
        ("0 19 * * *", "Asia/Shanghai", False),
    ),
    "notify-流量市公司通报": (),
    "notify-爱家亲情网区公司通报": (("0 16 * * *", "Asia/Shanghai", False),),
    "notify-爱家亲情网市公司通报": (("0 14 * * *", "Asia/Shanghai", False),),
    "notify-爱家亲情网每日盯控": (("0 9-18 * * *", "Asia/Shanghai", False),),
    "notify-爱家亲情网每日盯控-网格-渠道经理": (("0 9-18 * * *", "Asia/Shanghai", False),),
    "notify-爱家亲情网营业厅每日盯控": (("0 9-18 * * *", "Asia/Shanghai", False),),
    "notify-离网市公司通报": (),
    "notify-网格区公司PK赛通报": (("23 9-19 * * *", "Asia/Shanghai", False),),
    "notify-降档市公司通报": (),
}


def deployment_schedules(deployment: dict) -> tuple[tuple[str, str, bool], ...]:
    return tuple(
        (schedule["cron"], schedule["timezone"], schedule["active"])
        for schedule in deployment.get("schedules", [])
    )


def test_prefect_manifest_declares_all_remote_deployments() -> None:
    payload = yaml.safe_load((ROOT / "prefect.yaml").read_text(encoding="utf-8"))
    deployments = {item["name"]: item for item in payload["deployments"]}

    assert set(deployments) == {*EXPECTED_SYSTEM_SCHEDULES, *EXPECTED_NOTIFY_SCHEDULES}
    for name, expected_schedules in EXPECTED_SYSTEM_SCHEDULES.items():
        assert deployment_schedules(deployments[name]) == expected_schedules


def test_prefect_manifest_uses_relative_paths_for_all_notify_deployments() -> None:
    payload = yaml.safe_load((ROOT / "prefect.yaml").read_text(encoding="utf-8"))
    deployments = {item["name"]: item for item in payload["deployments"]}

    for name, expected_schedules in EXPECTED_NOTIFY_SCHEDULES.items():
        deployment = deployments[name]
        report_name = name.removeprefix("notify-")
        assert deployment["entrypoint"] == "flows/notify_single_flow.py:auto_notify_flow"
        assert deployment["work_pool"] == {
            "name": "windows-notify-pool",
            "work_queue_name": "default",
        }
        assert deployment["parameters"] == {"config_path": f"config/tasks/{report_name}.json"}
        assert not Path(deployment["parameters"]["config_path"]).is_absolute()
        assert deployment_schedules(deployment) == expected_schedules
