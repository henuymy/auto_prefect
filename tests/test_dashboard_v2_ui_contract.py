from pathlib import Path


COCKPIT = Path("admin_react/src/dashboard/DashboardCockpit.tsx")


def test_history_time_copy_distinguishes_batch_start_and_finish():
    source = COCKPIT.read_text(encoding="utf-8")

    assert "采集档位（批次开始时间）" in source
    assert "匹配批次开始时间" in source
    assert "批次完成时间" in source
    assert "change_tolerance_minutes" in source


def test_cumulative_mode_queries_selected_daily_accumulation_date():
    source = COCKPIT.read_text(encoding="utf-8")
    assert 'const [cumulativeAsOf, setCumulativeAsOf] = useState("");' in source
    assert 'const [cumulativeInput, setCumulativeInput] = useState("");' in source
    assert '"DAY_ACC",\n            cumulativeAsOf || undefined,' in source
    assert 'cumulativeValue={cumulativeInput}' in source
    assert 'asOf: dataTimeMode === "history" ? historyAsOf : cumulativeAsOf || null,' in source
