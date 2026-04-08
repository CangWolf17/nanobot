from __future__ import annotations

import asyncio
import json

import pytest

from nanobot.bus.events import OutboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.channels.discord import DISCORD_API_BASE, DiscordChannel, DiscordConfig


class _FakeResponse:
    def __init__(
        self,
        *,
        status_code: int = 200,
        json_data: dict | None = None,
        content: bytes = b"",
        error: Exception | None = None,
    ) -> None:
        self.status_code = status_code
        self._json_data = json_data or {}
        self.content = content
        self._error = error

    def json(self) -> dict:
        return self._json_data

    def raise_for_status(self) -> None:
        if self._error is not None:
            raise self._error
        if self.status_code >= 400 and self.status_code != 429:
            raise RuntimeError(f"http {self.status_code}")


class _FakeHttpClient:
    def __init__(
        self,
        *,
        post_responses: list[_FakeResponse | Exception] | None = None,
        get_responses: list[_FakeResponse | Exception] | None = None,
    ) -> None:
        self.post_calls: list[dict] = []
        self.get_calls: list[str] = []
        self._post_responses = list(post_responses or [])
        self._get_responses = list(get_responses or [])
        self.closed = False

    async def post(self, url: str, **kwargs):
        self.post_calls.append({"url": url, **kwargs})
        if self._post_responses:
            response = self._post_responses.pop(0)
        else:
            response = _FakeResponse()
        if isinstance(response, Exception):
            raise response
        return response

    async def get(self, url: str):
        self.get_calls.append(url)
        if self._get_responses:
            response = self._get_responses.pop(0)
        else:
            response = _FakeResponse()
        if isinstance(response, Exception):
            raise response
        return response

    async def aclose(self) -> None:
        self.closed = True


class _BlockingTypingHttpClient:
    def __init__(self) -> None:
        self.post_calls: list[dict] = []
        self.started = asyncio.Event()

    async def post(self, url: str, **kwargs):
        self.post_calls.append({"url": url, **kwargs})
        self.started.set()
        await asyncio.Future()


class _FakeWebSocket:
    def __init__(self) -> None:
        self.closed = False
        self.sent: list[str] = []

    async def close(self) -> None:
        self.closed = True

    async def send(self, payload: str) -> None:
        self.sent.append(payload)


def _make_payload(
    *,
    author_id: int = 123,
    author_bot: bool = False,
    channel_id: int = 456,
    message_id: int = 789,
    content: str = "hello",
    guild_id: int | None = None,
    mentions: list[dict] | None = None,
    attachments: list[dict] | None = None,
    reply_to: int | None = None,
) -> dict:
    payload = {
        "author": {"id": author_id, "bot": author_bot},
        "channel_id": str(channel_id),
        "content": content,
        "id": str(message_id),
        "mentions": mentions or [],
        "attachments": attachments or [],
    }
    if guild_id is not None:
        payload["guild_id"] = str(guild_id)
    if reply_to is not None:
        payload["referenced_message"] = {"id": str(reply_to)}
    return payload


@pytest.mark.asyncio
async def test_start_returns_when_token_missing() -> None:
    channel = DiscordChannel(DiscordConfig(enabled=True, allow_from=["*"]), MessageBus())

    await channel.start()

    assert channel.is_running is False
    assert channel._http is None


@pytest.mark.asyncio
async def test_stop_cleans_up_runtime_state() -> None:
    channel = DiscordChannel(
        DiscordConfig(enabled=True, token="token", allow_from=["*"]),
        MessageBus(),
    )
    http = _FakeHttpClient()
    ws = _FakeWebSocket()
    heartbeat = asyncio.create_task(asyncio.sleep(60))
    typing_task = asyncio.create_task(asyncio.sleep(60))

    channel._running = True
    channel._http = http
    channel._ws = ws
    channel._heartbeat_task = heartbeat
    channel._typing_tasks["123"] = typing_task

    await channel.stop()
    await asyncio.sleep(0)

    assert channel.is_running is False
    assert channel._heartbeat_task is None
    assert heartbeat.cancelled() is True
    assert typing_task.cancelled() is True
    assert channel._typing_tasks == {}
    assert ws.closed is True
    assert http.closed is True
    assert channel._ws is None
    assert channel._http is None


