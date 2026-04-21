"""Tests for restart notice delivery in ChannelManager."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from nanobot.bus.events import OutboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.channels.manager import ChannelManager
from nanobot.config.schema import Config
from nanobot.utils.restart import RestartNotice


@pytest.mark.asyncio
async def test_restart_notice_uses_interactive_render_metadata(monkeypatch) -> None:
    manager = ChannelManager(Config(), MessageBus())
    target = object()
    manager.channels["feishu"] = target
    manager._send_with_retry = AsyncMock()

    monkeypatch.setattr(
        "nanobot.channels.manager.consume_restart_notice_from_env",
        lambda: RestartNotice(
            channel="feishu",
            chat_id="oc_restart",
            started_at_raw="100.0",
        ),
    )
    monkeypatch.setattr(
        "nanobot.channels.manager.format_restart_completed_message",
        lambda _started_at_raw: "Restart completed in 2.0s.",
    )

    manager._notify_restart_done_if_needed()
    await asyncio.sleep(0)

    manager._send_with_retry.assert_awaited_once_with(
        target,
        OutboundMessage(
            channel="feishu",
            chat_id="oc_restart",
            content="Restart completed in 2.0s.",
            metadata={"render_as": "interactive"},
        ),
    )
