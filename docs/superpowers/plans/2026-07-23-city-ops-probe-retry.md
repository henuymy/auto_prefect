# City Ops 探活重试实施计划

> **给自动化执行者：** 必须使用 `superpowers:subagent-driven-development`（推荐）或 `superpowers:executing-plans` 按任务执行。步骤使用复选框追踪。

**目标：** `city_ops` 探活在瞬态网络异常后重试一次，并以连接 2 秒、读取 5 秒为请求超时限制。

**架构：** 在 `execute_stage_probe` 中封装单次 `requests.Session.request` 调用。仅 `requests.exceptions.RequestException` 进入一次 0.5 秒退避重试；任何已获得的 HTTP 响应保留原有状态码和认证失效判断。最终网络异常只暴露异常类名，不保留原始错误文本。

**技术栈：** Python 3.13、requests、pytest、monkeypatch。

## 全局约束

- 每次探活最多两次 HTTP 请求：首次请求和一次重试。
- 请求使用 `timeout=(2, 5)`，分别表示连接和读取超时。
- 仅 `requests.exceptions.RequestException` 可重试；HTTP 302、401、403 及其他响应不重试。
- 重试前等待 0.5 秒。
- 诊断仅保存异常类名；不得保存 Cookie、Token、请求头、请求体或原始异常内容。

---

### 任务 1：为探活重试建立回归测试

**文件：**
- 修改：`tests/test_session_manager.py`
- 测试：`tests/test_session_manager.py`

**接口：**
- 使用：`services.session_manager.execute_stage_probe(stage_name, probe, stage)`。
- 产出：证明请求异常时最多重试一次、成功响应不重试、诊断已脱敏的测试。

- [ ] **步骤 1：编写瞬态异常的失败测试**

```python
def test_execute_stage_probe_retries_one_transient_request_failure(monkeypatch):
    calls = []

    def fake_request(self, method, url, **kwargs):
        calls.append((method, url, kwargs))
        if len(calls) == 1:
            raise session_manager.requests.exceptions.ReadTimeout("secret detail")
        return FakeResponse(status_code=200, payload={"reCode": "0"})

    monkeypatch.setattr(session_manager.requests.Session, "request", fake_request)
    monkeypatch.setattr(session_manager.time, "sleep", lambda seconds: None)

    result = session_manager.execute_stage_probe(
        "city_ops",
        {"method": "POST", "url": "https://example/getUserInfo", "success_json_path": "reCode", "success_value": "0"},
        valid_city_ops_cookie_dump()["stages"][0],
    )

    assert result["ok"] is True
    assert len(calls) == 2
    assert calls[0][2]["timeout"] == (2, 5)
```

- [ ] **步骤 2：运行测试，确认失败**

运行：`pytest tests/test_session_manager.py::test_execute_stage_probe_retries_one_transient_request_failure -v`

预期：失败，因为现有实现第一次 `ReadTimeout` 会直接抛出，并且仍传入单个整数超时值。

- [ ] **步骤 3：编写持续异常和 HTTP 认证响应的失败测试**

```python
def test_execute_stage_probe_reports_safe_error_type_after_one_retry(monkeypatch):
    calls = []

    def fake_request(self, *_args, **_kwargs):
        calls.append(True)
        raise session_manager.requests.exceptions.ConnectTimeout("token=secret")

    monkeypatch.setattr(session_manager.requests.Session, "request", fake_request)
    monkeypatch.setattr(session_manager.time, "sleep", lambda seconds: None)

    result = session_manager.validate_stage_probes(
        valid_city_ops_cookie_dump(),
        ["city_ops"],
        {"city_ops": {"method": "POST", "url": "https://example/getUserInfo"}},
    )

    assert len(calls) == 2
    assert result["results"][0]["error"] == "ConnectTimeout"
    assert "secret" not in repr(result)
```

运行：`pytest tests/test_session_manager.py::test_execute_stage_probe_reports_safe_error_type_after_one_retry -v`

预期：失败，因为当前探活只请求一次，并保存完整的异常字符串。

- [ ] **步骤 4：编写 HTTP 认证响应不重试的失败测试**

```python
def test_execute_stage_probe_does_not_retry_authentication_response(monkeypatch):
    calls = []

    def fake_request(self, *_args, **_kwargs):
        calls.append(True)
        return FakeResponse(status_code=401, payload={"reCode": "401"})

    monkeypatch.setattr(session_manager.requests.Session, "request", fake_request)

    result = session_manager.execute_stage_probe(
        "city_ops",
        {"method": "POST", "url": "https://example/getUserInfo"},
        valid_city_ops_cookie_dump()["stages"][0],
    )

    assert result["reason"] == "session_expired"
    assert len(calls) == 1
```