@pytest.mark.asyncio
async def test_send_returns_when_http_not_initialized() -> None:
    channel = DiscordChannel(DiscordConfig(enabled=True, allow_from=["*"]), MessageBus())

    await channel.send(OutboundMessage(channel="discord", chat_id="123", content="hello"))

    assert channel._typing_tasks == {}


@pytest.mark.asyncio
async def test_send_stops_typing_after_final_send_but_keeps_progress_typing() -> None:
    channel = DiscordChannel(
        DiscordConfig(enabled=True, token="token", allow_from=["*"]),
        MessageBus(),
    )
    http = _BlockingTypingHttpClient()
    channel._running = True
    channel._http = http

    async def fake_send_payload(url: str, headers: dict[str, str], payload: dict) -> bool:
        return True

    channel._send_payload = fake_send_payload  # type: ignore[method-assign]

    await channel._start_typing("123")
    await http.started.wait()

    await channel.send(
        OutboundMessage(
            channel="discord",
            chat_id="123",
            content="progress",
            metadata={"_progress": True},
        )
    )

    assert "123" in channel._typing_tasks

    await channel.send(OutboundMessage(channel="discord", chat_id="123", content="final"))
    await asyncio.sleep(0)

    assert channel._typing_tasks == {}


@pytest.mark.asyncio
async def test_send_uses_reply_payload_for_text_only_message() -> None:
    channel = DiscordChannel(
        DiscordConfig(enabled=True, token="token", allow_from=["*"]),
        MessageBus(),
    )
    http = _FakeHttpClient(post_responses=[_FakeResponse()])
    channel._http = http

    await channel.send(
        OutboundMessage(
            channel="discord",
            chat_id="123",
            content="hello",
            reply_to="55",
        )
    )

    assert http.post_calls == [
        {
            "url": f"{DISCORD_API_BASE}/channels/123/messages",
            "headers": {"Authorization": "Bot token"},
            "json": {
                "content": "hello",
                "message_reference": {"message_id": "55"},
                "allowed_mentions": {"replied_user": False},
            },
        }
    ]


@pytest.mark.asyncio
async def test_send_emits_attachment_failure_placeholder_when_no_text(tmp_path) -> None:
    channel = DiscordChannel(
        DiscordConfig(enabled=True, token="token", allow_from=["*"]),
        MessageBus(),
    )
    http = _FakeHttpClient(post_responses=[_FakeResponse()])
    channel._http = http

    await channel.send(
        OutboundMessage(
            channel="discord",
            chat_id="123",
            content="",
            media=[str(tmp_path / "missing.txt")],
        )
    )

    assert http.post_calls == [
        {
            "url": f"{DISCORD_API_BASE}/channels/123/messages",
            "headers": {"Authorization": "Bot token"},
            "json": {"content": "[attachment: missing.txt - send failed]"},
        }
    ]


@pytest.mark.asyncio
async def test_send_payload_retries_after_rate_limit(monkeypatch) -> None:
    channel = DiscordChannel(
        DiscordConfig(enabled=True, token="token", allow_from=["*"]),
        MessageBus(),
    )
    http = _FakeHttpClient(
        post_responses=[
            _FakeResponse(status_code=429, json_data={"retry_after": 0.0}),
            _FakeResponse(),
        ]
    )
    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr("nanobot.channels.discord.asyncio.sleep", fake_sleep)
    channel._http = http

    sent = await channel._send_payload(
        f"{DISCORD_API_BASE}/channels/123/messages",
        {"Authorization": "Bot token"},
        {"content": "hello"},
    )

    assert sent is True
    assert len(http.post_calls) == 2
    assert sleeps == [0.0]


