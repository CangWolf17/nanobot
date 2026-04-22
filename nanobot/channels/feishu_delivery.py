"""Runtime-owned Feishu delivery helpers for workspace callers."""

from __future__ import annotations

import importlib.util
import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from nanobot.channels.feishu_render import (
    build_interactive_card_payload,
    build_streaming_placeholder_card_json,
)

FEISHU_AVAILABLE = importlib.util.find_spec("lark_oapi") is not None
CONFIG_PATH = Path.home() / ".nanobot" / "config.json"
STREAM_ELEMENT_ID = "stream_content"
STREAM_LAG_THRESHOLD_SECONDS = 0.60


def _load_feishu_config(config_path: Path = CONFIG_PATH) -> dict[str, Any]:
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return {}
    channels = payload.get("channels")
    if not isinstance(channels, dict):
        return {}
    feishu = channels.get("feishu")
    return dict(feishu) if isinstance(feishu, dict) else {}


@lru_cache(maxsize=1)
def _get_client() -> Any | None:
    if not FEISHU_AVAILABLE:
        return None
    config = _load_feishu_config()
    app_id = str(config.get("appId") or config.get("app_id") or "").strip()
    app_secret = str(config.get("appSecret") or config.get("app_secret") or "").strip()
    if not app_id or not app_secret:
        return None

    import lark_oapi as lark

    return (
        lark.Client.builder()
        .app_id(app_id)
        .app_secret(app_secret)
        .log_level(lark.LogLevel.INFO)
        .build()
    )


def _send_message_sync(receive_id_type: str, receive_id: str, msg_type: str, content: str) -> dict[str, Any]:
    client = _get_client()
    if client is None:
        return {"ok": False, "error": "delivery_contract_unavailable"}

    from lark_oapi.api.im.v1 import CreateMessageRequest, CreateMessageRequestBody

    request = (
        CreateMessageRequest.builder()
        .receive_id_type(receive_id_type)
        .request_body(
            CreateMessageRequestBody.builder()
            .receive_id(receive_id)
            .msg_type(msg_type)
            .content(content)
            .build()
        )
        .build()
    )
    response = client.im.v1.message.create(request)
    if not response.success():
        return {
            "ok": False,
            "error": "message_send_failed",
            "code": response.code,
            "msg": response.msg,
        }
    return {"ok": True, "message_id": getattr(response.data, "message_id", None)}


def send_interactive_card(
    receive_id: str,
    content: str,
    *,
    receive_id_type: str = "open_id",
    title: str | None = None,
    mention_user_id: str | None = None,
    mention_all: bool = False,
) -> dict[str, Any]:
    payload = build_interactive_card_payload(
        content,
        title=title,
        mention_user_id=mention_user_id,
        mention_all=mention_all,
    )
    return _send_message_sync(receive_id_type, receive_id, payload["msg_type"], payload["content"])


def send_text_message(receive_id: str, text: str, *, receive_id_type: str = "open_id") -> dict[str, Any]:
    content = json.dumps({"text": text}, ensure_ascii=False)
    return _send_message_sync(receive_id_type, receive_id, "text", content)


def create_bridge_stream_card(receive_id: str, *, receive_id_type: str = "chat_id", placeholder_text: str = "正在生成…") -> dict[str, Any]:
    client = _get_client()
    if client is None:
        return {"ok": False, "fallback_reason": "delivery_contract_unavailable"}

    from lark_oapi.api.cardkit.v1 import CreateCardRequest, CreateCardRequestBody

    request = (
        CreateCardRequest.builder()
        .request_body(
            CreateCardRequestBody.builder()
            .type("card_json")
            .data(build_streaming_placeholder_card_json(placeholder_text))
            .build()
        )
        .build()
    )
    response = client.cardkit.v1.card.create(request)
    if not response.success():
        return {"ok": False, "fallback_reason": "stream_handle_unavailable", "code": response.code, "msg": response.msg}

    card_id = getattr(response.data, "card_id", None)
    if not card_id:
        return {"ok": False, "fallback_reason": "stream_handle_unavailable"}

    sent = _send_message_sync(
        receive_id_type,
        receive_id,
        "interactive",
        json.dumps({"type": "card", "data": {"card_id": card_id}}, ensure_ascii=False),
    )
    if not sent.get("ok"):
        return {"ok": False, "fallback_reason": "stream_handle_unavailable", "message_error": sent}
    return {"ok": True, "card_id": card_id, "message_id": sent.get("message_id")}


def update_bridge_stream_card(card_id: str, content: str, sequence: int) -> dict[str, Any]:
    client = _get_client()
    if client is None:
        return {"ok": False, "error": "delivery_contract_unavailable"}

    from lark_oapi.api.cardkit.v1 import ContentCardElementRequest, ContentCardElementRequestBody

    request = (
        ContentCardElementRequest.builder()
        .card_id(card_id)
        .element_id(STREAM_ELEMENT_ID)
        .request_body(ContentCardElementRequestBody.builder().content(content).sequence(sequence).build())
        .build()
    )
    response = client.cardkit.v1.card_element.content(request)
    if not response.success():
        return {"ok": False, "error": "stream_update_failed", "code": response.code, "msg": response.msg}
    return {"ok": True}


def close_bridge_stream_card(card_id: str, sequence: int) -> dict[str, Any]:
    client = _get_client()
    if client is None:
        return {"ok": False, "error": "delivery_contract_unavailable"}

    from lark_oapi.api.cardkit.v1 import SettingsCardRequest, SettingsCardRequestBody

    request = (
        SettingsCardRequest.builder()
        .card_id(card_id)
        .request_body(
            SettingsCardRequestBody.builder()
            .settings(json.dumps({"config": {"streaming_mode": False}}, ensure_ascii=False))
            .sequence(sequence)
            .uuid("workspace-bridge-close")
            .build()
        )
        .build()
    )
    response = client.cardkit.v1.card.settings(request)
    if not response.success():
        return {"ok": False, "error": "stream_close_failed", "code": response.code, "msg": response.msg}
    return {"ok": True}
