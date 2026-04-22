import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from nanobot.channels import feishu as feishu_channel
from nanobot.channels.feishu_delivery import (
    STREAM_ELEMENT_ID,
    build_interactive_card_payload,
    close_bridge_stream_card,
    create_bridge_stream_card,
    send_interactive_card,
    send_text_message,
    update_bridge_stream_card,
)
from nanobot.channels.feishu_render import build_streaming_placeholder_card_json


def _mock_success_response(**data):
    response = MagicMock()
    response.success.return_value = True
    response.data = SimpleNamespace(**data)
    return response


def _mock_failure_response(code: int = 99999, msg: str = "error"):
    response = MagicMock()
    response.success.return_value = False
    response.code = code
    response.msg = msg
    return response


def test_send_interactive_card_uses_shared_payload_shape() -> None:
    client = MagicMock()
    client.im.v1.message.create.return_value = _mock_success_response(message_id="om_1")
    with patch("nanobot.channels.feishu_delivery._get_client", return_value=client):
        result = send_interactive_card("oc_test", "## hello", receive_id_type="chat_id", title="标题")

    assert result["ok"] is True
    request = client.im.v1.message.create.call_args[0][0]
    assert request.request_body.msg_type == "interactive"
    assert "标题" in request.request_body.content
    assert "hello" in request.request_body.content


def test_send_text_message_delegates_to_runtime_delivery() -> None:
    client = MagicMock()
    client.im.v1.message.create.return_value = _mock_success_response(message_id="om_text")
    with patch("nanobot.channels.feishu_delivery._get_client", return_value=client):
        result = send_text_message("oc_test", "hello", receive_id_type="chat_id")

    assert result == {"ok": True, "message_id": "om_text"}


def test_create_bridge_stream_card_returns_handle() -> None:
    client = MagicMock()
    client.cardkit.v1.card.create.return_value = _mock_success_response(card_id="card_1")
    client.im.v1.message.create.return_value = _mock_success_response(message_id="om_card")
    with patch("nanobot.channels.feishu_delivery._get_client", return_value=client):
        result = create_bridge_stream_card("oc_test", receive_id_type="chat_id")

    assert result["ok"] is True
    assert result["card_id"] == "card_1"


def test_create_bridge_stream_card_returns_fallback_when_handle_missing() -> None:
    client = MagicMock()
    client.cardkit.v1.card.create.return_value = _mock_failure_response()
    with patch("nanobot.channels.feishu_delivery._get_client", return_value=client):
        result = create_bridge_stream_card("oc_test", receive_id_type="chat_id")

    assert result["ok"] is False
    assert result["fallback_reason"] == "stream_handle_unavailable"


def test_update_and_close_bridge_stream_card_use_runtime_client() -> None:
    client = MagicMock()
    client.cardkit.v1.card_element.content.return_value = _mock_success_response()
    client.cardkit.v1.card.settings.return_value = _mock_success_response()
    with patch("nanobot.channels.feishu_delivery._get_client", return_value=client):
        assert update_bridge_stream_card("card_1", "hello", 1)["ok"] is True
        assert close_bridge_stream_card("card_1", 2)["ok"] is True


def test_streaming_placeholder_and_runtime_updates_share_element_id() -> None:
    placeholder = json.loads(build_streaming_placeholder_card_json("正在生成…"))
    element = placeholder["body"]["elements"][0]

    assert element["element_id"] == STREAM_ELEMENT_ID
    assert feishu_channel._STREAM_ELEMENT_ID == STREAM_ELEMENT_ID