运行：`pytest tests/test_session_manager.py::test_execute_stage_probe_does_not_retry_authentication_response -v`

预期：通过，证明实现不会将已返回的 HTTP 认证响应纳入网络重试路径。

- [ ] **步骤 5：实现最小探活重试逻辑**

```python
def execute_stage_probe(stage_name, probe, stage):
    session = requests.Session()
    session.trust_env = bool(probe.get("trust_env", False))
    session.cookies.update(build_cookie_jar(stage, cookie_names=probe.get("cookie_names")))
    session.headers.update({"User-Agent": "session-probe/1.0"})
    method = str(probe.get("method") or "GET").upper()
    request_kwargs = build_probe_request_kwargs(probe, stage)

    for attempt in range(2):
        try:
            response = session.request(method, probe["url"], **request_kwargs)
            break
        except requests.exceptions.RequestException as exc:
            if attempt == 1:
                return {
                    "stage": stage_name,
                    "enabled": True,
                    "ok": False,
                    "reason": "probe_error",
                    "error": type(exc).__name__,
                }
            time.sleep(0.5)

    payload = response_json_or_none(response)
    status_codes = probe.get("success_status_codes") or list(range(200, 300))
    status_ok = response.status_code in {int(code) for code in status_codes}
    expired = response_has_session_expired(response, payload=payload)
    result = {
        "stage": stage_name,
        "enabled": True,
        "url": response.url,
        "status_code": response.status_code,
        "ok": False,
    }
    if expired:
        result["reason"] = "session_expired"
        return result
    if not status_ok:
        result["reason"] = "status_not_allowed"
        return result
    success_json_path = probe.get("success_json_path")
    if success_json_path:
        if not isinstance(payload, dict):
            result["reason"] = "json_required"
            return result
        actual_value = get_by_path(payload, success_json_path)
        expected_value = probe.get("success_value")
        if expected_value is not None and str(actual_value) != str(expected_value):
            result["reason"] = "json_value_mismatch"
            result["actual_value"] = actual_value
            result["expected_value"] = expected_value
            return result
    if not response.content and not probe.get("allow_empty_body", False) and not success_json_path:
        result["reason"] = "empty_body"
        return result
    result["ok"] = True
    return result
```

将 `build_probe_request_kwargs` 的 `timeout` 改为 `(
int(probe.get("connect_timeout_seconds", 2) or 2),
int(probe.get("read_timeout_seconds", 5) or 5),
)`。将 `validate_stage_probes` 的兜底异常结果改为：

```python
except Exception as exc:
    results.append(
        {
            "stage": stage_name,
            "enabled": True,
            "ok": False,
            "reason": "probe_error",
            "error": type(exc).__name__,
        }
    )
```

这样既有 `format_probe_failure` 可以继续显示安全的“错误类型”。

- [ ] **步骤 6：运行新增测试，确认通过**

运行：

```powershell
pytest tests/test_session_manager.py::test_execute_stage_probe_retries_one_transient_request_failure tests/test_session_manager.py::test_execute_stage_probe_reports_safe_error_type_after_one_retry tests/test_session_manager.py::test_execute_stage_probe_does_not_retry_authentication_response -v
```

预期：三个测试均通过。

- [ ] **步骤 7：更新 City Ops 探活配置**

在 `config/modules/autologin.json` 的 `stage_probes.city_ops` 中添加：

```json
"connect_timeout_seconds": 2,
"read_timeout_seconds": 5
```

该探活当前未显式配置 `timeout_seconds`，因此无需删除任何字段。

- [ ] **步骤 8：运行会话模块回归测试**

运行：`pytest tests/test_session_manager.py -v`

预期：所有会话管理测试通过。

- [ ] **步骤 9：检查格式并提交**

运行：

```powershell
ruff check services/session_manager.py tests/test_session_manager.py
git diff --check
git add services/session_manager.py config/modules/autologin.json tests/test_session_manager.py docs/superpowers/plans/2026-07-23-city-ops-probe-retry.md
git commit -m "fix: retry transient city ops probe failures"
```

预期：Ruff 和差异检查均通过，提交只包含探活重试相关文件。
