from pathlib import Path


COCKPIT = Path("frontend/src/dashboard/DashboardCockpit.tsx")


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
    cumulative_only_cache_as_of = '''asOf: dataTimeMode === "history"
      ? historyAsOf
      : dataTimeMode === "cumulative"
        ? cumulativeAsOf || null
        : null,'''
    normalized_source = "\n".join(line.lstrip() for line in source.splitlines())
    normalized_contract = "\n".join(line.lstrip() for line in cumulative_only_cache_as_of.splitlines())
    assert normalized_source.count(normalized_contract) == 2


def test_cumulative_mode_uses_lazy_available_date_selector():
    source = COCKPIT.read_text(encoding="utf-8")
    assert "getDashboardAccOptions" in source
    assert "cumulativeDateOptions" in source
    assert 'aria-label="累计日期"' in source
    assert "onCumulativeLoadMore" in source
    assert "加载更多日期" in source
    assert 'onCumulativeQuery' in source
