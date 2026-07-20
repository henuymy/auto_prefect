from backend.services.monitor_api_schema import to_frontend_pending_queue, to_frontend_run


def test_api_schema_only_exposes_safe_monitor_details():
    run = {
        "id": "run-1", "source": "prefect", "task_name": "通报 · 经营日报", "status": "failed",
        "target_kind": "report", "target_id": "report-deployment-1",
        "scheduled_at": "2026-07-18T09:00:00+08:00", "started_at": "2026-07-18T09:00:01+08:00",
        "finished_at": "2026-07-18T09:00:10+08:00", "current_step": "登录认证",
        "business_error_summary": "登录状态失效", "technical_error_summary": "认证状态校验失败",
        "steps": [{"name": "登录认证", "status": "failed", "message": "已隐藏"}],
        "logs": [{"at": "2026-07-18T09:00:10+08:00", "level": "ERROR", "message": "登录状态失效", "details": "secret"}],
    }

    payload = to_frontend_run(run)

    assert payload["durationSeconds"] == 9
    assert payload["targetId"] == "report-deployment-1"
    assert payload["steps"][0]["status"] == "failed"
    assert payload["logs"][0] == {"at": "2026-07-18T09:00:10+08:00", "level": "ERROR", "message": "登录状态失效"}
    assert "details" not in payload["logs"][0]


def test_pending_queue_is_individual_runs_in_scheduled_time_order():
    queue = to_frontend_pending_queue([
        {"id": "later", "source": "prefect", "task_name": "B", "status": "scheduled", "scheduled_at": "2026-07-19T09:00:00+08:00"},
        {"id": "earlier", "source": "prefect", "task_name": "A", "status": "scheduled", "scheduled_at": "2026-07-18T09:00:00+08:00"},
    ])

    assert queue["total"] == 2
    assert [item["id"] for item in queue["items"]] == ["earlier", "later"]
    assert queue["items"][0]["nextStep"] == "尚未开始"


def test_api_schema_marks_prefect_detail_unavailable_without_losing_the_summary():
    payload = to_frontend_run({
        "id": "run-1",
        "source": "prefect",
        "task_name": "通报 · 经营日报",
        "status": "failed",
        "current_step": "运行失败",
        "business_error_summary": "Prefect 连接异常前已同步的失败摘要",
        "detail_available": False,
        "detail_message": "Prefect 详情暂不可用，正在显示已同步摘要。",
    })

    assert payload["detailAvailable"] is False
    assert payload["detailMessage"] == "Prefect 详情暂不可用，正在显示已同步摘要。"
    assert payload["error"]["businessSummary"] == "Prefect 连接异常前已同步的失败摘要"
