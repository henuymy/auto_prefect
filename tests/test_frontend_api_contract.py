from pathlib import Path


API_CLIENT = Path("frontend/src/lib/api.ts")


def test_api_requests_have_a_bounded_timeout():
    source = API_CLIENT.read_text(encoding="utf-8")

    assert "const API_REQUEST_TIMEOUT_MS = 30_000;" in source
    assert "signal: AbortSignal.timeout(API_REQUEST_TIMEOUT_MS)" in source
    assert 'throw new Error("请求超时，请检查后端服务")' in source
