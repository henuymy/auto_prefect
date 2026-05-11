from services.cookie_recorder import capture_web_storage


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


def test_capture_web_storage_returns_session_storage():
    storage = capture_web_storage(FakeDriver())

    assert storage["session_storage"]["zhyyptInfo"] == '{"accessToken":"token"}'
    assert storage["storage_origin"] == "https://usm.ha.cmcc:19011"


def test_capture_web_storage_keeps_error_context():
    storage = capture_web_storage(FailingDriver())

    assert "blocked" in storage["storage_error"]

