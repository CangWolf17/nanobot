"""Tests for /restart slash command."""

from __future__ import annotations

import asyncio
import os
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nanobot.bus.events import InboundMessage, OutboundMessage
from nanobot.providers.base import LLMResponse


def _make_loop():
    """Create a minimal AgentLoop with mocked dependencies."""
    from nanobot.agent.loop import AgentLoop
    from nanobot.bus.queue import MessageBus

    bus = MessageBus()
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    workspace = MagicMock()
    workspace.__truediv__ = MagicMock(return_value=MagicMock())

    with (
        patch("nanobot.agent.loop.ContextBuilder"),
        patch("nanobot.agent.loop.SessionManager"),
        patch("nanobot.agent.loop.SubagentManager"),
    ):
        loop = AgentLoop(bus=bus, provider=provider, workspace=workspace)
    return loop, bus


class TestRestartCommand:
    @pytest.mark.asyncio
    async def test_restart_sends_message_and_calls_execv(self):
        from nanobot.command.builtin import cmd_restart
        from nanobot.command.router import CommandContext
        from nanobot.utils.restart import (
            RESTART_NOTIFY_CHANNEL_ENV,
            RESTART_NOTIFY_CHAT_ID_ENV,
            RESTART_STARTED_AT_ENV,
        )

        loop, bus = _make_loop()
        msg = InboundMessage(channel="cli", sender_id="user", chat_id="direct", content="/restart")
        ctx = CommandContext(msg=msg, session=None, key=msg.session_key, raw="/restart", loop=loop)

        with patch.dict(os.environ, {}, clear=False), \
             patch("nanobot.command.builtin.os.execv") as mock_execv:
            out = await cmd_restart(ctx)
            assert "Restarting" in out.content
            assert os.environ.get(RESTART_NOTIFY_CHANNEL_ENV) == "cli"
            assert os.environ.get(RESTART_NOTIFY_CHAT_ID_ENV) == "direct"
            assert os.environ.get(RESTART_STARTED_AT_ENV)

            await asyncio.sleep(1.5)
            mock_execv.assert_called_once()

    @pytest.mark.asyncio
    async def test_restart_intercepted_in_run_loop(self):
        """Verify /restart is handled at the run-loop level, not inside _dispatch."""
        loop, bus = _make_loop()
        msg = InboundMessage(channel="telegram", sender_id="u1", chat_id="c1", content="/restart")

        with (
            patch.object(loop, "_dispatch", new_callable=AsyncMock) as mock_dispatch,
            patch("nanobot.command.builtin.os.execv"),
        ):
            await bus.publish_inbound(msg)

            loop._running = True
            run_task = asyncio.create_task(loop.run())
            await asyncio.sleep(0.1)
            loop._running = False
            run_task.cancel()
            try:
                await run_task
            except asyncio.CancelledError:
                pass

            mock_dispatch.assert_not_called()
            out = await asyncio.wait_for(bus.consume_outbound(), timeout=1.0)
            assert "Restarting" in out.content

    @pytest.mark.asyncio
    async def test_status_intercepted_in_run_loop(self):
        """Verify /status is handled at the run-loop level for immediate replies."""
        loop, bus = _make_loop()
        msg = InboundMessage(channel="telegram", sender_id="u1", chat_id="c1", content="/status")

        with patch.object(loop, "_dispatch", new_callable=AsyncMock) as mock_dispatch:
            await bus.publish_inbound(msg)

            loop._running = True
            run_task = asyncio.create_task(loop.run())
            await asyncio.sleep(0.1)
            loop._running = False
            run_task.cancel()
            try:
                await run_task
            except asyncio.CancelledError:
                pass

            mock_dispatch.assert_not_called()
            out = await asyncio.wait_for(bus.consume_outbound(), timeout=1.0)
            assert "nanobot" in out.content.lower() or "Model" in out.content

    @pytest.mark.asyncio
    async def test_run_propagates_external_cancellation(self):
        """External task cancellation should not be swallowed by the inbound wait loop."""
        loop, _bus = _make_loop()

        run_task = asyncio.create_task(loop.run())
        await asyncio.sleep(0.1)
        run_task.cancel()

        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(run_task, timeout=1.0)

    def test_help_falls_back_to_shared_builtin_help_text_when_workspace_help_unavailable(
        self, tmp_path
    ):
        from nanobot.command.builtin import cmd_help, build_help_text
        from nanobot.command.router import CommandContext

        async def run() -> None:
            loop, _bus = _make_loop()
            msg = InboundMessage(channel="telegram", sender_id="u1", chat_id="c1", content="/help")
            ctx = CommandContext(msg=msg, session=None, key=msg.session_key, raw="/help", loop=loop)

            with patch("nanobot.command.builtin.Path.home", return_value=tmp_path):
                response = await cmd_help(ctx)

            assert response is not None
            assert response.content == build_help_text()
            assert response.metadata == {"render_as": "text"}

        asyncio.run(run())

    @pytest.mark.asyncio
    async def test_status_reports_runtime_info(self):
        loop, _bus = _make_loop()
        session = MagicMock()
        session.get_history.return_value = [{"role": "user"}] * 3
        loop.sessions.get_or_create.return_value = session
        loop._start_time = time.time() - 125
        loop._last_usage = {"prompt_tokens": 0, "completion_tokens": 0}
        loop.consolidator.estimate_session_prompt_tokens = MagicMock(
            return_value=(20500, "tiktoken")
        )

        msg = InboundMessage(channel="telegram", sender_id="u1", chat_id="c1", content="/status")

        response = await loop._process_message(msg)

        assert response is not None
        assert "Model: test-model" in response.content
        assert "Tokens: 0 in / 0 out" in response.content
        assert "Context: 20k/65k (31%)" in response.content
        assert "Session: 3 messages" in response.content
        assert "Uptime: 2m 5s" in response.content
        assert response.metadata == {"render_as": "text"}

    @pytest.mark.asyncio
    async def test_status_prefers_workspace_harness_interrupt_summary_when_available(self):
        loop, _bus = _make_loop()
        session = MagicMock()
        session.metadata = {"interrupt_state": {"summary": "session interrupt summary"}}
        session.get_history.return_value = [{"role": "user"}] * 3
        loop.sessions.get_or_create.return_value = session
        loop._start_time = time.time() - 125
        loop._last_usage = {"prompt_tokens": 0, "completion_tokens": 0}
        loop.memory_consolidator.estimate_session_prompt_tokens = MagicMock(
            return_value=(20500, "tiktoken")
        )

        msg = InboundMessage(channel="telegram", sender_id="u1", chat_id="c1", content="/status")
        with patch(
            "nanobot.command.builtin._read_workspace_harness_status_summary",
            return_value="harness interrupted / awaiting redirect",
        ):
            response = await loop._process_message(msg)

        assert response is not None
        assert "Interrupt: harness interrupted / awaiting redirect" in response.content
        assert "session interrupt summary" not in response.content

    @pytest.mark.asyncio
    async def test_status_falls_back_to_session_interrupt_summary_when_workspace_harness_missing(
        self,
    ):
        loop, _bus = _make_loop()
        session = MagicMock()
        session.metadata = {"interrupt_state": {"summary": "interrupted — waiting for redirect"}}
        session.get_history.return_value = [{"role": "user"}] * 3
        loop.sessions.get_or_create.return_value = session
        loop._start_time = time.time() - 125
        loop._last_usage = {"prompt_tokens": 0, "completion_tokens": 0}
        loop.memory_consolidator.estimate_session_prompt_tokens = MagicMock(
            return_value=(20500, "tiktoken")
        )

        msg = InboundMessage(channel="telegram", sender_id="u1", chat_id="c1", content="/status")
        with patch(
            "nanobot.command.builtin._read_workspace_harness_status_summary", return_value=None
        ):
            response = await loop._process_message(msg)

        assert response is not None
        assert "Interrupt: interrupted — waiting for redirect" in response.content

    @pytest.mark.asyncio
    async def test_run_agent_loop_resets_usage_when_provider_omits_it(self):
        loop, _bus = _make_loop()
        loop.provider.chat_with_retry = AsyncMock(
            side_effect=[
                LLMResponse(content="first", usage={"prompt_tokens": 9, "completion_tokens": 4}),
                LLMResponse(content="second", usage={}),
            ]
        )

        await loop._run_agent_loop([])
        assert loop._last_usage == {"prompt_tokens": 9, "completion_tokens": 4}

        await loop._run_agent_loop([])
        assert loop._last_usage == {"prompt_tokens": 0, "completion_tokens": 0}

    @pytest.mark.asyncio
    async def test_status_falls_back_to_last_usage_when_context_estimate_missing(self):
        loop, _bus = _make_loop()
        session = MagicMock()
        session.get_history.return_value = [{"role": "user"}]
        loop.sessions.get_or_create.return_value = session
        loop._last_usage = {"prompt_tokens": 1200, "completion_tokens": 34}
        loop.consolidator.estimate_session_prompt_tokens = MagicMock(
            return_value=(0, "none")
        )

        response = await loop._process_message(
            InboundMessage(channel="telegram", sender_id="u1", chat_id="c1", content="/status")
        )

        assert response is not None
        assert "Tokens: 1200 in / 34 out" in response.content
        assert "Context: 1k/65k (1%)" in response.content

    @pytest.mark.asyncio
    async def test_normal_message_clears_interrupt_state_before_agent_run(self):
        loop, _bus = _make_loop()
        session = MagicMock()
        session.metadata = {"interrupt_state": {"summary": "interrupted — waiting for redirect"}}
        session.get_history.return_value = []
        loop.sessions.get_or_create.return_value = session
        loop._maybe_run_pre_reply_consolidation = AsyncMock(return_value=True)
        loop._select_history_for_reply = MagicMock(return_value=[])
        loop.context.build_messages = MagicMock(return_value=[])
        loop._run_agent_loop = AsyncMock(return_value=("done", None, []))
        loop._save_turn = MagicMock()
        loop.sessions.save = MagicMock()
        loop._schedule_background = lambda coro: coro.close()
        loop.tools.get = MagicMock(return_value=None)
        redirect_service = MagicMock()
        session.metadata["interrupt_state"]["session_key"] = "telegram:c1"
        session.metadata["interrupt_state"]["workspace_harness_id"] = "har_0001"

        with patch(
            "nanobot.agent.loop.HarnessService.for_workspace", return_value=redirect_service
        ):
            response = await loop._process_message(
                InboundMessage(
                    channel="telegram",
                    sender_id="u1",
                    chat_id="c1",
                    content="继续改成 interrupt 语义",
                )
            )

        assert response is not None
        assert response.content == "done"
        assert "interrupt_state" not in session.metadata
        loop.sessions.save.assert_called()
        redirect_service.redirect_after_interrupt.assert_called_once_with(
            "继续改成 interrupt 语义",
            session_key="telegram:c1",
            harness_id="har_0001",
        )

    @pytest.mark.asyncio
    async def test_process_direct_preserves_render_metadata(self):
        loop, _bus = _make_loop()
        session = MagicMock()
        session.get_history.return_value = []
        loop.sessions.get_or_create.return_value = session
        loop.subagents.get_running_count.return_value = 0

        response = await loop.process_direct("/status", session_key="cli:test")

        assert response is not None
        assert response.metadata == {"render_as": "text"}
