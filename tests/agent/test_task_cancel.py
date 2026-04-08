"""Tests for /stop task cancellation."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_loop(*, exec_config=None):
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
        patch("nanobot.agent.loop.SubagentManager") as mock_sub_mgr,
    ):
        mock_sub_mgr.return_value.cancel_by_session = AsyncMock(return_value=0)
        loop = AgentLoop(bus=bus, provider=provider, workspace=workspace, exec_config=exec_config)
    return loop, bus


class TestHandleStop:
    @pytest.mark.asyncio
    async def test_interrupt_no_active_task(self):
        from nanobot.bus.events import InboundMessage
        from nanobot.command.builtin import cmd_interrupt
        from nanobot.command.router import CommandContext

        loop, bus = _make_loop()
        msg = InboundMessage(channel="test", sender_id="u1", chat_id="c1", content="/interrupt")
        ctx = CommandContext(
            msg=msg, session=None, key=msg.session_key, raw="/interrupt", loop=loop
        )
        out = await cmd_interrupt(ctx)
        assert "No active task" in out.content

    @pytest.mark.asyncio
    async def test_interrupt_cancels_active_task(self):
        from nanobot.bus.events import InboundMessage
        from nanobot.command.builtin import cmd_interrupt
        from nanobot.command.router import CommandContext

        loop, bus = _make_loop()
        cancelled = asyncio.Event()

        async def slow_task():
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                cancelled.set()
                raise

        task = asyncio.create_task(slow_task())
        await asyncio.sleep(0)
        loop._active_tasks["test:c1"] = [task]

        msg = InboundMessage(channel="test", sender_id="u1", chat_id="c1", content="/interrupt")
        ctx = CommandContext(
            msg=msg, session=None, key=msg.session_key, raw="/interrupt", loop=loop
        )
        out = await cmd_interrupt(ctx)

        assert cancelled.is_set()
        assert "interrupted" in out.content.lower()

    @pytest.mark.asyncio
    async def test_interrupt_sets_session_interrupt_metadata(self):
        from nanobot.bus.events import InboundMessage
        from nanobot.command.builtin import cmd_interrupt
        from nanobot.command.router import CommandContext

        loop, bus = _make_loop()
        session = MagicMock()
        session.metadata = {}
        loop.sessions.get_or_create.return_value = session
        cancelled = asyncio.Event()

        async def slow_task():
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                cancelled.set()
                raise

        task = asyncio.create_task(slow_task())
        await asyncio.sleep(0)
        loop._active_tasks["test:c1"] = [task]

        msg = InboundMessage(channel="test", sender_id="u1", chat_id="c1", content="/interrupt")
        ctx = CommandContext(
            msg=msg, session=None, key=msg.session_key, raw="/interrupt", loop=loop
        )
        out = await cmd_interrupt(ctx)

        assert cancelled.is_set()
        assert session.metadata["interrupt_state"]["status"] == "interrupted"
        assert session.metadata["interrupt_state"]["reason"] == "user_interrupt"
        assert session.metadata["interrupt_state"]["session_key"] == "test:c1"
        assert "redirect" in out.content.lower()

    @pytest.mark.asyncio
    async def test_interrupt_persists_inflight_partial_turn_to_session_history(self):
        from nanobot.bus.events import InboundMessage
        from nanobot.command.builtin import cmd_interrupt
        from nanobot.command.router import CommandContext
        from nanobot.session.manager import Session

        loop, _bus = _make_loop()
        session = Session(key="test:c1")
        loop.sessions.get_or_create.return_value = session
        loop.sessions.save = MagicMock()
        cancelled = asyncio.Event()

        async def slow_task():
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                cancelled.set()
                raise

        task = asyncio.create_task(slow_task())
        await asyncio.sleep(0)
        loop._active_tasks["test:c1"] = [task]
        loop._inflight_turns["test:c1"] = {
            "role": "user",
            "content": "/harness 修复 interrupt 的真实接线",
            "assistant_partial": "已经输出到一半：先把 partial assistant 写入 session history。",
        }

        msg = InboundMessage(channel="test", sender_id="u1", chat_id="c1", content="/interrupt")
        ctx = CommandContext(
            msg=msg, session=None, key=msg.session_key, raw="/interrupt", loop=loop
        )
        out = await cmd_interrupt(ctx)

        assert cancelled.is_set()
        assert "interrupted" in out.content.lower()
        assert session.messages[-2]["role"] == "user"
        assert session.messages[-2]["content"] == "/harness 修复 interrupt 的真实接线"
        assert session.messages[-1]["role"] == "assistant"
        assert "partial assistant" in session.messages[-1]["content"]
        assert "partial_assistant_preview" in session.metadata["interrupt_state"]
        assert "test:c1" not in loop._inflight_turns

    @pytest.mark.asyncio
    async def test_interrupt_preserved_partial_output_is_available_to_next_message_history(self):
        from nanobot.bus.events import InboundMessage
        from nanobot.command.builtin import cmd_interrupt
        from nanobot.command.router import CommandContext
        from nanobot.session.manager import Session

        loop, _bus = _make_loop()
        session = Session(key="test:c1")
        loop.sessions.get_or_create.return_value = session
        loop.sessions.save = MagicMock()
        cancelled = asyncio.Event()

        async def slow_task():
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                cancelled.set()
                raise

        task = asyncio.create_task(slow_task())
        await asyncio.sleep(0)
        loop._active_tasks["test:c1"] = [task]
        partial = "已经输出到一半：先把 partial assistant 写入 session history。"
        loop._inflight_turns["test:c1"] = {
            "role": "user",
            "content": "/harness 修复 interrupt 的真实接线",
            "assistant_partial": partial,
        }

        interrupt_msg = InboundMessage(
            channel="test", sender_id="u1", chat_id="c1", content="/interrupt"
        )
        interrupt_ctx = CommandContext(
            msg=interrupt_msg,
            session=None,
            key=interrupt_msg.session_key,
            raw="/interrupt",
            loop=loop,
        )
        await cmd_interrupt(interrupt_ctx)

        captured = {}
        loop._maybe_run_pre_reply_consolidation = AsyncMock(return_value=True)
        loop._select_history_for_reply = MagicMock(
            side_effect=lambda s, preflight_ok=True: s.get_history(max_messages=0)
        )

        def _build_messages(*, history, current_message, **kwargs):
            captured["history"] = history
            return list(history) + [{"role": "user", "content": current_message}]

        async def _run_agent_loop(messages, **kwargs):
            return "done", [], list(messages) + [{"role": "assistant", "content": "done"}]

        loop.context.build_messages = MagicMock(side_effect=_build_messages)
        loop._run_agent_loop = _run_agent_loop  # type: ignore[method-assign]
        loop._schedule_background = lambda coro: coro.close()
        loop.tools.get = MagicMock(return_value=None)

        response = await loop._process_message(
            InboundMessage(channel="test", sender_id="u1", chat_id="c1", content="按这个继续")
        )

        assert cancelled.is_set()
        assert response is not None
        assert response.content == "done"
        assert any(
            item.get("role") == "assistant" and partial in str(item.get("content") or "")
            for item in captured["history"]
        )

    @pytest.mark.asyncio
    async def test_interrupt_updates_workspace_harness_state_when_active_task_exists(
        self, tmp_path
    ):
        from nanobot.bus.events import InboundMessage
        from nanobot.command.builtin import cmd_interrupt
        from nanobot.command.router import CommandContext
        from nanobot.harness.service import HarnessService

        loop, _bus = _make_loop()
        loop.workspace = tmp_path / ".nanobot" / "workspace"
        session = MagicMock()
        session.metadata = {}
        loop.sessions.get_or_create.return_value = session
        cancelled = asyncio.Event()

        async def slow_task():
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                cancelled.set()
                raise

        task = asyncio.create_task(slow_task())
        await asyncio.sleep(0)
        loop._active_tasks["test:c1"] = [task]
        service = HarnessService.for_workspace(loop.workspace)
        service.handle_command(
            "/harness 修复 interrupt 的真实接线",
            session_key="test:c1",
            sender_id="u1",
        )

        msg = InboundMessage(channel="test", sender_id="u1", chat_id="c1", content="/interrupt")
        ctx = CommandContext(
            msg=msg, session=None, key=msg.session_key, raw="/interrupt", loop=loop
        )

        out = await cmd_interrupt(ctx)

        assert cancelled.is_set()
        assert "interrupted" in out.content.lower()
        assert "interrupted" in service.render_status().lower()
        assert session.metadata["interrupt_state"]["workspace_harness_id"]

    @pytest.mark.asyncio
    async def test_interrupt_skips_workspace_harness_update_when_no_active_task(self, tmp_path):
        from nanobot.bus.events import InboundMessage
        from nanobot.command.builtin import cmd_interrupt
        from nanobot.command.router import CommandContext

        loop, _bus = _make_loop()
        msg = InboundMessage(channel="test", sender_id="u1", chat_id="c1", content="/interrupt")
        ctx = CommandContext(
            msg=msg, session=None, key=msg.session_key, raw="/interrupt", loop=loop
        )

        with (
            patch("nanobot.command.builtin.Path.home", return_value=tmp_path),
            patch("nanobot.command.builtin.subprocess.run") as mock_run,
        ):
            out = await cmd_interrupt(ctx)

        assert "No active task" in out.content
        mock_run.assert_not_called()

    @pytest.mark.asyncio
    async def test_stop_does_not_set_interrupt_metadata(self):
        from nanobot.bus.events import InboundMessage
        from nanobot.command.builtin import cmd_stop
        from nanobot.command.router import CommandContext

        loop, bus = _make_loop()
        session = MagicMock()
        session.metadata = {}
        loop.sessions.get_or_create.return_value = session
        cancelled = asyncio.Event()

        async def slow_task():
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                cancelled.set()
                raise

        task = asyncio.create_task(slow_task())
        await asyncio.sleep(0)
        loop._active_tasks["test:c1"] = [task]

        msg = InboundMessage(channel="test", sender_id="u1", chat_id="c1", content="/stop")
        ctx = CommandContext(msg=msg, session=None, key=msg.session_key, raw="/stop", loop=loop)
        await cmd_stop(ctx)

        assert cancelled.is_set()
        assert "interrupt_state" not in session.metadata

    @pytest.mark.asyncio
    async def test_new_does_not_touch_workspace_harness_durable_truth(self, tmp_path):
        from nanobot.bus.events import InboundMessage
        from nanobot.command.builtin import cmd_new
        from nanobot.command.router import CommandContext

        loop, _bus = _make_loop()
        session = MagicMock()
        session.messages = []
        session.last_consolidated = 0
        session.metadata = {"interrupt_state": {"status": "interrupted"}}
        session.clear = MagicMock()
        loop.sessions.get_or_create.return_value = session

        msg = InboundMessage(channel="test", sender_id="u1", chat_id="c1", content="/new")
        ctx = CommandContext(msg=msg, session=session, key=msg.session_key, raw="/new", loop=loop)

        with patch("nanobot.command.builtin.subprocess.run") as mock_run:
            out = await cmd_new(ctx)

        assert "interrupt_state" not in session.metadata
        assert "new session started" in out.content.lower()
        mock_run.assert_not_called()

    @pytest.mark.asyncio
    async def test_stop_no_active_task(self):
        from nanobot.bus.events import InboundMessage
        from nanobot.command.builtin import cmd_stop
        from nanobot.command.router import CommandContext

        loop, bus = _make_loop()
        msg = InboundMessage(channel="test", sender_id="u1", chat_id="c1", content="/stop")
        ctx = CommandContext(msg=msg, session=None, key=msg.session_key, raw="/stop", loop=loop)
        out = await cmd_stop(ctx)
        assert "No active task" in out.content

    @pytest.mark.asyncio
    async def test_stop_cancels_active_task(self):
        from nanobot.bus.events import InboundMessage
        from nanobot.command.builtin import cmd_stop
        from nanobot.command.router import CommandContext

        loop, bus = _make_loop()
        cancelled = asyncio.Event()

        async def slow_task():
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                cancelled.set()
                raise

        task = asyncio.create_task(slow_task())
        await asyncio.sleep(0)
        loop._active_tasks["test:c1"] = [task]

        msg = InboundMessage(channel="test", sender_id="u1", chat_id="c1", content="/stop")
        ctx = CommandContext(msg=msg, session=None, key=msg.session_key, raw="/stop", loop=loop)
        out = await cmd_stop(ctx)

        assert cancelled.is_set()
        assert "stopped" in out.content.lower()

    @pytest.mark.asyncio
    async def test_stop_cancels_multiple_tasks(self):
        from nanobot.bus.events import InboundMessage
        from nanobot.command.builtin import cmd_stop
        from nanobot.command.router import CommandContext

        loop, bus = _make_loop()
        events = [asyncio.Event(), asyncio.Event()]

        async def slow(idx):
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                events[idx].set()
                raise

        tasks = [asyncio.create_task(slow(i)) for i in range(2)]
        await asyncio.sleep(0)
        loop._active_tasks["test:c1"] = tasks

        msg = InboundMessage(channel="test", sender_id="u1", chat_id="c1", content="/stop")
        ctx = CommandContext(msg=msg, session=None, key=msg.session_key, raw="/stop", loop=loop)
        out = await cmd_stop(ctx)

        assert all(e.is_set() for e in events)
        assert "2 task" in out.content


class TestDispatch:
    def test_register_builtin_commands_marks_interrupt_as_priority(self):
        from nanobot.command import CommandRouter, register_builtin_commands

        router = CommandRouter()
        register_builtin_commands(router)

        assert router.is_priority("/interrupt") is True

    def test_exec_tool_not_registered_when_disabled(self):
        from nanobot.config.schema import ExecToolConfig

        loop, _bus = _make_loop(exec_config=ExecToolConfig(enable=False))

        assert loop.tools.get("exec") is None

    @pytest.mark.asyncio
    async def test_dispatch_processes_and_publishes(self):
        from nanobot.bus.events import InboundMessage, OutboundMessage

        loop, bus = _make_loop()
        msg = InboundMessage(channel="test", sender_id="u1", chat_id="c1", content="hello")
        loop._process_message = AsyncMock(
            return_value=OutboundMessage(channel="test", chat_id="c1", content="hi")
        )
        await loop._dispatch(msg)
        out = await asyncio.wait_for(bus.consume_outbound(), timeout=1.0)
        assert out.content == "hi"

    @pytest.mark.asyncio
    async def test_processing_lock_serializes(self):
        from nanobot.bus.events import InboundMessage, OutboundMessage

        loop, bus = _make_loop()
        order = []

        async def mock_process(m, **kwargs):
            order.append(f"start-{m.content}")
            await asyncio.sleep(0.05)
            order.append(f"end-{m.content}")
            return OutboundMessage(channel="test", chat_id="c1", content=m.content)

        loop._process_message = mock_process
        msg1 = InboundMessage(channel="test", sender_id="u1", chat_id="c1", content="a")
        msg2 = InboundMessage(channel="test", sender_id="u1", chat_id="c1", content="b")

        t1 = asyncio.create_task(loop._dispatch(msg1))
        t2 = asyncio.create_task(loop._dispatch(msg2))
        await asyncio.gather(t1, t2)
        assert order == ["start-a", "end-a", "start-b", "end-b"]


class TestSubagentCancellation:
    @pytest.mark.asyncio
    async def test_cancel_by_session(self):
        from nanobot.agent.subagent import SubagentManager
        from nanobot.bus.queue import MessageBus

        bus = MessageBus()
        provider = MagicMock()
        provider.get_default_model.return_value = "test-model"
        mgr = SubagentManager(provider=provider, workspace=MagicMock(), bus=bus)

        cancelled = asyncio.Event()

        async def slow():
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                cancelled.set()
                raise

        task = asyncio.create_task(slow())
        await asyncio.sleep(0)
        mgr._running_tasks["sub-1"] = task
        mgr._session_tasks["test:c1"] = {"sub-1"}

        count = await mgr.cancel_by_session("test:c1")
        assert count == 1
        assert cancelled.is_set()

    @pytest.mark.asyncio
    async def test_cancel_by_session_no_tasks(self):
        from nanobot.agent.subagent import SubagentManager
        from nanobot.bus.queue import MessageBus

        bus = MessageBus()
        provider = MagicMock()
        provider.get_default_model.return_value = "test-model"
        mgr = SubagentManager(provider=provider, workspace=MagicMock(), bus=bus)
        assert await mgr.cancel_by_session("nonexistent") == 0

    @pytest.mark.asyncio
    async def test_subagent_preserves_reasoning_fields_in_tool_turn(self, monkeypatch, tmp_path):
        from nanobot.agent.subagent import SubagentManager
        from nanobot.bus.queue import MessageBus
        from nanobot.providers.base import LLMResponse, ToolCallRequest

        bus = MessageBus()
        provider = MagicMock()
        provider.get_default_model.return_value = "test-model"

        captured_second_call: list[dict] = []

        call_count = {"n": 0}

        async def scripted_chat_with_retry(*, messages, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return LLMResponse(
                    content="thinking",
                    tool_calls=[ToolCallRequest(id="call_1", name="list_dir", arguments={})],
                    reasoning_content="hidden reasoning",
                    thinking_blocks=[{"type": "thinking", "thinking": "step"}],
                )
            captured_second_call[:] = messages
            return LLMResponse(content="done", tool_calls=[])

        provider.chat_with_retry = scripted_chat_with_retry
        mgr = SubagentManager(provider=provider, workspace=tmp_path, bus=bus)

        async def fake_execute(self, name, arguments):
            return "tool result"

        monkeypatch.setattr("nanobot.agent.tools.registry.ToolRegistry.execute", fake_execute)

        await mgr._run_subagent("sub-1", "do task", "label", {"channel": "test", "chat_id": "c1"})

        assistant_messages = [
            msg
            for msg in captured_second_call
            if msg.get("role") == "assistant" and msg.get("tool_calls")
        ]
        assert len(assistant_messages) == 1
        assert assistant_messages[0]["reasoning_content"] == "hidden reasoning"
        assert assistant_messages[0]["thinking_blocks"] == [
            {"type": "thinking", "thinking": "step"}
        ]

    @pytest.mark.asyncio
    async def test_subagent_announces_error_when_tool_execution_fails(self, monkeypatch, tmp_path):
        from nanobot.agent.subagent import SubagentManager
        from nanobot.bus.queue import MessageBus
        from nanobot.providers.base import LLMResponse, ToolCallRequest

        bus = MessageBus()
        provider = MagicMock()
        provider.get_default_model.return_value = "test-model"
        provider.chat_with_retry = AsyncMock(
            return_value=LLMResponse(
                content="thinking",
                tool_calls=[ToolCallRequest(id="call_1", name="list_dir", arguments={})],
            )
        )
        mgr = SubagentManager(provider=provider, workspace=tmp_path, bus=bus)
        mgr._announce_result = AsyncMock()

        calls = {"n": 0}

        async def fake_execute(self, name, arguments):
            calls["n"] += 1
            if calls["n"] == 1:
                return "first result"
            raise RuntimeError("boom")

        monkeypatch.setattr("nanobot.agent.tools.registry.ToolRegistry.execute", fake_execute)

        await mgr._run_subagent("sub-1", "do task", "label", {"channel": "test", "chat_id": "c1"})

        mgr._announce_result.assert_awaited_once()
        args = mgr._announce_result.await_args.args
        assert "Completed steps:" in args[3]
        assert "- list_dir: first result" in args[3]
        assert "Failure:" in args[3]
        assert "- list_dir: boom" in args[3]
        assert args[5] == "error"

    @pytest.mark.asyncio
    async def test_spawn_context_propagates_session_key_and_subagent_inherits_strict_mode(
        self, tmp_path
    ):
        from nanobot.agent.runner import AgentRunResult
        from nanobot.agent.subagent import SubagentManager
        from nanobot.agent.tools.spawn import SpawnTool
        from nanobot.bus.queue import MessageBus

        bus = MessageBus()
        provider = MagicMock()
        provider.get_default_model.return_value = "test-model"

        sessions = tmp_path / "sessions"
        session_root = sessions / "ses_0001"
        session_root.mkdir(parents=True)
        (sessions / "control.json").write_text('{"active_session_id":"ses_0001"}', encoding="utf-8")
        (sessions / "index.json").write_text(
            '{"sessions":{"ses_0001":{"session_root":"' + str(session_root) + '"}}}',
            encoding="utf-8",
        )
        (session_root / "dev_state.json").write_text(
            '{"strict_dev_mode":"enforce","task_kind":"feature","phase":"red_required","work_mode":"build","gates":{"plan":{"required":true,"satisfied":true},"failing_test":{"required":true,"satisfied":false},"verification":{"required":true,"satisfied":false}}}',
            encoding="utf-8",
        )

        mgr = SubagentManager(provider=provider, workspace=tmp_path, bus=bus)
        mgr.runner.run = AsyncMock(
            return_value=AgentRunResult(
                final_content="done",
                messages=[],
                tools_used=[],
                usage={},
            )
        )
        mgr._announce_result = AsyncMock()

        spawn_tool = SpawnTool(manager=mgr)
        spawn_tool.set_context("telegram", "chat-1")
        result = await spawn_tool.execute(task="do task", label="bg")

        assert "started" in result.lower()
        running = list(mgr._running_tasks.values())
        assert len(running) == 1
        await running[0]

        spec = mgr.runner.run.await_args.args[0]
        assert spec.concurrent_tools is False
        assert "## Dev Discipline" in spec.initial_messages[0]["content"]
        assert mgr._session_tasks == {}

    @pytest.mark.asyncio
    async def test_spawn_context_can_preserve_workspace_harness_metadata_for_subagent_completion(
        self, tmp_path
    ):
        from nanobot.agent.runner import AgentRunResult
        from nanobot.agent.subagent import SubagentManager
        from nanobot.agent.tools.spawn import SpawnTool
        from nanobot.bus.queue import MessageBus

        bus = MessageBus()
        provider = MagicMock()
        provider.get_default_model.return_value = "test-model"

        mgr = SubagentManager(provider=provider, workspace=tmp_path, bus=bus)
        mgr.runner.run = AsyncMock(
            return_value=AgentRunResult(
                final_content="done",
                messages=[],
                tools_used=[],
                usage={},
            )
        )

        spawn_tool = SpawnTool(manager=mgr)
        spawn_tool.set_context(
            "feishu",
            "chat-1",
            metadata={
                "workspace_agent_cmd": "harness",
                "workspace_harness_auto": True,
                "_origin_sender_id": "user1",
            },
        )
        result = await spawn_tool.execute(task="do task", label="bg")

        assert "started" in result.lower()
        running = list(mgr._running_tasks.values())
        assert len(running) == 1
        await running[0]

        follow_up = await asyncio.wait_for(bus.consume_inbound(), timeout=1.0)
        assert follow_up.channel == "system"
        assert follow_up.chat_id == "feishu:chat-1"
        assert follow_up.metadata["workspace_agent_cmd"] == "harness"
        assert follow_up.metadata["workspace_harness_auto"] is True
        assert follow_up.metadata["_origin_sender_id"] == "user1"

    @pytest.mark.asyncio
    async def test_spawn_is_hard_blocked_when_active_harness_disallows_subagents(self, tmp_path):
        from nanobot.agent.subagent import SubagentManager
        from nanobot.agent.tools.spawn import SpawnTool
        from nanobot.bus.queue import MessageBus

        bus = MessageBus()
        provider = MagicMock()
        provider.get_default_model.return_value = "test-model"

        mgr = SubagentManager(provider=provider, workspace=tmp_path, bus=bus)
        mgr.spawn = AsyncMock()

        spawn_tool = SpawnTool(manager=mgr)
        spawn_tool.set_context(
            "feishu",
            "chat-1",
            metadata={
                "workspace_agent_cmd": "harness",
                "workspace_runtime": {
                    "has_active_harness": True,
                    "active_harness": {
                        "id": "har_0054",
                        "status": "active",
                        "phase": "executing",
                        "awaiting_user": False,
                        "blocked": False,
                        "auto": False,
                        "subagent_allowed": False,
                    },
                },
            },
        )
        result = await spawn_tool.execute(task="do task", label="bg")

        assert "blocked" in result.lower()
        assert "subagent_allowed=false" in result
        mgr.spawn.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_spawn_acquires_resource_lease_before_starting_background_task(
        self, monkeypatch, tmp_path
    ):
        from nanobot.agent.subagent import SubagentManager
        from nanobot.agent.subagent_resources import AcquireDecision, SubagentLease
        from nanobot.bus.queue import MessageBus

        bus = MessageBus()
        provider = MagicMock()
        provider.get_default_model.return_value = "test-model"
        mgr = SubagentManager(provider=provider, workspace=tmp_path, bus=bus)

        lease = SubagentLease(
            model_id="standard-gpt-5.4-high-aizhiwen-top",
            tier="standard",
            route="aizhiwen-top",
            effort="high",
        )
        resource_manager = MagicMock()
        resource_manager.acquire.return_value = AcquireDecision(status="granted", lease=lease)
        resource_manager.release = MagicMock()
        mgr.resource_manager = resource_manager
        mgr._resolve_subagent_request = MagicMock(return_value=object())
        mgr._run_subagent = AsyncMock()

        result = await mgr.spawn(task="do task", label="bg", session_key="test:c1")

        assert "started" in result.lower()
        resource_manager.acquire.assert_called_once()
        running = list(mgr._running_tasks.values())
        assert len(running) == 1
        await running[0]
        mgr._run_subagent.assert_awaited_once()
        args = mgr._run_subagent.await_args.args
        assert args[4] == lease

    def test_spawn_tool_accepts_optional_tier_and_model_parameters(self):
        from nanobot.agent.tools.spawn import SpawnTool

        mgr = MagicMock()
        tool = SpawnTool(manager=mgr)

        props = tool.parameters["properties"]
        assert "tier" in props
        assert props["tier"]["enum"] == ["lite", "standard"]
        assert "model" in props

    @pytest.mark.asyncio
    async def test_spawn_rejects_when_resource_manager_denies_request(self, tmp_path):
        from nanobot.agent.subagent import SubagentManager
        from nanobot.agent.subagent_resources import AcquireDecision
        from nanobot.bus.queue import MessageBus

        bus = MessageBus()
        provider = MagicMock()
        provider.get_default_model.return_value = "test-model"
        mgr = SubagentManager(provider=provider, workspace=tmp_path, bus=bus)

        resource_manager = MagicMock()
        resource_manager.acquire.return_value = AcquireDecision(
            status="rejected", reason="queue_limit"
        )
        resource_manager.release = MagicMock()
        mgr.resource_manager = resource_manager
        mgr._resolve_subagent_request = MagicMock(return_value=object())
        mgr._run_subagent = AsyncMock()

        result = await mgr.spawn(task="do task", label="bg", session_key="test:c1")

        assert "rejected" in result.lower()
        assert "queue_limit" in result
        mgr._run_subagent.assert_not_awaited()
        assert mgr._running_tasks == {}

    def test_resolve_subagent_request_prefers_explicit_model_then_tier_then_harness_defaults(
        self, tmp_path
    ):
        from nanobot.agent.subagent import SubagentManager
        from nanobot.bus.queue import MessageBus

        bus = MessageBus()
        provider = MagicMock()
        provider.get_default_model.return_value = "gpt-5.4"
        mgr = SubagentManager(provider=provider, workspace=tmp_path, bus=bus)

        request = mgr._resolve_subagent_request(
            task="do task",
            label="bg",
            tier="lite",
            model="lite-minimax-m2.7-high-minimax",
            origin={
                "channel": "feishu",
                "chat_id": "c1",
                "metadata": {
                    "workspace_runtime": {
                        "active_harness": {
                            "id": "har_0026",
                            "subagent_model": "standard-gpt-5.4-high-aizhiwen-top",
                            "subagent_tier": "standard",
                        }
                    }
                },
            },
        )

        assert request.model == "lite-minimax-m2.7-high-minimax"
        assert request.tier == "lite"
        assert request.harness_model == "standard-gpt-5.4-high-aizhiwen-top"
        assert request.harness_tier == "standard"

    def test_build_subagent_prompt_injects_runtime_context_bundle(self, tmp_path):
        from nanobot.agent.subagent import SubagentManager
        from nanobot.bus.queue import MessageBus

        bus = MessageBus()
        provider = MagicMock()
        provider.get_default_model.return_value = "gpt-5.4"
        mgr = SubagentManager(provider=provider, workspace=tmp_path, bus=bus)

        prompt = mgr._build_subagent_prompt(
            origin={
                "channel": "feishu",
                "chat_id": "c1",
                "metadata": {
                    "workspace_runtime": {
                        "work_mode": "build",
                        "has_active_harness": True,
                        "active_harness": {
                            "id": "har_0024",
                            "type": "feature",
                            "status": "active",
                            "phase": "runnable",
                            "awaiting_user": False,
                            "blocked": False,
                            "auto": False,
                        },
                        "main_harness": {
                            "id": "har_0002",
                            "type": "project",
                            "status": "active",
                            "phase": "planning",
                            "has_open_children": True,
                        },
                    }
                },
            }
        )

        assert "## Subagent Execution Context" in prompt
        assert "### Project Context" in prompt
        assert "har_0024" in prompt
        assert "### Today's Context" in prompt
        assert "work_mode: build" in prompt
        assert "### Output Rules" in prompt
        assert "recommend a concrete next step" in prompt.lower()
        assert "### Role Framing" in prompt
        assert "senior engineer" in prompt.lower()

    @pytest.mark.asyncio
    async def test_run_subagent_injects_context_bundle_before_task_text(
        self, monkeypatch, tmp_path
    ):
        from nanobot.agent.runner import AgentRunner, AgentRunResult
        from nanobot.agent.subagent import SubagentManager
        from nanobot.bus.queue import MessageBus

        bus = MessageBus()
        provider = MagicMock()
        provider.get_default_model.return_value = "gpt-5.4"
        mgr = SubagentManager(provider=provider, workspace=tmp_path, bus=bus)
        mgr._announce_result = AsyncMock()

        captured = {}

        async def fake_run(self, spec):
            captured["messages"] = spec.initial_messages
            return AgentRunResult(final_content="done", messages=[], tools_used=[], usage={})

        monkeypatch.setattr(AgentRunner, "run", fake_run)

        await mgr._run_subagent(
            "sub-1",
            "Inspect scripts/subagent.py and report the wiring gap.",
            "inject-check",
            {
                "channel": "feishu",
                "chat_id": "c1",
                "metadata": {
                    "workspace_runtime": {
                        "work_mode": "build",
                        "has_active_harness": True,
                        "active_harness": {
                            "id": "har_0024",
                            "type": "feature",
                            "status": "active",
                            "phase": "runnable",
                            "awaiting_user": False,
                            "blocked": False,
                            "auto": False,
                        },
                    }
                },
            },
            None,
        )

        user_message = captured["messages"][1]["content"]
        assert "## Subagent Execution Context" in user_message
        assert "### Project Context" in user_message
        assert "har_0024" in user_message
        assert "Inspect scripts/subagent.py and report the wiring gap." in user_message

    @pytest.mark.asyncio
    async def test_run_subagent_keeps_manual_task_text_as_additive_override(
        self, monkeypatch, tmp_path
    ):
        from nanobot.agent.runner import AgentRunner, AgentRunResult
        from nanobot.agent.subagent import SubagentManager
        from nanobot.bus.queue import MessageBus

        bus = MessageBus()
        provider = MagicMock()
        provider.get_default_model.return_value = "gpt-5.4"
        mgr = SubagentManager(provider=provider, workspace=tmp_path, bus=bus)
        mgr._announce_result = AsyncMock()

        captured = {}

        async def fake_run(self, spec):
            captured["messages"] = spec.initial_messages
            return AgentRunResult(final_content="done", messages=[], tools_used=[], usage={})

        monkeypatch.setattr(AgentRunner, "run", fake_run)

        await mgr._run_subagent(
            "sub-2",
            "Extra caller note: focus only on spawn metadata passthrough.",
            "inject-additive",
            {"channel": "feishu", "chat_id": "c1", "metadata": {}},
            None,
        )

        user_message = captured["messages"][1]["content"]
        assert "## Subagent Execution Context" in user_message
        assert "Extra caller note: focus only on spawn metadata passthrough." in user_message

    @pytest.mark.asyncio
    async def test_run_subagent_rebuilds_provider_from_lease_and_uses_provider_model(
        self, monkeypatch, tmp_path
    ):
        from nanobot.agent.runner import AgentRunner, AgentRunResult
        from nanobot.agent.subagent import SubagentManager
        from nanobot.agent.subagent_resources import SubagentLease
        from nanobot.bus.queue import MessageBus

        bus = MessageBus()
        parent_provider = MagicMock()
        parent_provider.get_default_model.return_value = "parent-model"
        mgr = SubagentManager(provider=parent_provider, workspace=tmp_path, bus=bus)
        mgr._announce_result = AsyncMock()

        lease = SubagentLease(
            model_id="lite-minimax-m2.7-high-minimax",
            tier="lite",
            route="minimax",
            effort="high",
        )

        child_provider = MagicMock()
        child_provider.get_default_model.return_value = "MiniMax-M2.7"
        child_provider.generation.max_tokens = 8192
        child_provider.generation.temperature = 0.1
        child_provider.generation.reasoning_effort = "high"

        captured = {}

        async def fake_run(self, spec):
            captured["model"] = spec.model
            return AgentRunResult(final_content="done", messages=[], tools_used=[], usage={})

        monkeypatch.setattr(
            mgr,
            "_build_provider_for_lease",
            MagicMock(return_value=(child_provider, "MiniMax-M2.7")),
        )
        monkeypatch.setattr(AgentRunner, "run", fake_run)

        await mgr._run_subagent(
            "sub-1", "do task", "label", {"channel": "test", "chat_id": "c1"}, lease
        )

        mgr._build_provider_for_lease.assert_called_once_with(lease)
        assert captured["model"] == "MiniMax-M2.7"

    @pytest.mark.asyncio
    async def test_run_subagent_rebuilds_provider_from_v2_registry_lease(self, tmp_path):
        import json

        from nanobot.agent.subagent import SubagentManager
        from nanobot.agent.subagent_resources import SubagentLease
        from nanobot.bus.queue import MessageBus

        registry = {
            "version": 2,
            "profile_defaults": {"chat": {"ref": "standard-gpt-5.4-high-tokenx"}},
            "routes": {
                "tokenx": {
                    "config_provider_ref": "custom",
                    "adapter": "openai_compat",
                    "api_base_override": "https://tokenx24.com/v1",
                }
            },
            "models": {
                "standard-gpt-5.4-high-tokenx": {
                    "family": "gpt-5.4",
                    "tier": "standard",
                    "effort": "high",
                    "route_ref": "tokenx",
                    "provider_model": "gpt-5.4",
                    "enabled": True,
                    "template": False,
                    "capabilities": {"tool_calls": True},
                }
            },
        }
        (tmp_path / "model_registry.json").write_text(
            json.dumps(registry, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        (tmp_path / "config.json").write_text(
            json.dumps(
                {
                    "agents": {"defaults": {"model": "gpt-5.4"}},
                    "providers": {"custom": {"apiKey": "k-tokenx"}},
                },
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )

        bus = MessageBus()
        parent_provider = MagicMock()
        parent_provider.get_default_model.return_value = "parent-model"
        mgr = SubagentManager(provider=parent_provider, workspace=tmp_path, bus=bus)

        provider, model = mgr._build_provider_for_lease(
            SubagentLease(
                model_id="standard-gpt-5.4-high-tokenx",
                tier="standard",
                route="tokenx",
                effort="high",
            )
        )

        assert provider.get_default_model() == "gpt-5.4"
        assert model == "gpt-5.4"

    def test_build_provider_for_lease_falls_back_to_legacy_registry_after_v2_semantic_error(
        self, tmp_path
    ):
        import json

        from nanobot.agent.subagent import SubagentManager
        from nanobot.agent.subagent_resources import SubagentLease
        from nanobot.bus.queue import MessageBus

        registry = {
            "version": 2,
            "profile_defaults": {"chat": {"ref": "standard-gpt-5.4-high-tokenx"}},
            "routes": {
                "tokenx": {
                    "config_provider_ref": "custom",
                    "adapter": "openai_compat",
                }
            },
            "models": {
                "standard-gpt-5.4-high-tokenx": {
                    "family": "gpt-5.4",
                    "tier": "standard",
                    "effort": "high",
                    "route_ref": "tokenx",
                    "provider_model": "v2-ignored",
                    "enabled": True,
                    "template": False,
                }
            },
        }
        (tmp_path / "model_registry.json").write_text(
            json.dumps(registry, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        bus = MessageBus()
        parent_provider = MagicMock()
        parent_provider.get_default_model.return_value = "parent-model"
        mgr = SubagentManager(provider=parent_provider, workspace=tmp_path, bus=bus)
        mgr.resource_manager.registry = {
            "models": {
                "standard-gpt-5.4-high-tokenx": {
                    "provider": "custom",
                    "provider_model": "legacy-gpt-5.4",
                    "connection": {
                        "api_base": "https://tokenx24.com/v1",
                        "api_key": "k-legacy",
                        "extra_headers": {},
                    },
                }
            }
        }

        provider, model = mgr._build_provider_for_lease(
            SubagentLease(
                model_id="standard-gpt-5.4-high-tokenx",
                tier="standard",
                route="tokenx",
                effort="high",
            )
        )

        assert provider is not parent_provider
        assert provider.get_default_model() == "legacy-gpt-5.4"
        assert model == "legacy-gpt-5.4"

    @pytest.mark.asyncio
    async def test_run_subagent_records_hard_provider_failure_and_shrinks_current_candidate_pool(
        self, tmp_path
    ):
        import json

        from nanobot.agent.runner import AgentRunResult
        from nanobot.agent.subagent import SubagentManager
        from nanobot.agent.subagent_resources import SubagentLease
        from nanobot.bus.queue import MessageBus

        registry = {
            "version": 1,
            "subagent_defaults": {"model": "gpt-5.4", "task_budget": 3, "level_limit": 2},
            "models": {
                "standard-gpt-5.4-high-aizhiwen-top": {
                    "tier": "standard",
                    "family": "gpt-5.4",
                    "effort": "high",
                    "route": "aizhiwen-top",
                    "provider": "custom",
                    "provider_model": "gpt-5.4",
                    "connection": {
                        "api_base": "https://aizhiwen.top/v1",
                        "api_key": "k-a",
                        "extra_headers": {},
                    },
                    "agent": {"temperature": 0.3, "max_tokens": 8192},
                    "enabled": True,
                    "template": False,
                    "aliases": ["gpt-5.4"],
                },
                "standard-gpt-5.4-high-tokenx": {
                    "tier": "standard",
                    "family": "gpt-5.4",
                    "effort": "high",
                    "route": "tokenx",
                    "provider": "custom",
                    "provider_model": "gpt-5.4",
                    "connection": {
                        "api_base": "https://tokenx24.com/v1",
                        "api_key": "k-t",
                        "extra_headers": {},
                    },
                    "agent": {"temperature": 0.3, "max_tokens": 8192},
                    "enabled": True,
                    "template": False,
                    "aliases": [],
                },
            },
        }
        (tmp_path / "config.json").write_text(
            json.dumps(
                {"agents": {"defaults": {"model": "gpt-5.4"}}, "providers": {}}, ensure_ascii=False
            )
            + "\n",
            encoding="utf-8",
        )
        (tmp_path / "model_registry.json").write_text(
            json.dumps(registry, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        bus = MessageBus()
        provider = MagicMock()
        provider.get_default_model.return_value = "standard-gpt-5.4-high-aizhiwen-top"
        mgr = SubagentManager(
            provider=provider,
            workspace=tmp_path,
            bus=bus,
            model="standard-gpt-5.4-high-aizhiwen-top",
        )
        mgr._announce_result = AsyncMock()
        mgr.runner.run = AsyncMock(
            return_value=AgentRunResult(
                final_content=None,
                messages=[],
                tools_used=[],
                usage={},
                stop_reason="error",
                error="quota exceeded",
            )
        )

        lease = SubagentLease(
            model_id="standard-gpt-5.4-high-aizhiwen-top",
            tier="standard",
            route="aizhiwen-top",
            effort="high",
        )

        await mgr._run_subagent(
            "sub-1", "do task", "label", {"channel": "test", "chat_id": "c1"}, lease
        )

        assert (
            mgr.resource_manager.route_policies["aizhiwen-top"].availability == "hard_unavailable"
        )
        assert (
            mgr.resource_manager.route_policies["aizhiwen-top"].unavailable_reason
            == "quota_exhausted"
        )

        follow_up = mgr.resource_manager.acquire(mgr.resource_manager.default_request())
        assert follow_up.status == "granted"
        assert follow_up.lease is not None
        assert follow_up.lease.model_id == "standard-gpt-5.4-high-tokenx"

        updated = json.loads((tmp_path / "model_registry.json").read_text(encoding="utf-8"))
        assert updated["provider_status"]["aizhiwen-top"]["availability"] == "hard_unavailable"
        assert updated["provider_status"]["aizhiwen-top"]["reason"] == "quota_exhausted"
        assert updated["provider_status"]["aizhiwen-top"]["source"] == "runtime_error"
        assert "updated_at" in updated["provider_status"]["aizhiwen-top"]

    @pytest.mark.asyncio
    async def test_run_subagent_records_transient_provider_failure_without_shrinking_candidate_pool(
        self, tmp_path
    ):
        import json

        from nanobot.agent.runner import AgentRunResult
        from nanobot.agent.subagent import SubagentManager
        from nanobot.agent.subagent_resources import SubagentLease
        from nanobot.bus.queue import MessageBus

        registry = {
            "version": 1,
            "subagent_defaults": {"model": "gpt-5.4", "task_budget": 3, "level_limit": 2},
            "models": {
                "standard-gpt-5.4-high-aizhiwen-top": {
                    "tier": "standard",
                    "family": "gpt-5.4",
                    "effort": "high",
                    "route": "aizhiwen-top",
                    "provider": "custom",
                    "provider_model": "gpt-5.4",
                    "connection": {
                        "api_base": "https://aizhiwen.top/v1",
                        "api_key": "k-a",
                        "extra_headers": {},
                    },
                    "agent": {"temperature": 0.3, "max_tokens": 8192},
                    "enabled": True,
                    "template": False,
                    "aliases": ["gpt-5.4"],
                },
                "standard-gpt-5.4-high-tokenx": {
                    "tier": "standard",
                    "family": "gpt-5.4",
                    "effort": "high",
                    "route": "tokenx",
                    "provider": "custom",
                    "provider_model": "gpt-5.4",
                    "connection": {
                        "api_base": "https://tokenx24.com/v1",
                        "api_key": "k-t",
                        "extra_headers": {},
                    },
                    "agent": {"temperature": 0.3, "max_tokens": 8192},
                    "enabled": True,
                    "template": False,
                    "aliases": [],
                },
            },
        }
        (tmp_path / "config.json").write_text(
            json.dumps(
                {"agents": {"defaults": {"model": "gpt-5.4"}}, "providers": {}}, ensure_ascii=False
            )
            + "\n",
            encoding="utf-8",
        )
        (tmp_path / "model_registry.json").write_text(
            json.dumps(registry, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        bus = MessageBus()
        provider = MagicMock()
        provider.get_default_model.return_value = "standard-gpt-5.4-high-aizhiwen-top"
        mgr = SubagentManager(
            provider=provider,
            workspace=tmp_path,
            bus=bus,
            model="standard-gpt-5.4-high-aizhiwen-top",
        )
        mgr._announce_result = AsyncMock()
        mgr.runner.run = AsyncMock(
            return_value=AgentRunResult(
                final_content=None,
                messages=[],
                tools_used=[],
                usage={},
                stop_reason="error",
                error="HTTP 502 upstream timeout",
            )
        )

        lease = SubagentLease(
            model_id="standard-gpt-5.4-high-aizhiwen-top",
            tier="standard",
            route="aizhiwen-top",
            effort="high",
        )

        await mgr._run_subagent(
            "sub-1", "do task", "label", {"channel": "test", "chat_id": "c1"}, lease
        )

        assert (
            mgr.resource_manager.route_policies["aizhiwen-top"].availability
            == "transient_unavailable"
        )
        assert mgr.resource_manager.route_policies["aizhiwen-top"].unavailable_reason == "http_502"

        follow_up = mgr.resource_manager.acquire(mgr.resource_manager.default_request())
        assert follow_up.status == "granted"
        assert follow_up.lease is not None
        assert follow_up.lease.model_id == "standard-gpt-5.4-high-aizhiwen-top"

        updated = json.loads((tmp_path / "model_registry.json").read_text(encoding="utf-8"))
        assert updated["provider_status"]["aizhiwen-top"]["availability"] == "transient_unavailable"
        assert updated["provider_status"]["aizhiwen-top"]["reason"] == "http_502"
        assert updated["provider_status"]["aizhiwen-top"]["source"] == "runtime_error"
        assert "updated_at" in updated["provider_status"]["aizhiwen-top"]

    @pytest.mark.asyncio
    async def test_run_subagent_error_uses_provider_probe_to_refresh_route_when_probe_succeeds(
        self, tmp_path
    ):
        import json

        from nanobot.agent.runner import AgentRunResult
        from nanobot.agent.subagent import SubagentManager
        from nanobot.agent.subagent_resources import SubagentLease
        from nanobot.bus.queue import MessageBus

        registry = {
            "version": 1,
            "subagent_defaults": {"model": "gpt-5.4", "task_budget": 3, "level_limit": 2},
            "models": {
                "standard-gpt-5.4-high-aizhiwen-top": {
                    "tier": "standard",
                    "family": "gpt-5.4",
                    "effort": "high",
                    "route": "aizhiwen-top",
                    "provider": "custom",
                    "provider_model": "gpt-5.4",
                    "connection": {
                        "api_base": "https://aizhiwen.top/v1",
                        "api_key": "k-a",
                        "extra_headers": {},
                    },
                    "agent": {"temperature": 0.3, "max_tokens": 8192},
                    "enabled": True,
                    "template": False,
                    "aliases": ["gpt-5.4"],
                },
                "standard-gpt-5.4-high-tokenx": {
                    "tier": "standard",
                    "family": "gpt-5.4",
                    "effort": "high",
                    "route": "tokenx",
                    "provider": "custom",
                    "provider_model": "gpt-5.4",
                    "connection": {
                        "api_base": "https://tokenx24.com/v1",
                        "api_key": "k-t",
                        "extra_headers": {},
                    },
                    "agent": {"temperature": 0.3, "max_tokens": 8192},
                    "enabled": True,
                    "template": False,
                    "aliases": [],
                },
            },
        }
        (tmp_path / "config.json").write_text(
            json.dumps(
                {"agents": {"defaults": {"model": "gpt-5.4"}}, "providers": {}}, ensure_ascii=False
            )
            + "\n",
            encoding="utf-8",
        )
        (tmp_path / "model_registry.json").write_text(
            json.dumps(registry, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        bus = MessageBus()
        provider = MagicMock()
        provider.get_default_model.return_value = "standard-gpt-5.4-high-aizhiwen-top"
        provider_probe = MagicMock(
            return_value={
                "ok": True,
                "provider": "custom",
                "api_base": "https://aizhiwen.top/v1",
                "reason": "OK",
            }
        )
        mgr = SubagentManager(
            provider=provider,
            workspace=tmp_path,
            bus=bus,
            model="standard-gpt-5.4-high-aizhiwen-top",
            provider_probe=provider_probe,
        )
        mgr._announce_result = AsyncMock()
        mgr.runner.run = AsyncMock(
            return_value=AgentRunResult(
                final_content=None,
                messages=[],
                tools_used=[],
                usage={},
                stop_reason="error",
                error="quota exceeded",
            )
        )

        lease = SubagentLease(
            model_id="standard-gpt-5.4-high-aizhiwen-top",
            tier="standard",
            route="aizhiwen-top",
            effort="high",
        )

        await mgr._run_subagent(
            "sub-1", "do task", "label", {"channel": "test", "chat_id": "c1"}, lease
        )

        provider_probe.assert_called_once_with(tmp_path, ref="standard-gpt-5.4-high-aizhiwen-top")
        assert mgr.resource_manager.route_policies["aizhiwen-top"].availability == "available"
        updated = json.loads((tmp_path / "model_registry.json").read_text(encoding="utf-8"))
        assert updated["provider_status"]["aizhiwen-top"]["availability"] == "available"
        assert updated["provider_status"]["aizhiwen-top"]["source"] == "monitor_refresh"

    @pytest.mark.asyncio
    async def test_run_subagent_success_refreshes_current_route_status_in_manager_and_workspace(
        self, tmp_path
    ):
        import json

        from nanobot.agent.runner import AgentRunResult
        from nanobot.agent.subagent import SubagentManager
        from nanobot.agent.subagent_resources import (
            SubagentLease,
            build_manager_from_workspace_snapshot,
        )
        from nanobot.bus.queue import MessageBus

        registry = {
            "version": 1,
            "provider_status": {
                "aizhiwen-top": {
                    "availability": "hard_unavailable",
                    "reason": "quota_exhausted",
                    "source": "runtime_error",
                    "updated_at": "2026-04-06T09:40:00+00:00",
                }
            },
            "subagent_defaults": {"model": "gpt-5.4", "task_budget": 3, "level_limit": 2},
            "models": {
                "standard-gpt-5.4-high-aizhiwen-top": {
                    "tier": "standard",
                    "family": "gpt-5.4",
                    "effort": "high",
                    "route": "aizhiwen-top",
                    "provider": "custom",
                    "provider_model": "gpt-5.4",
                    "connection": {
                        "api_base": "https://aizhiwen.top/v1",
                        "api_key": "k-a",
                        "extra_headers": {},
                    },
                    "agent": {"temperature": 0.3, "max_tokens": 8192},
                    "enabled": True,
                    "template": False,
                    "aliases": ["gpt-5.4"],
                },
                "standard-gpt-5.4-high-tokenx": {
                    "tier": "standard",
                    "family": "gpt-5.4",
                    "effort": "high",
                    "route": "tokenx",
                    "provider": "custom",
                    "provider_model": "gpt-5.4",
                    "connection": {
                        "api_base": "https://tokenx24.com/v1",
                        "api_key": "k-t",
                        "extra_headers": {},
                    },
                    "agent": {"temperature": 0.3, "max_tokens": 8192},
                    "enabled": True,
                    "template": False,
                    "aliases": [],
                },
            },
        }
        (tmp_path / "config.json").write_text(
            json.dumps(
                {"agents": {"defaults": {"model": "gpt-5.4"}}, "providers": {}}, ensure_ascii=False
            )
            + "\n",
            encoding="utf-8",
        )
        (tmp_path / "model_registry.json").write_text(
            json.dumps(registry, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        bus = MessageBus()
        provider = MagicMock()
        provider.get_default_model.return_value = "standard-gpt-5.4-high-aizhiwen-top"
        mgr = SubagentManager(
            provider=provider,
            workspace=tmp_path,
            bus=bus,
            model="standard-gpt-5.4-high-aizhiwen-top",
        )
        mgr._announce_result = AsyncMock()
        mgr.runner.run = AsyncMock(
            return_value=AgentRunResult(
                final_content="done",
                messages=[],
                tools_used=[],
                usage={},
            )
        )

        lease = SubagentLease(
            model_id="standard-gpt-5.4-high-aizhiwen-top",
            tier="standard",
            route="aizhiwen-top",
            effort="high",
        )

        assert (
            mgr.resource_manager.route_policies["aizhiwen-top"].availability == "hard_unavailable"
        )

        await mgr._run_subagent(
            "sub-1", "do task", "label", {"channel": "test", "chat_id": "c1"}, lease
        )

        assert mgr.resource_manager.route_policies["aizhiwen-top"].availability == "available"
        assert mgr.resource_manager.route_policies["aizhiwen-top"].unavailable_reason == ""

        updated = json.loads((tmp_path / "model_registry.json").read_text(encoding="utf-8"))
        assert updated["provider_status"]["aizhiwen-top"]["availability"] == "available"
        assert updated["provider_status"]["aizhiwen-top"]["reason"] == ""
        assert updated["provider_status"]["aizhiwen-top"]["source"] == "monitor_refresh"
        assert "updated_at" in updated["provider_status"]["aizhiwen-top"]

        follow_up = mgr.resource_manager.acquire(mgr.resource_manager.default_request())
        assert follow_up.status == "granted"
        assert follow_up.lease is not None
        assert follow_up.lease.model_id == "standard-gpt-5.4-high-aizhiwen-top"

        rebuilt = build_manager_from_workspace_snapshot(workspace=tmp_path)
        rebuilt_follow_up = rebuilt.acquire(rebuilt.default_request())
        assert rebuilt_follow_up.status == "granted"
        assert rebuilt_follow_up.lease is not None
        assert rebuilt_follow_up.lease.model_id == "standard-gpt-5.4-high-aizhiwen-top"

    @pytest.mark.asyncio
    async def test_spawn_releases_resource_lease_after_successful_completion(self, tmp_path):
        from nanobot.agent.runner import AgentRunResult
        from nanobot.agent.subagent import SubagentManager
        from nanobot.agent.subagent_resources import AcquireDecision, SubagentLease
        from nanobot.bus.queue import MessageBus

        bus = MessageBus()
        provider = MagicMock()
        provider.get_default_model.return_value = "test-model"
        mgr = SubagentManager(provider=provider, workspace=tmp_path, bus=bus)

        lease = SubagentLease(
            model_id="standard-gpt-5.4-high-aizhiwen-top",
            tier="standard",
            route="aizhiwen-top",
            effort="high",
        )
        resource_manager = MagicMock()
        resource_manager.acquire.return_value = AcquireDecision(status="granted", lease=lease)
        resource_manager.release = MagicMock()
        mgr.resource_manager = resource_manager
        mgr._resolve_subagent_request = MagicMock(return_value=object())
        mgr._announce_result = AsyncMock()
        mgr.runner.run = AsyncMock(
            return_value=AgentRunResult(
                final_content="done",
                messages=[],
                tools_used=[],
                usage={},
            )
        )

        result = await mgr.spawn(task="do task", label="bg", session_key="test:c1")
        assert "started" in result.lower()
        running = list(mgr._running_tasks.values())
        assert len(running) == 1
        await running[0]

        resource_manager.release.assert_called_once_with(lease)

    @pytest.mark.asyncio
    async def test_spawn_releases_resource_lease_after_failure(self, tmp_path):
        from nanobot.agent.subagent import SubagentManager
        from nanobot.agent.subagent_resources import AcquireDecision, SubagentLease
        from nanobot.bus.queue import MessageBus

        bus = MessageBus()
        provider = MagicMock()
        provider.get_default_model.return_value = "test-model"
        mgr = SubagentManager(provider=provider, workspace=tmp_path, bus=bus)

        lease = SubagentLease(
            model_id="standard-gpt-5.4-high-aizhiwen-top",
            tier="standard",
            route="aizhiwen-top",
            effort="high",
        )
        resource_manager = MagicMock()
        resource_manager.acquire.return_value = AcquireDecision(status="granted", lease=lease)
        resource_manager.release = MagicMock()
        mgr.resource_manager = resource_manager
        mgr._resolve_subagent_request = MagicMock(return_value=object())
        mgr._announce_result = AsyncMock()
        mgr.runner.run = AsyncMock(side_effect=RuntimeError("boom"))

        result = await mgr.spawn(task="do task", label="bg", session_key="test:c1")
        assert "started" in result.lower()
        running = list(mgr._running_tasks.values())
        assert len(running) == 1
        await running[0]

        resource_manager.release.assert_called_once_with(lease)

    @pytest.mark.asyncio
    async def test_cancel_by_session_releases_resource_lease(self, monkeypatch, tmp_path):
        from nanobot.agent.subagent import SubagentManager
        from nanobot.agent.subagent_resources import AcquireDecision, SubagentLease
        from nanobot.bus.queue import MessageBus
        from nanobot.providers.base import LLMResponse, ToolCallRequest

        bus = MessageBus()
        provider = MagicMock()
        provider.get_default_model.return_value = "test-model"
        provider.chat_with_retry = AsyncMock(
            return_value=LLMResponse(
                content="thinking",
                tool_calls=[ToolCallRequest(id="call_1", name="list_dir", arguments={})],
            )
        )
        mgr = SubagentManager(provider=provider, workspace=tmp_path, bus=bus)
        mgr._announce_result = AsyncMock()

        lease = SubagentLease(
            model_id="standard-gpt-5.4-high-aizhiwen-top",
            tier="standard",
            route="aizhiwen-top",
            effort="high",
        )
        resource_manager = MagicMock()
        resource_manager.acquire.return_value = AcquireDecision(status="granted", lease=lease)
        resource_manager.release = MagicMock()
        mgr.resource_manager = resource_manager
        mgr._resolve_subagent_request = MagicMock(return_value=object())

        started = asyncio.Event()
        cancelled = asyncio.Event()

        async def fake_execute(self, name, arguments):
            started.set()
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                cancelled.set()
                raise

        monkeypatch.setattr("nanobot.agent.tools.registry.ToolRegistry.execute", fake_execute)

        result = await mgr.spawn(task="do task", label="bg", session_key="test:c1")
        assert "started" in result.lower()
        running = list(mgr._running_tasks.values())
        assert len(running) == 1

        await started.wait()
        count = await mgr.cancel_by_session("test:c1")

        assert count == 1
        assert cancelled.is_set()
        resource_manager.release.assert_called_once_with(lease)

    @pytest.mark.asyncio
    async def test_cancel_by_session_cancels_running_subagent_tool(self, monkeypatch, tmp_path):
        from nanobot.agent.subagent import SubagentManager
        from nanobot.bus.queue import MessageBus
        from nanobot.providers.base import LLMResponse, ToolCallRequest

        bus = MessageBus()
        provider = MagicMock()
        provider.get_default_model.return_value = "test-model"
        provider.chat_with_retry = AsyncMock(
            return_value=LLMResponse(
                content="thinking",
                tool_calls=[ToolCallRequest(id="call_1", name="list_dir", arguments={})],
            )
        )
        mgr = SubagentManager(provider=provider, workspace=tmp_path, bus=bus)
        mgr._announce_result = AsyncMock()

        started = asyncio.Event()
        cancelled = asyncio.Event()

        async def fake_execute(self, name, arguments):
            started.set()
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                cancelled.set()
                raise

        monkeypatch.setattr("nanobot.agent.tools.registry.ToolRegistry.execute", fake_execute)

        task = asyncio.create_task(
            mgr._run_subagent("sub-1", "do task", "label", {"channel": "test", "chat_id": "c1"})
        )
        mgr._running_tasks["sub-1"] = task
        mgr._session_tasks["test:c1"] = {"sub-1"}

        await started.wait()

        count = await mgr.cancel_by_session("test:c1")

        assert count == 1
        assert cancelled.is_set()
        assert task.cancelled()
        mgr._announce_result.assert_not_awaited()
