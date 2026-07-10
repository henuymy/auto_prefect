from pathlib import Path


COCKPIT = Path("admin_react/src/dashboard/DashboardCockpit.tsx")


def test_history_time_copy_distinguishes_batch_start_and_finish():
    source = COCKPIT.read_text(encoding="utf-8")

    assert "采集档位（批次开始时间）" in source
    assert "匹配批次开始时间" in source
    assert "批次完成时间" in source
    assert "change_tolerance_minutes" in source
