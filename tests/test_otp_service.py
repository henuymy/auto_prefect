from infrastructure.gotify_client import delete_message as gotify_delete_message
from services import otp_service
from datetime import datetime, timezone


def test_delete_message_is_reexported_for_login_service() -> None:
    assert otp_service.delete_message is gotify_delete_message


def test_wait_for_otp_supports_subsecond_polling(monkeypatch) -> None:
    messages = [
        [],
        [
            {
                "id": 2,
                "title": "10658221",
                "message": "动态密钥 123456",
                "date": datetime.now(timezone.utc).isoformat(),
            }
        ],
    ]
    sleeps = []
    monkeypatch.setattr(otp_service, "fetch_messages", lambda _: messages.pop(0))
    monkeypatch.setattr(otp_service.time, "sleep", sleeps.append)

    result = otp_service.wait_for_otp(
        {
            "timeout_seconds": 5,
            "poll_interval_seconds": 0.5,
            "title_prefix": "10658221",
            "required_keywords": ["动态密钥"],
            "allowed_senders": ["10658221"],
            "code_regex": r"(?<!\d)(\d{6})(?!\d)",
        },
        datetime.now(timezone.utc),
        latest_message_id=1,
    )

    assert result.code == "123456"
    assert sleeps == [0.5]
