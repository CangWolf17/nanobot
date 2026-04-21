"""Tests for Feishu streaming (send_delta) via CardKit streaming API."""
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from nanobot.bus.events import OutboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.channels.feishu import FeishuChannel, FeishuConfig, _FeishuStreamBuf


def _make_channel(streaming: bool = True) -> FeishuChannel:
    config = FeishuConfig(
        enabled=True,
        app_id="cli_test",
        app_secret="secret",
        allow_from=["*"],
        streaming=streaming,
    )
    ch = FeishuChannel(config, MessageBus())
    ch._client = MagicMock()
    ch._loop = None
    return ch


def _mock_create_card_response(card_id: str = "card_stream_001"):
    resp = MagicMock()
    resp.success.return_value = True
    resp.data = SimpleNamespace(card_id=card_id)
    return resp


def _mock_send_response(message_id: str = "om_stream_001"):
    resp = MagicMock()
    resp.success.return_value = True
    resp.data = SimpleNamespace(message_id=message_id)
    return resp


def _mock_content_response(success: bool = True):
    resp = MagicMock()
    resp.success.return_value = success
    resp.code = 0 if success else 99999
    resp.msg = "ok" if success else "error"
    return resp


class TestFeishuStreamingConfig:
    def test_streaming_default_true(self):
        assert FeishuConfig().streaming is True
        assert FeishuConfig().streaming_placeholder_text == "正在生成…"

    def test_supports_streaming_when_enabled(self):
        ch = _make_channel(streaming=True)
        assert ch.supports_streaming is True

    def test_supports_streaming_disabled(self):
        ch = _make_channel(streaming=False)
        assert ch.supports_streaming is False




def test_feishu_streaming_completion_notice_defaults_disabled() -> None:
    assert FeishuConfig().streaming_completion_notice_enabled is False


def test_feishu_streaming_completion_notice_can_be_enabled() -> None:
    config = FeishuConfig(streaming_completion_notice_enabled=True)
    assert config.streaming_completion_notice_enabled is True
    assert config.streaming_completion_notice_text == "✅ 回复完成"
    assert config.streaming_completion_notice_mention_user is False
    assert config.streaming_completion_notice_mention_fallback_user_id == ""
    assert config.streaming_completion_notice_mention_fallback_name == ""


class TestCreateStreamingCard:
    def test_returns_card_id_on_success(self):
        ch = _make_channel()
        ch._client.cardkit.v1.card.create.return_value = _mock_create_card_response("card_123")
        ch._client.im.v1.message.create.return_value = _mock_send_response()
        result = ch._create_streaming_card_sync("chat_id", "oc_chat1")
        assert result == "card_123"
        ch._client.cardkit.v1.card.create.assert_called_once()
        ch._client.im.v1.message.create.assert_called_once()
        request = ch._client.cardkit.v1.card.create.call_args[0][0]
        assert "正在生成…" in request.request_body.data

    def test_returns_none_on_failure(self):
        ch = _make_channel()
        resp = MagicMock()
        resp.success.return_value = False
        resp.code = 99999
        resp.msg = "error"
        ch._client.cardkit.v1.card.create.return_value = resp
        assert ch._create_streaming_card_sync("chat_id", "oc_chat1") is None

    def test_returns_none_on_exception(self):
        ch = _make_channel()
        ch._client.cardkit.v1.card.create.side_effect = RuntimeError("network")
        assert ch._create_streaming_card_sync("chat_id", "oc_chat1") is None

    def test_returns_none_when_card_send_fails(self):
        ch = _make_channel()
        ch._client.cardkit.v1.card.create.return_value = _mock_create_card_response("card_123")
        resp = MagicMock()
        resp.success.return_value = False
        resp.code = 99999
        resp.msg = "error"
        resp.get_log_id.return_value = "log1"
        ch._client.im.v1.message.create.return_value = resp
        assert ch._create_streaming_card_sync("chat_id", "oc_chat1") is None


class TestCloseStreamingMode:
    def test_returns_true_on_success(self):
        ch = _make_channel()
        ch._client.cardkit.v1.card.settings.return_value = _mock_content_response(True)
        assert ch._close_streaming_mode_sync("card_1", 10) is True

    def test_returns_false_on_failure(self):
        ch = _make_channel()
        ch._client.cardkit.v1.card.settings.return_value = _mock_content_response(False)
        assert ch._close_streaming_mode_sync("card_1", 10) is False

    def test_returns_false_on_exception(self):
        ch = _make_channel()
        ch._client.cardkit.v1.card.settings.side_effect = RuntimeError("err")
        assert ch._close_streaming_mode_sync("card_1", 10) is False


