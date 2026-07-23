from backend.services.prefect_monitor_failure import classify_prefect_failure


def test_prefect_failure_limits_long_summaries_after_sanitizing() -> None:
    failure = classify_prefect_failure("token=secret-value " + ("x" * 100_000))

    assert failure.business_summary is not None
    assert failure.technical_summary is not None
    assert len(failure.business_summary) <= 500
    assert len(failure.technical_summary) <= 8_000
    assert "secret-value" not in failure.business_summary
    assert "secret-value" not in failure.technical_summary
    assert "已截断" in failure.technical_summary
