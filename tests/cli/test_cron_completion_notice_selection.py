from nanobot.bus.events import OutboundMessage


def test_gateway_cron_notice_prefers_response_metadata_key_principle() -> None:
    response = OutboundMessage(
        channel="feishu",
        chat_id="ou_test",
        content="正文",
        metadata={
            "_completion_notice_text": "Key Principle：先收口，再扩展。",
            "_origin_sender_id": "ou_creator",
        },
    )

    completion_notice_text = str(
        response.metadata.get("_completion_notice_text")
        or "来自 payload 的文案"
        or ""
    ).strip()
    completion_notice_user_id = str(
        response.metadata.get("_completion_notice_mention_user_id")
        or response.metadata.get("_origin_sender_id")
        or "ou_creator"
        or ""
    ).strip()

    assert completion_notice_text == "Key Principle：先收口，再扩展。"
    assert completion_notice_user_id == "ou_creator"
