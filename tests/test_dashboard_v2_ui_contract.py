from pathlib import Path


COCKPIT = Path("frontend/src/dashboard/DashboardCockpit.tsx")


def test_history_time_copy_distinguishes_batch_start_and_finish():
    source = COCKPIT.read_text(encoding="utf-8")

    assert "采集档位（批次开始时间）" in source
    assert "匹配批次开始时间" in source
    assert "批次完成时间" in source
    assert "change_tolerance_minutes" in source


def test_history_mode_only_lists_currently_enabled_indicators():
    source = COCKPIT.read_text(encoding="utf-8")

    assert "getDashboardIndicators(false, true)" in source
    assert 'item.storage_mode === "STORE"\n        && item.enabled' in source
    assert 'dataTimeMode === "history" || item.enabled' not in source


def test_cumulative_mode_queries_selected_daily_accumulation_date():
    source = COCKPIT.read_text(encoding="utf-8")
    assert 'const [cumulativeAsOf, setCumulativeAsOf] = useState("");' in source
    assert 'const [cumulativeInput, setCumulativeInput] = useState("");' in source
    assert '"DAY_ACC",\n            cumulativeAsOf || undefined,' in source
    assert 'targetScenario,\n            "MONTH",\n            "ASSESSMENT",' in source
    assert 'targetScenario,\n                  "MONTH",\n                  "WORKING",' in source
    assert 'cumulativeValue={cumulativeInput}' in source
    cumulative_only_cache_as_of = '''asOf: dataTimeMode === "history"
      ? historyAsOf
      : dataTimeMode === "cumulative"
        ? cumulativeAsOf || null
        : null,'''
    normalized_source = "\n".join(line.lstrip() for line in source.splitlines())
    normalized_contract = "\n".join(line.lstrip() for line in cumulative_only_cache_as_of.splitlines())
    assert normalized_source.count(normalized_contract) == 2


def test_cockpit_labels_stored_accumulation_as_a_baseline():
    source = COCKPIT.read_text(encoding="utf-8")

    assert "累计基线" in source
    assert "截至最近已完成采集日的 DAY_ACC，不含当天实时" in source
    assert "<strong>当月累计</strong><span>前一日期累计 + 当日实时</span>" not in source


def test_cumulative_mode_uses_lazy_available_date_selector():
    source = COCKPIT.read_text(encoding="utf-8")
    assert "getDashboardAccOptions" in source
    assert "cumulativeDateOptions" in source
    assert 'aria-label="累计日期"' in source
    assert "onCumulativeLoadMore" in source
    assert "加载更多日期" in source
    assert 'onCumulativeQuery' in source
    assert "void loadCumulativeDateOptions(true);" in source
    assert "await loadCumulativeDateOptions(true);" in source


def test_realtime_first_paint_defers_history_availability_requests():
    source = COCKPIT.read_text(encoding="utf-8")

    assert "if (dataTimeMode !== \"history\") return;" in source
    assert "void loadHistoryAvailability();" in source


def test_first_single_indicator_reuses_catalog_before_dashboard_query():
    source = COCKPIT.read_text(encoding="utf-8")

    assert "initialIndicatorCatalogRequestRef" in source
    assert "setInitialIndicatorReady(true);" in source
    assert "initialCatalogPromise ?? getDashboardIndicators(false, true)" in source
    assert "const core = await getDashboardStagedValues(stageRequest(\"CORE\"));" in source
    assert source.index("const core = await getDashboardStagedValues(stageRequest(\"CORE\"));") < source.index(
        "let catalog = initialCatalog ?? [];"
    )


def test_target_editor_only_edits_execution_plans_and_keeps_audit_separate():
    source = COCKPIT.read_text(encoding="utf-8")

    assert 'const [view, setView] = useState<"EXECUTION" | "AUDIT">("EXECUTION");' in source
    assert 'const executionPlans = useMemo(' in source
    assert 'plan.status === "DRAFT"' in source
    assert '保存草稿' in source
    assert 'setDashboardTargetPlanRealtime' in source
    assert '设为实时目标' in source
    assert '发布为考核版本' in source
    assert 'className="target-audit-view"' in source
    assert '考核版本记录' in source
    assert '以此版本创建草稿' in source
    assert '执行方案' not in source
    assert '复制为草稿并编辑' not in source


def test_custom_formula_keeps_source_storage_role_outside_formula_rows():
    source = COCKPIT.read_text(encoding="utf-8")
    css = Path("frontend/src/dashboard/dashboard-cockpit.css").read_text(encoding="utf-8")
    manager = source[
        source.index("function CustomIndicatorManager"):
        source.index("function SourceMetricPicker")
    ]

    assert "custom-component-dependency" in source
    assert "custom-component-columns" in source
    assert "custom-metric-editor-footer" in source
    assert "公式预览" in source
    assert 'return storageMode === "STORE" ? "结果落库" : "仅计算输入";' in source
    assert 'label: "可用"' in source
    assert "被公式引用" in source
    assert "独立展示的源指标必须使用结果落库" in source
    assert '<option value="COMPONENT" disabled={indicator.enabled}>仅计算输入</option>' in source
    assert "公式组件" not in source
    assert "source_storage_mode" not in source
    assert "source-manager-columns" in source
    assert "grid-template-columns: max-content minmax(0, 1fr);" in css
    assert "overflow-wrap: anywhere;" in css
    assert "const [customMessage, setCustomMessage]" in manager
    assert "const [sourceMessage, setSourceMessage]" in manager
    assert "{sourceMessage &&" in manager
    assert "{customMessage &&" in manager
    assert "setMessage(" not in manager