@pytest.mark.asyncio
async def test_send_file_includes_reply_payload_json(tmp_path) -> None:
    channel = DiscordChannel(
        DiscordConfig(enabled=True, token="token", allow_from=["*"]),
        MessageBus(),
    )
    http = _FakeHttpClient(post_responses=[_FakeResponse()])
    file_path = tmp_path / "demo.txt"
    file_path.write_text("hi")
    channel._http = http

    sent = await channel._send_file(
        f"{DISCORD_API_BASE}/channels/123/messages",
        {"Authorization": "Bot token"},
        str(file_path),
        reply_to="55",
    )

    assert sent is True
    assert len(http.post_calls) == 1
    assert http.post_calls[0]["files"]["files[0]"][0] == "demo.txt"
    assert json.loads(http.post_calls[0]["data"]["payload_json"]) == {
        "message_reference": {"message_id": "55"},
        "allowed_mentions": {"replied_user": False},
    }


@pytest.mark.asyncio
async def test_handle_message_create_ignores_bot_messages() -> None:
    channel = DiscordChannel(DiscordConfig(enabled=True, allow_from=["*"]), MessageBus())
    handled: list[dict] = []
    channel._handle_message = lambda **kwargs: handled.append(kwargs)  # type: ignore[method-assign]

    await channel._handle_message_create(_make_payload(author_bot=True))

    assert handled == []


@pytest.mark.asyncio
async def test_handle_message_create_accepts_allowlisted_dm() -> None:
    channel = DiscordChannel(DiscordConfig(enabled=True, allow_from=["123"]), MessageBus())
    handled: list[dict] = []

    async def capture_handle(**kwargs) -> None:
        handled.append(kwargs)

    channel._handle_message = capture_handle  # type: ignore[method-assign]
    channel._start_typing = lambda _channel_id: asyncio.sleep(0)  # type: ignore[method-assign]

    await channel._handle_message_create(_make_payload(author_id=123, channel_id=456, message_id=789))

    assert handled == [
        {
            "sender_id": "123",
            "chat_id": "456",
            "content": "hello",
            "media": [],
            "metadata": {"message_id": "789", "guild_id": None, "reply_to": None},
        }
    ]


@pytest.mark.asyncio
async def test_handle_message_create_ignores_unmentioned_guild_message() -> None:
    channel = DiscordChannel(
        DiscordConfig(enabled=True, allow_from=["*"], group_policy="mention"),
        MessageBus(),
    )
    handled: list[dict] = []

    async def capture_handle(**kwargs) -> None:
        handled.append(kwargs)

    channel._bot_user_id = "999"
    channel._handle_message = capture_handle  # type: ignore[method-assign]
    channel._start_typing = lambda _channel_id: asyncio.sleep(0)  # type: ignore[method-assign]

    await channel._handle_message_create(_make_payload(guild_id=1, content="hello everyone"))

    assert handled == []


@pytest.mark.asyncio
async def test_handle_message_create_accepts_mentioned_guild_message() -> None:
    channel = DiscordChannel(
        DiscordConfig(enabled=True, allow_from=["*"], group_policy="mention"),
        MessageBus(),
    )
    handled: list[dict] = []

    async def capture_handle(**kwargs) -> None:
        handled.append(kwargs)

    channel._bot_user_id = "999"
    channel._handle_message = capture_handle  # type: ignore[method-assign]
    channel._start_typing = lambda _channel_id: asyncio.sleep(0)  # type: ignore[method-assign]

    await channel._handle_message_create(
        _make_payload(
            guild_id=1,
            content="<@999> hello",
            mentions=[{"id": "999"}],
            reply_to=321,
        )
    )

    assert handled[0]["metadata"]["reply_to"] == "321"


