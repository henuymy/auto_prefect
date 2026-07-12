import json

from services.cookie_recorder import CookieRecorder, capture_web_storage


class FakeDriver:
    def execute_script(self, _script):
        return {
            "storage_origin": "https://usm.ha.cmcc:19011",
            "storage_url": "https://usm.ha.cmcc:19011/zhyypt/dist/",
            "session_storage": {"zhyyptInfo": '{"accessToken":"token"}'},
            "local_storage": {},
        }


class FailingDriver:
    def execute_script(self, _script):
        raise RuntimeError("blocked")


class CaptureDriver(FakeDriver):
    current_url = "https://example/stage"
    title = "stage"

    def get_cookies(self):
        return [{"name": "sid", "value": "private"}]


def test_capture_web_storage_returns_session_storage():
    storage = capture_web_storage(FakeDriver())

    assert storage["session_storage"]["zhyyptInfo"] == '{"accessToken":"token"}'
    assert storage["storage_origin"] == "https://usm.ha.cmcc:19011"


def test_capture_web_storage_keeps_error_context():
    storage = capture_web_storage(FailingDriver())

    assert "blocked" in storage["storage_error"]


def test_cookie_recorder_writes_each_stage_atomically_to_private_snapshot(tmp_path, monkeypatch):
    output_path = tmp_path / "attempt.json"
    replacements = []
    real_replace = __import__("os").replace

    def recording_replace(source, target):
        replacements.append((source, target))
        real_replace(source, target)

    monkeypatch.setattr("services.cookie_recorder.os.replace", recording_replace)
    CookieRecorder(output_path).capture(CaptureDriver(), "city_ops")

    assert json.loads(output_path.read_text(encoding="utf-8"))["stages"][0]["stage"] == "city_ops"
    assert replacements[0][1] == output_path

