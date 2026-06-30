from infrastructure.gotify_client import delete_message as gotify_delete_message
from services import otp_service


def test_delete_message_is_reexported_for_login_service() -> None:
    assert otp_service.delete_message is gotify_delete_message