@pytest.mark.asyncio
async def test_handle_message_create_downloads_attachments(tmp_path, monkeypatch) -> None:
    channel = DiscordChannel(DiscordConfig(enabled=True, allow_from=["*"]), MessageBus())
    handled: list[dict] = []
    http = _FakeHttpClient(get_responses=[_FakeResponse(content=b"attachment")])

    async def capture_handle(**kwargs) -> None:
        handled.append(kwargs)

    channel._handle_message = capture_handle  # type: ignore[method-assign]
    channel._start_typing = lambda _channel_id: asyncio.sleep(0)  # type: ignore[method-assign]
    channel._http = http
    monkeypatch.setattr("nanobot.channels.discord.get_media_dir", lambda _name: tmp_path)

    await channel._handle_message_create(
        _make_payload(
            attachments=[
                {
                    "id": "12",
                    "filename": "photo.png",
                    "size": 1,
                    "url": "https://example.invalid/photo.png",
                }
            ],
            content="see file",
        )
    )

    assert handled[0]["media"] == [str(tmp_path / "12_photo.png")]
    assert "[attachment:" in handled[0]["content"]
    assert http.get_calls == ["https://example.invalid/photo.png"]


@pytest.mark.asyncio
async def test_handle_message_create_marks_failed_attachment_download(
    tmp_path, monkeypatch
) -> None:
    channel = DiscordChannel(DiscordConfig(enabled=True, allow_from=["*"]), MessageBus())
    handled: list[dict] = []
    http = _FakeHttpClient(get_responses=[RuntimeError("boom")])

    async def capture_handle(**kwargs) -> None:
        handled.append(kwargs)

    channel._handle_message = capture_handle  # type: ignore[method-assign]
    channel._start_typing = lambda _channel_id: asyncio.sleep(0)  # type: ignore[method-assign]
    channel._http = http
    monkeypatch.setattr("nanobot.channels.discord.get_media_dir", lambda _name: tmp_path)

    await channel._handle_message_create(
        _make_payload(
            attachments=[
                {
                    "id": "12",
                    "filename": "photo.png",
                    "size": 1,
                    "url": "https://example.invalid/photo.png",
                }
            ],
            content="",
        )
    )

    assert handled[0]["media"] == []
    assert handled[0]["content"] == "[attachment: photo.png - download failed]"


@pytest.mark.asyncio
async def test_handle_message_create_stops_typing_on_handler_error() -> None:
    channel = DiscordChannel(
        DiscordConfig(enabled=True, token="token", allow_from=["*"]),
        MessageBus(),
    )
    http = _BlockingTypingHttpClient()
    channel._running = True
    channel._http = http

    async def fail_handle(**kwargs) -> None:
        await http.started.wait()
        raise RuntimeError("boom")

    channel._handle_message = fail_handle  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="boom"):
        await channel._handle_message_create(_make_payload(author_id=123, channel_id=456))

    await asyncio.sleep(0)
    assert channel._typing_tasks == {}


@pytest.mark.asyncio
async def test_start_typing_posts_until_stopped() -> None:
    channel = DiscordChannel(
        DiscordConfig(enabled=True, token="token", allow_from=["*"]),
        MessageBus(),
    )
    http = _BlockingTypingHttpClient()
    channel._running = True
    channel._http = http

    await channel._start_typing("123")
    await http.started.wait()

    assert "123" in channel._typing_tasks
    assert http.post_calls[0]["url"] == f"{DISCORD_API_BASE}/channels/123/typing"

    await channel._stop_typing("123")
    await asyncio.sleep(0)

    assert channel._typing_tasks == {}


@pytest.mark.asyncio
async def test_identify_sends_expected_gateway_payload() -> None:
    channel = DiscordChannel(
        DiscordConfig(enabled=True, token="token", allow_from=["*"], intents=42),
        MessageBus(),
    )
    ws = _FakeWebSocket()
    channel._ws = ws

    await channel._identify()

    assert json.loads(ws.sent[0]) == {
        "op": 2,
        "d": {
            "token": "token",
            "intents": 42,
            "properties": {
                "os": "nanobot",
                "browser": "nanobot",
                "device": "nanobot",
            },
        },
    }