class TestStreamUpdateText:
    def test_returns_true_on_success(self):
        ch = _make_channel()
        ch._client.cardkit.v1.card_element.content.return_value = _mock_content_response(True)
        assert ch._stream_update_text_sync("card_1", "hello", 1) is True

    def test_returns_false_on_failure(self):
        ch = _make_channel()
        ch._client.cardkit.v1.card_element.content.return_value = _mock_content_response(False)
        assert ch._stream_update_text_sync("card_1", "hello", 1) is False

    def test_returns_false_on_exception(self):
        ch = _make_channel()
        ch._client.cardkit.v1.card_element.content.side_effect = RuntimeError("err")
        assert ch._stream_update_text_sync("card_1", "hello", 1) is False


class TestSendDelta:
    @pytest.mark.asyncio
    async def test_stream_start_creates_card_before_first_delta(self):
        ch = _make_channel()
        ch._client.cardkit.v1.card.create.return_value = _mock_create_card_response("card_new")
        ch._client.im.v1.message.create.return_value = _mock_send_response("om_new")

        await ch.send_delta("oc_chat1", "", metadata={"_stream_start": True})

        assert "oc_chat1" in ch._stream_bufs
        buf = ch._stream_bufs["oc_chat1"]
        assert buf.card_id == "card_new"
        ch._client.cardkit.v1.card.create.assert_called_once()
        ch._client.im.v1.message.create.assert_called_once()

    @pytest.mark.asyncio
    async def test_first_delta_creates_card_and_sends(self):
        ch = _make_channel()
        ch._client.cardkit.v1.card.create.return_value = _mock_create_card_response("card_new")
        ch._client.im.v1.message.create.return_value = _mock_send_response("om_new")
        ch._client.cardkit.v1.card_element.content.return_value = _mock_content_response()

        await ch.send_delta("oc_chat1", "Hello ")

        assert "oc_chat1" in ch._stream_bufs
        buf = ch._stream_bufs["oc_chat1"]
        assert buf.text == "Hello "
        assert buf.card_id == "card_new"
        assert buf.sequence == 1
        ch._client.cardkit.v1.card.create.assert_called_once()
        ch._client.im.v1.message.create.assert_called_once()
        ch._client.cardkit.v1.card_element.content.assert_called_once()

    @pytest.mark.asyncio
    async def test_first_visible_delta_after_stream_start_updates_immediately(self):
        ch = _make_channel()
        ch._stream_bufs["oc_chat1"] = _FeishuStreamBuf(text="", card_id="card_1", sequence=0, last_edit=time.monotonic())
        ch._client.cardkit.v1.card_element.content.return_value = _mock_content_response()

        await ch.send_delta("oc_chat1", "Hello")

        buf = ch._stream_bufs["oc_chat1"]
        assert buf.text == "Hello"
        assert buf.sequence == 1
        ch._client.cardkit.v1.card_element.content.assert_called_once()

    @pytest.mark.asyncio
    async def test_second_delta_within_interval_skips_update(self):
        ch = _make_channel()
        buf = _FeishuStreamBuf(text="Hello ", card_id="card_1", sequence=1, last_edit=time.monotonic())
        ch._stream_bufs["oc_chat1"] = buf

        await ch.send_delta("oc_chat1", "world")

        assert buf.text == "Hello world"
        ch._client.cardkit.v1.card_element.content.assert_not_called()

    @pytest.mark.asyncio
    async def test_delta_after_interval_updates_text(self):
        ch = _make_channel()
        buf = _FeishuStreamBuf(text="Hello ", card_id="card_1", sequence=1, last_edit=time.monotonic() - 1.0)
        ch._stream_bufs["oc_chat1"] = buf

        ch._client.cardkit.v1.card_element.content.return_value = _mock_content_response()
        await ch.send_delta("oc_chat1", "world")

        assert buf.text == "Hello world"
        assert buf.sequence == 2
        ch._client.cardkit.v1.card_element.content.assert_called_once()

    @pytest.mark.asyncio
    async def test_stream_end_sends_final_update(self):
        ch = _make_channel()
        ch._stream_bufs["oc_chat1"] = _FeishuStreamBuf(
            text="Final content", card_id="card_1", sequence=3, last_edit=0.0,
        )
        ch._client.cardkit.v1.card_element.content.return_value = _mock_content_response()
        ch._client.cardkit.v1.card.settings.return_value = _mock_content_response()

        await ch.send_delta("oc_chat1", "", metadata={"_stream_end": True})

        assert "oc_chat1" not in ch._stream_bufs
        ch._client.cardkit.v1.card_element.content.assert_called_once()
        ch._client.cardkit.v1.card.settings.assert_called_once()
        settings_call = ch._client.cardkit.v1.card.settings.call_args[0][0]
        assert settings_call.body.sequence == 5  # after final content seq 4

    @pytest.mark.asyncio
    async def test_stream_end_fallback_when_no_card_id(self):
        """If card creation failed, stream_end falls back to a plain card message."""
        ch = _make_channel()
        ch._stream_bufs["oc_chat1"] = _FeishuStreamBuf(
            text="Fallback content", card_id=None, sequence=0, last_edit=0.0,
        )
        ch._client.im.v1.message.create.return_value = _mock_send_response("om_fb")

        await ch.send_delta("oc_chat1", "", metadata={"_stream_end": True})

        assert "oc_chat1" not in ch._stream_bufs
        ch._client.cardkit.v1.card_element.content.assert_not_called()
        ch._client.im.v1.message.create.assert_called_once()


    @pytest.mark.asyncio
    async def test_stream_end_does_not_send_completion_notice_without_explicit_metadata(self):
        ch = _make_channel()
        ch.config.streaming_completion_notice_enabled = True
        ch.config.streaming_completion_notice_text = "✅ 回复完成"
        ch._stream_bufs["oc_chat1"] = _FeishuStreamBuf(
            text="Final content", card_id="card_1", sequence=3, last_edit=0.0,
        )

        from unittest.mock import patch
        with (
            patch.object(ch, "_stream_update_text_sync", return_value=True) as mock_update,
            patch.object(ch, "_close_streaming_mode_sync", return_value=True) as mock_close,
            patch.object(ch, "_send_message_sync", return_value="om_done") as mock_send,
        ):
            await ch.send_delta("oc_chat1", "", metadata={"_stream_end": True, "_resuming": False})

        assert "oc_chat1" not in ch._stream_bufs
        mock_update.assert_called_once_with("card_1", "Final content", 4)
        mock_close.assert_called_once_with("card_1", 5)
        mock_send.assert_not_called()

    @pytest.mark.asyncio
    async def test_send_skips_completion_notice_when_mention_flag_disabled(self):
        ch = _make_channel()
        ch.config.streaming_completion_notice_enabled = True
        ch.config.streaming_completion_notice_mention_user = False

        from unittest.mock import patch

        from nanobot.bus.events import OutboundMessage
        with patch.object(ch, "_send_message_sync", return_value="om_done") as mock_send:
            await ch.send(OutboundMessage(
                channel="feishu",
                chat_id="oc_chat1",
                content="Final content that was already streamed",
                metadata={
                    "_streamed": True,
                    "_completion_notice": True,
                    "_completion_notice_text": "✅ 回复完成",
                    "_completion_notice_mention_user_id": "ou_runtime_user",
                },
            ))

        mock_send.assert_not_called()

    @pytest.mark.asyncio
    async def test_send_emits_completion_notice_with_group_mention_when_enabled(self):
        ch = _make_channel()
        ch.config.streaming_completion_notice_enabled = True
        ch.config.streaming_completion_notice_mention_user = True

        from unittest.mock import patch

        from nanobot.bus.events import OutboundMessage
        with patch.object(ch, "_send_message_sync", return_value="om_done") as mock_send:
            await ch.send(OutboundMessage(
                channel="feishu",
                chat_id="oc_chat1",
                content="Final content that was already streamed",
                metadata={
                    "_streamed": True,
                    "_completion_notice": True,
                    "_completion_notice_text": "✅ 回复完成",
                    "_completion_notice_mention_user_id": "ou_runtime_user",
                },
            ))

        args = mock_send.call_args[0]
        assert args[0] == "chat_id"
        assert args[1] == "oc_chat1"
        assert args[2] == "interactive"
        assert '<at id=ou_runtime_user></at> ✅ 回复完成' in args[3]

    @pytest.mark.asyncio
    async def test_send_completion_notice_in_direct_message_emits_interactive_markdown_at_notice(self):
        ch = _make_channel()
        ch.config.streaming_completion_notice_enabled = True
        ch.config.streaming_completion_notice_mention_user = True

        from unittest.mock import patch

        from nanobot.bus.events import OutboundMessage
        with patch.object(ch, "_send_message_sync", return_value="om_done") as mock_send:
            await ch.send(OutboundMessage(
                channel="feishu",
                chat_id="ou_runtime_user",
                content="Final content that was already streamed",
                metadata={
                    "_streamed": True,
                    "_completion_notice": True,
                    "_completion_notice_text": "✅ 回复完成",
                    "_completion_notice_mention_user_id": "ou_runtime_user",
                },
            ))

        args = mock_send.call_args[0]
        assert args[0] == "open_id"
        assert args[1] == "ou_runtime_user"
        assert args[2] == "interactive"
        assert '<at id=ou_runtime_user></at> ✅ 回复完成' in args[3]

    @pytest.mark.asyncio
    async def test_send_completion_notice_ignores_fallback_name_and_uses_runtime_markdown_at(self):
        ch = _make_channel()
        ch.config.streaming_completion_notice_enabled = True
        ch.config.streaming_completion_notice_mention_user = True
        ch.config.streaming_completion_notice_mention_fallback_name = "Tim"

        from unittest.mock import patch

        from nanobot.bus.events import OutboundMessage
        with patch.object(ch, "_send_message_sync", return_value="om_done") as mock_send:
            await ch.send(OutboundMessage(
                channel="feishu",
                chat_id="ou_runtime_user",
                content="Final content that was already streamed",
                metadata={
                    "_streamed": True,
                    "_completion_notice": True,
                    "_completion_notice_text": "✅ 回复完成",
                    "_completion_notice_mention_user_id": "ou_runtime_user",
                },
            ))

        args = mock_send.call_args[0]
        assert args[2] == "interactive"
        assert '<at id=ou_runtime_user></at> ✅ 回复完成' in args[3]
        assert "Tim" not in args[3]

    @pytest.mark.asyncio
    async def test_send_completion_notice_uses_fallback_user_id_when_runtime_id_missing(self):
        ch = _make_channel()
        ch.config.streaming_completion_notice_enabled = True
        ch.config.streaming_completion_notice_mention_user = True
        ch.config.streaming_completion_notice_mention_fallback_user_id = "ou_fallback_user"

        from unittest.mock import patch

        from nanobot.bus.events import OutboundMessage
        with patch.object(ch, "_send_message_sync", return_value="om_done") as mock_send:
            await ch.send(OutboundMessage(
                channel="feishu",
                chat_id="oc_chat1",
                content="Final content that was already streamed",
                metadata={
                    "_streamed": True,
                    "_completion_notice": True,
                    "_completion_notice_text": "✅ 回复完成",
                },
            ))

        args = mock_send.call_args[0]
        assert args[2] == "interactive"
        assert '<at id=ou_fallback_user></at> ✅ 回复完成' in args[3]

    @pytest.mark.asyncio
    async def test_send_completion_notice_skips_when_no_runtime_or_fallback_user_id(self):
        ch = _make_channel()
        ch.config.streaming_completion_notice_enabled = True
        ch.config.streaming_completion_notice_mention_user = True

        from unittest.mock import patch

        from nanobot.bus.events import OutboundMessage
        with patch.object(ch, "_send_message_sync", return_value="om_done") as mock_send:
            await ch.send(OutboundMessage(
                channel="feishu",
                chat_id="oc_chat1",
                content="Final content that was already streamed",
                metadata={
                    "_streamed": True,
                    "_completion_notice": True,
                    "_completion_notice_text": "✅ 回复完成",
                },
            ))

        mock_send.assert_not_called()

    @pytest.mark.asyncio
    async def test_stream_end_skips_completion_notice_when_resuming(self):
        ch = _make_channel()
        ch.config.streaming_completion_notice_enabled = True
        ch._stream_bufs["oc_chat1"] = _FeishuStreamBuf(
            text="Final content", card_id="card_1", sequence=3, last_edit=0.0,
        )

        from unittest.mock import patch
        with (
            patch.object(ch, "_stream_update_text_sync", return_value=True),
            patch.object(ch, "_close_streaming_mode_sync", return_value=True),
            patch.object(ch, "_send_message_sync", return_value="om_done") as mock_send,
        ):
            await ch.send_delta("oc_chat1", "", metadata={"_stream_end": True, "_resuming": True})

        mock_send.assert_not_called()

    @pytest.mark.asyncio
    async def test_stream_end_skips_completion_notice_when_disabled(self):
        ch = _make_channel()
        ch.config.streaming_completion_notice_enabled = False
        ch._stream_bufs["oc_chat1"] = _FeishuStreamBuf(
            text="Final content", card_id="card_1", sequence=3, last_edit=0.0,
        )

        from unittest.mock import patch
        with (
            patch.object(ch, "_stream_update_text_sync", return_value=True),
            patch.object(ch, "_close_streaming_mode_sync", return_value=True),
            patch.object(ch, "_send_message_sync", return_value="om_done") as mock_send,
        ):
            await ch.send_delta("oc_chat1", "", metadata={"_stream_end": True, "_resuming": False})

        mock_send.assert_not_called()

    @pytest.mark.asyncio
    async def test_stream_end_resuming_keeps_buffer(self):
        """_resuming=True flushes text to card but keeps the buffer for the next segment."""
        ch = _make_channel()
        ch._stream_bufs["oc_chat1"] = _FeishuStreamBuf(
            text="Partial answer", card_id="card_1", sequence=2, last_edit=0.0,
        )
        ch._client.cardkit.v1.card_element.content.return_value = _mock_content_response()

        await ch.send_delta("oc_chat1", "", metadata={"_stream_end": True, "_resuming": True})

        assert "oc_chat1" in ch._stream_bufs
        buf = ch._stream_bufs["oc_chat1"]
        assert buf.card_id == "card_1"
        assert buf.sequence == 3
        ch._client.cardkit.v1.card_element.content.assert_called_once()
        ch._client.cardkit.v1.card.settings.assert_not_called()

    @pytest.mark.asyncio
    async def test_stream_end_resuming_then_final_end(self):
        """Full multi-segment flow: resuming mid-turn, then final end closes the card."""
        ch = _make_channel()
        ch._stream_bufs["oc_chat1"] = _FeishuStreamBuf(
            text="Seg1", card_id="card_1", sequence=1, last_edit=0.0,
        )
        ch._client.cardkit.v1.card_element.content.return_value = _mock_content_response()
        ch._client.cardkit.v1.card.settings.return_value = _mock_content_response()

        await ch.send_delta("oc_chat1", "", metadata={"_stream_end": True, "_resuming": True})
        assert "oc_chat1" in ch._stream_bufs

        ch._stream_bufs["oc_chat1"].text += " Seg2"
        await ch.send_delta("oc_chat1", "", metadata={"_stream_end": True})

        assert "oc_chat1" not in ch._stream_bufs
        ch._client.cardkit.v1.card.settings.assert_called_once()

    @pytest.mark.asyncio
    async def test_stream_end_resuming_no_card_is_noop(self):
        """_resuming with no card_id (card creation failed) is a safe no-op."""
        ch = _make_channel()
        ch._stream_bufs["oc_chat1"] = _FeishuStreamBuf(
            text="text", card_id=None, sequence=0, last_edit=0.0,
        )
        await ch.send_delta("oc_chat1", "", metadata={"_stream_end": True, "_resuming": True})

        assert "oc_chat1" in ch._stream_bufs
        ch._client.cardkit.v1.card_element.content.assert_not_called()

    @pytest.mark.asyncio
    async def test_stream_end_without_buf_is_noop(self):
        ch = _make_channel()
        await ch.send_delta("oc_chat1", "", metadata={"_stream_end": True})
        ch._client.cardkit.v1.card_element.content.assert_not_called()

    @pytest.mark.asyncio
    async def test_empty_delta_skips_send(self):
        ch = _make_channel()
        await ch.send_delta("oc_chat1", "   ")

        assert "oc_chat1" in ch._stream_bufs
        ch._client.cardkit.v1.card.create.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_client_returns_early(self):
        ch = _make_channel()
        ch._client = None
        await ch.send_delta("oc_chat1", "text")
        assert "oc_chat1" not in ch._stream_bufs

    @pytest.mark.asyncio
    async def test_sequence_increments_correctly(self):
        ch = _make_channel()
        buf = _FeishuStreamBuf(text="a", card_id="card_1", sequence=5, last_edit=0.0)
        ch._stream_bufs["oc_chat1"] = buf

        ch._client.cardkit.v1.card_element.content.return_value = _mock_content_response()
        await ch.send_delta("oc_chat1", "b")
        assert buf.sequence == 6

        buf.last_edit = 0.0  # reset to bypass throttle
        await ch.send_delta("oc_chat1", "c")
        assert buf.sequence == 7


    @pytest.mark.asyncio
    async def test_send_weather_brief_forces_interactive_card(self):
        ch = _make_channel()

        from unittest.mock import patch

        from nanobot.bus.events import OutboundMessage
        with patch.object(ch, "_send_message_sync", return_value="om_done") as mock_send:
            await ch.send(OutboundMessage(
                channel="feishu",
                chat_id="oc_chat1",
                content="天气成品",
                metadata={"workspace_agent_cmd": "weather_brief"},
            ))

        args = mock_send.call_args[0]
        assert args[0] == "chat_id"
        assert args[1] == "oc_chat1"
        assert args[2] == "interactive"
        assert '"elements"' in args[3]

    @pytest.mark.asyncio
    async def test_send_render_as_interactive_forces_interactive_card(self):
        ch = _make_channel()

        from unittest.mock import patch

        from nanobot.bus.events import OutboundMessage
        with patch.object(ch, "_send_message_sync", return_value="om_done") as mock_send:
            await ch.send(OutboundMessage(
                channel="feishu",
                chat_id="oc_chat1",
                content="已上线",
                metadata={"render_as": "interactive"},
            ))

        args = mock_send.call_args[0]
        assert args[0] == "chat_id"
        assert args[1] == "oc_chat1"
        assert args[2] == "interactive"
        assert '"elements"' in args[3]

    """Tool hint messages should be inlined into active streaming cards."""

    @pytest.mark.asyncio
    async def test_tool_hint_inlined_when_stream_active(self):
        """With an active streaming buffer, tool hint appends to the card."""
        ch = _make_channel()
        ch._stream_bufs["oc_chat1"] = _FeishuStreamBuf(
            text="Partial answer", card_id="card_1", sequence=2, last_edit=0.0,
        )
        ch._client.cardkit.v1.card_element.content.return_value = _mock_content_response()

        msg = OutboundMessage(
            channel="feishu", chat_id="oc_chat1",
            content='web_fetch("https://example.com")',
            metadata={"_tool_hint": True},
        )
        await ch.send(msg)

        buf = ch._stream_bufs["oc_chat1"]
        assert '🔧 web_fetch("https://example.com")' in buf.text
        assert buf.sequence == 3
        ch._client.cardkit.v1.card_element.content.assert_called_once()
        ch._client.im.v1.message.create.assert_not_called()

    @pytest.mark.asyncio
    async def test_tool_hint_preserved_on_next_delta(self):
        """When new delta arrives, the tool hint is kept as permanent content and delta appends after it."""
        ch = _make_channel()
        ch._stream_bufs["oc_chat1"] = _FeishuStreamBuf(
            text="Partial answer\n\n🔧 web_fetch(\"url\")\n\n",
            card_id="card_1", sequence=3, last_edit=0.0,
        )
        ch._client.cardkit.v1.card_element.content.return_value = _mock_content_response()

        await ch.send_delta("oc_chat1", " continued")

        buf = ch._stream_bufs["oc_chat1"]
        assert "Partial answer" in buf.text
        assert "🔧 web_fetch" in buf.text
        assert buf.text.endswith(" continued")

    @pytest.mark.asyncio
    async def test_tool_hint_fallback_when_no_stream(self):
        """Without an active buffer, tool hint falls back to a standalone card."""
        ch = _make_channel()
        ch._client.im.v1.message.create.return_value = _mock_send_response("om_hint")

        msg = OutboundMessage(
            channel="feishu", chat_id="oc_chat1",
            content='read_file("path")',
            metadata={"_tool_hint": True},
        )
        await ch.send(msg)

        assert "oc_chat1" not in ch._stream_bufs
        ch._client.im.v1.message.create.assert_called_once()

    @pytest.mark.asyncio
    async def test_consecutive_tool_hints_append(self):
        """When multiple tool hints arrive consecutively, each appends to the card."""
        ch = _make_channel()
        ch._stream_bufs["oc_chat1"] = _FeishuStreamBuf(
            text="Partial answer", card_id="card_1", sequence=2, last_edit=0.0,
        )
        ch._client.cardkit.v1.card_element.content.return_value = _mock_content_response()

        msg1 = OutboundMessage(
            channel="feishu", chat_id="oc_chat1",
            content='$ cd /project', metadata={"_tool_hint": True},
        )
        await ch.send(msg1)

        msg2 = OutboundMessage(
            channel="feishu", chat_id="oc_chat1",
            content='$ git status', metadata={"_tool_hint": True},
        )
        await ch.send(msg2)

        buf = ch._stream_bufs["oc_chat1"]
        assert "$ cd /project" in buf.text
        assert "$ git status" in buf.text
        assert buf.text.startswith("Partial answer")
        assert "🔧 $ cd /project" in buf.text
        assert "🔧 $ git status" in buf.text

    @pytest.mark.asyncio
    async def test_tool_hint_preserved_on_resuming_flush(self):
        """When _resuming flushes the buffer, tool hint is kept as permanent content."""
        ch = _make_channel()
        ch._stream_bufs["oc_chat1"] = _FeishuStreamBuf(
            text="Partial answer\n\n🔧 $ cd /project\n\n",
            card_id="card_1", sequence=2, last_edit=0.0,
        )
        ch._client.cardkit.v1.card_element.content.return_value = _mock_content_response()

        await ch.send_delta("oc_chat1", "", metadata={"_stream_end": True, "_resuming": True})

        buf = ch._stream_bufs["oc_chat1"]
        assert "Partial answer" in buf.text
        assert "🔧 $ cd /project" in buf.text

    @pytest.mark.asyncio
    async def test_tool_hint_preserved_on_final_stream_end(self):
        """When final _stream_end closes the card, tool hint is kept in the final text."""
        ch = _make_channel()
        ch._stream_bufs["oc_chat1"] = _FeishuStreamBuf(
            text="Final content\n\n🔧 web_fetch(\"url\")\n\n",
            card_id="card_1", sequence=3, last_edit=0.0,
        )
        ch._client.cardkit.v1.card_element.content.return_value = _mock_content_response()
        ch._client.cardkit.v1.card.settings.return_value = _mock_content_response()

        await ch.send_delta("oc_chat1", "", metadata={"_stream_end": True})

        assert "oc_chat1" not in ch._stream_bufs
        update_call = ch._client.cardkit.v1.card_element.content.call_args[0][0]
        assert "🔧" in update_call.body.content

    @pytest.mark.asyncio
    async def test_empty_tool_hint_is_noop(self):
        """Empty or whitespace-only tool hint content is silently ignored."""
        ch = _make_channel()
        ch._stream_bufs["oc_chat1"] = _FeishuStreamBuf(
            text="Partial answer", card_id="card_1", sequence=2, last_edit=0.0,
        )

        for content in ("", "   ", "\t\n"):
            msg = OutboundMessage(
                channel="feishu", chat_id="oc_chat1",
                content=content, metadata={"_tool_hint": True},
            )
            await ch.send(msg)

        buf = ch._stream_bufs["oc_chat1"]
        assert buf.text == "Partial answer"
        assert buf.sequence == 2
        ch._client.cardkit.v1.card_element.content.assert_not_called()


class TestSendMessageReturnsId:
    def test_returns_message_id_on_success(self):
        ch = _make_channel()
        ch._client.im.v1.message.create.return_value = _mock_send_response("om_abc")
        result = ch._send_message_sync("chat_id", "oc_chat1", "text", '{"text":"hi"}')
        assert result == "om_abc"

    def test_returns_none_on_failure(self):
        ch = _make_channel()
        resp = MagicMock()
        resp.success.return_value = False
        resp.code = 99999
        resp.msg = "error"
        resp.get_log_id.return_value = "log1"
        ch._client.im.v1.message.create.return_value = resp
        result = ch._send_message_sync("chat_id", "oc_chat1", "text", '{"text":"hi"}')
        assert result is None
