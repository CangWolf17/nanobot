from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import asyncio
import pytest
import subprocess

from nanobot.agent.loop import AgentLoop
from nanobot.bus.events import InboundMessage, OutboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.config.schema import ChannelsConfig
from nanobot.harness.service import HarnessService
from nanobot.providers.base import LLMResponse, ToolCallRequest


def _make_loop(tmp_path: Path) -> tuple[AgentLoop, MessageBus]:
    bus = MessageBus()
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    provider.generation.max_tokens = 4096
    provider.chat_with_retry = AsyncMock(return_value=LLMResponse(content="done", tool_calls=[]))
    provider.chat_stream_with_retry = AsyncMock(
        return_value=LLMResponse(content="done", tool_calls=[])
    )
    loop = AgentLoop(bus=bus, provider=provider, workspace=tmp_path, model="test-model")
    loop.tools.get_definitions = MagicMock(return_value=[])
    loop.memory_consolidator.maybe_consolidate_by_tokens = AsyncMock(return_value=None)
    loop.commands.dispatch = AsyncMock(return_value=None)
    return loop, bus


def _start_harness(
    tmp_path: Path,
    *,
    session_key: str = "feishu:chat1",
    sender_id: str = "user1",
    status: str = "active",
    phase: str = "executing",
    auto_continue: bool = True,
    awaiting_user: bool = False,
    blocked: bool = False,
) -> HarnessService:
    service = HarnessService.for_workspace(tmp_path)
    result = service.handle_command(
        "/harness 修复 interrupt 的真实接线",
        session_key=session_key,
        sender_id=sender_id,
    )
    snapshot = service.store.load()
    active = snapshot.records[result.active_harness_id]
    active.status = status
    active.phase = phase
    active.awaiting_user = awaiting_user
    active.blocked = blocked
    active.execution_policy.auto_continue = auto_continue
    service.store.save(snapshot)
    return service


def test_workspace_agent_command_emits_progress_before_agent_run(tmp_path: Path) -> None:
    async def run() -> None:
        loop, bus = _make_loop(tmp_path)
        msg = InboundMessage(
            channel="telegram",
            sender_id="user1",
            chat_id="chat1",
            content="/小结",
            metadata={"workspace_agent_cmd": "小结"},
        )

        result = await loop._process_message(msg)
        progress = await asyncio.wait_for(bus.consume_outbound(), timeout=1.0)

        assert progress.content == "正在生成小结…"
        assert progress.metadata["_progress"] is True
        assert result is not None
        assert result.content == "done"

    asyncio.run(run())


def test_workspace_agent_plan_emits_planning_progress_before_agent_run(tmp_path: Path) -> None:
    async def run() -> None:
        loop, bus = _make_loop(tmp_path)
        msg = InboundMessage(
            channel="telegram",
            sender_id="user1",
            chat_id="chat1",
            content="/plan 规划一下发布节奏",
            metadata={"workspace_agent_cmd": "plan"},
        )

        result = await loop._process_message(msg)
        progress = await asyncio.wait_for(bus.consume_outbound(), timeout=1.0)

        assert progress.content == "正在进入规划讨论…"
        assert progress.metadata["_progress"] is True
        assert result is not None
        assert result.content == "done"

    asyncio.run(run())


def test_workspace_agent_notes_emits_progress_before_agent_run(tmp_path: Path) -> None:
    async def run() -> None:
        loop, bus = _make_loop(tmp_path)
        msg = InboundMessage(
            channel="telegram",
            sender_id="user1",
            chat_id="chat1",
            content="/笔记 新建 runtime follow-ups",
            metadata={"workspace_agent_cmd": "笔记"},
        )

        result = await loop._process_message(msg)
        progress = await asyncio.wait_for(bus.consume_outbound(), timeout=1.0)

        assert progress.content == "正在整理笔记草稿…"
        assert progress.metadata["_progress"] is True
        assert result is not None
        assert result.content == "done"

    asyncio.run(run())


def test_workspace_agent_weather_brief_hides_tool_hints_from_user(tmp_path: Path) -> None:
    async def run() -> None:
        loop, bus = _make_loop(tmp_path)

        async def fake_run_agent_loop(*_args, **kwargs):
            on_progress = kwargs.get("on_progress")
            assert on_progress is not None
            await on_progress('python /home/admin/.nanobot/workspace/scripts/weather.py 重庆南岸区 forecast', tool_hint=True)
            return "天气成品", [], []

        loop._run_agent_loop = AsyncMock(side_effect=fake_run_agent_loop)
        msg = InboundMessage(
            channel="feishu",
            sender_id="user1",
            chat_id="chat1",
            content="天气",
            metadata={"workspace_agent_cmd": "weather_brief"},
        )

        result = await loop._process_message(msg)
        first = await asyncio.wait_for(bus.consume_outbound(), timeout=1.0)

        assert first.content == "正在生成天气早报…"
        assert first.metadata["_progress"] is True
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(bus.consume_outbound(), timeout=0.05)
        assert result is not None
        assert result.content == "天气成品"

    asyncio.run(run())


def test_stream_requested_without_deltas_keeps_final_reply_sendable(tmp_path: Path) -> None:
    async def run() -> None:
        loop, bus = _make_loop(tmp_path)
        msg = InboundMessage(
            channel="feishu",
            sender_id="user1",
            chat_id="chat1",
            content="你好",
            metadata={"_wants_stream": True},
        )

        await loop._dispatch(msg)
        stream_start = await asyncio.wait_for(bus.consume_outbound(), timeout=1.0)
        stream_end = await asyncio.wait_for(bus.consume_outbound(), timeout=1.0)
        outbound = await asyncio.wait_for(bus.consume_outbound(), timeout=1.0)

        assert stream_start.metadata["_stream_start"] is True
        assert stream_end.metadata["_stream_end"] is True
        assert outbound.content == "done"
        assert "_streamed" not in (outbound.metadata or {})

    asyncio.run(run())


def test_stream_requested_command_dispatch_closes_placeholder_stream(tmp_path: Path) -> None:
    async def run() -> None:
        loop, bus = _make_loop(tmp_path)
        loop.commands.dispatch = AsyncMock(
            return_value=OutboundMessage(channel="feishu", chat_id="chat1", content="done")
        )
        msg = InboundMessage(
            channel="feishu",
            sender_id="user1",
            chat_id="chat1",
            content="/test",
            metadata={"_wants_stream": True},
        )

        await loop._dispatch(msg)
        stream_start = await asyncio.wait_for(bus.consume_outbound(), timeout=1.0)
        stream_end = await asyncio.wait_for(bus.consume_outbound(), timeout=1.0)
        outbound = await asyncio.wait_for(bus.consume_outbound(), timeout=1.0)

        assert stream_start.metadata["_stream_start"] is True
        assert stream_end.metadata["_stream_end"] is True
        assert outbound.content == "done"

    asyncio.run(run())


def test_stream_requested_exception_closes_placeholder_stream(tmp_path: Path) -> None:
    async def run() -> None:
        loop, bus = _make_loop(tmp_path)
        loop._process_message = AsyncMock(side_effect=RuntimeError("boom"))
        msg = InboundMessage(
            channel="feishu",
            sender_id="user1",
            chat_id="chat1",
            content="你好",
            metadata={"_wants_stream": True},
        )

        await loop._dispatch(msg)
        stream_start = await asyncio.wait_for(bus.consume_outbound(), timeout=1.0)
        stream_end = await asyncio.wait_for(bus.consume_outbound(), timeout=1.0)
        outbound = await asyncio.wait_for(bus.consume_outbound(), timeout=1.0)

        assert stream_start.metadata["_stream_start"] is True
        assert stream_end.metadata["_stream_end"] is True
        assert outbound.content == "Sorry, I encountered an error."

    asyncio.run(run())


def test_stream_requested_cancellation_closes_placeholder_stream(tmp_path: Path) -> None:
    async def run() -> None:
        loop, bus = _make_loop(tmp_path)
        loop._process_message = AsyncMock(side_effect=asyncio.CancelledError())
        msg = InboundMessage(
            channel="feishu",
            sender_id="user1",
            chat_id="chat1",
            content="你好",
            metadata={"_wants_stream": True},
        )

        with pytest.raises(asyncio.CancelledError):
            await loop._dispatch(msg)

        stream_start = await asyncio.wait_for(bus.consume_outbound(), timeout=1.0)
        stream_end = await asyncio.wait_for(bus.consume_outbound(), timeout=1.0)

        assert stream_start.metadata["_stream_start"] is True
        assert stream_end.metadata["_stream_end"] is True

    asyncio.run(run())


def test_final_reply_stays_sendable_when_earlier_stream_segment_had_deltas(tmp_path: Path) -> None:
    async def run() -> None:
        loop, bus = _make_loop(tmp_path)
        call_count = {"n": 0}

        async def _chat_stream_with_retry(**kwargs):
            call_count["n"] += 1
            on_content_delta = kwargs.get("on_content_delta")
            if call_count["n"] == 1:
                if on_content_delta is not None:
                    await on_content_delta("thinking")
                return LLMResponse(
                    content="thinking",
                    tool_calls=[ToolCallRequest(id="call_1", name="list_dir", arguments={"path": "."})],
                )
            return LLMResponse(content="final done", tool_calls=[])

        loop.provider.chat_stream_with_retry = _chat_stream_with_retry
        loop.tools.execute = AsyncMock(return_value="tool result")
        msg = InboundMessage(
            channel="feishu",
            sender_id="user1",
            chat_id="chat1",
            content="你好",
            metadata={"_wants_stream": True},
        )

        await loop._dispatch(msg)
        outputs = [await asyncio.wait_for(bus.consume_outbound(), timeout=1.0) for _ in range(bus.outbound_size)]
        final_reply = outputs[-1]

        assert final_reply.content == "final done"
        assert "_streamed" not in (final_reply.metadata or {})

    asyncio.run(run())


def test_separate_completion_notice_when_earlier_streamed_but_final_segment_silent(tmp_path: Path) -> None:
    async def run() -> None:
        loop, bus = _make_loop(tmp_path)
        loop.channels_config = ChannelsConfig.model_validate(
            {
                "feishu": {
                    "enabled": True,
                    "streaming": True,
                    "streamingCompletionNoticeEnabled": True,
                    "streamingCompletionNoticeText": "✅ 回复完成",
                    "streamingCompletionNoticeMentionUser": True,
                }
            }
        )
        call_count = {"n": 0}

        async def _chat_stream_with_retry(**kwargs):
            call_count["n"] += 1
            on_content_delta = kwargs.get("on_content_delta")
            if call_count["n"] == 1:
                if on_content_delta is not None:
                    await on_content_delta("thinking")
                return LLMResponse(
                    content="thinking",
                    tool_calls=[ToolCallRequest(id="call_1", name="list_dir", arguments={"path": "."})],
                )
            return LLMResponse(content="final done", tool_calls=[])

        loop.provider.chat_stream_with_retry = _chat_stream_with_retry
        loop.tools.execute = AsyncMock(return_value="tool result")
        msg = InboundMessage(
            channel="feishu",
            sender_id="user1",
            chat_id="chat1",
            content="你好",
            metadata={"_wants_stream": True},
        )

        await loop._dispatch(msg)
        outputs = [await asyncio.wait_for(bus.consume_outbound(), timeout=1.0) for _ in range(bus.outbound_size)]

        final_reply = next(item for item in outputs if item.content == "final done")
        completion_notice = next(
            item for item in outputs if (item.metadata or {}).get("_completion_notice") is True
        )

        assert final_reply.content == "final done"
        assert "_streamed" not in (final_reply.metadata or {})
        assert completion_notice.metadata["_completion_notice"] is True
        assert completion_notice.metadata["_completion_notice_mention_user_id"] == "user1"

    asyncio.run(run())


def test_separate_completion_notice_prefers_key_principle_text_when_final_segment_silent(tmp_path: Path) -> None:
    async def run() -> None:
        loop, bus = _make_loop(tmp_path)
        loop.channels_config = ChannelsConfig.model_validate(
            {
                "feishu": {
                    "enabled": True,
                    "streaming": True,
                    "streamingCompletionNoticeEnabled": True,
                    "streamingCompletionNoticeText": "✅ 回复完成",
                    "streamingCompletionNoticeMentionUser": True,
                }
            }
        )

        async def fake_process_message(_msg, **_kwargs):
            return OutboundMessage(
                channel="feishu",
                chat_id="chat1",
                content="final done",
                metadata={
                    "_streamed": True,
                    "_completion_notice_text": "Key Principle：先收口，再扩展。",
                    "_terminal_key_principle_text": "Key Principle：先收口，再扩展。",
                },
            )

        loop._process_message = fake_process_message
        msg = InboundMessage(
            channel="feishu",
            sender_id="user1",
            chat_id="chat1",
            content="你好",
            metadata={"_wants_stream": True},
        )

        await loop._dispatch(msg)
        outputs = [await asyncio.wait_for(bus.consume_outbound(), timeout=1.0) for _ in range(bus.outbound_size)]

        completion_notice = next(
            item for item in outputs if (item.metadata or {}).get("_completion_notice") is True
        )
        assert completion_notice.metadata["_completion_notice_text"] == "Key Principle：先收口，再扩展。"
        assert completion_notice.metadata["_completion_notice_mention_user_id"] == "user1"

    asyncio.run(run())


def test_zero_delta_streamed_turn_with_mention_emits_completion_notice(tmp_path: Path) -> None:
    async def run() -> None:
        loop, bus = _make_loop(tmp_path)
        loop.channels_config = ChannelsConfig.model_validate(
            {
                "feishu": {
                    "enabled": True,
                    "streaming": True,
                    "streamingCompletionNoticeEnabled": True,
                    "streamingCompletionNoticeText": "✅ 回复完成",
                    "streamingCompletionNoticeMentionUser": True,
                }
            }
        )
        msg = InboundMessage(
            channel="feishu",
            sender_id="user1",
            chat_id="chat1",
            content="你好",
            metadata={"_wants_stream": True, "message_id": "mid-1", "thread_id": "th-1", "root_id": "root-1"},
        )

        await loop._dispatch(msg)
        outputs = [await asyncio.wait_for(bus.consume_outbound(), timeout=1.0) for _ in range(bus.outbound_size)]

        final_reply = next(item for item in outputs if item.content == "done")
        completion_notice = next(
            item for item in outputs if (item.metadata or {}).get("_completion_notice") is True
        )

        assert "_streamed" not in (final_reply.metadata or {})
        assert completion_notice.metadata["_completion_notice_mention_user_id"] == "user1"
        assert completion_notice.metadata["message_id"] == "mid-1"
        assert completion_notice.metadata["thread_id"] == "th-1"
        assert completion_notice.metadata["root_id"] == "root-1"

    asyncio.run(run())


def test_whitespace_only_final_segment_with_mention_keeps_reply_and_notice(tmp_path: Path) -> None:
    async def run() -> None:
        loop, bus = _make_loop(tmp_path)
        loop.channels_config = ChannelsConfig.model_validate(
            {
                "feishu": {
                    "enabled": True,
                    "streaming": True,
                    "streamingCompletionNoticeEnabled": True,
                    "streamingCompletionNoticeText": "✅ 回复完成",
                    "streamingCompletionNoticeMentionUser": True,
                }
            }
        )

        async def _chat_stream_with_retry(**kwargs):
            on_content_delta = kwargs.get("on_content_delta")
            if on_content_delta is not None:
                await on_content_delta("   ")
            return LLMResponse(content="final done", tool_calls=[])

        loop.provider.chat_stream_with_retry = _chat_stream_with_retry
        msg = InboundMessage(
            channel="feishu",
            sender_id="user1",
            chat_id="chat1",
            content="你好",
            metadata={"_wants_stream": True},
        )

        await loop._dispatch(msg)
        outputs = [await asyncio.wait_for(bus.consume_outbound(), timeout=1.0) for _ in range(bus.outbound_size)]

        final_reply = next(item for item in outputs if item.content == "final done")
        completion_notice = next(
            item for item in outputs if (item.metadata or {}).get("_completion_notice") is True
        )

        assert "_streamed" not in (final_reply.metadata or {})
        assert completion_notice.metadata["_completion_notice"] is True

    asyncio.run(run())


def test_whitespace_only_final_stream_segment_keeps_final_reply_sendable(tmp_path: Path) -> None:
    async def run() -> None:
        loop, bus = _make_loop(tmp_path)

        async def _chat_stream_with_retry(**kwargs):
            on_content_delta = kwargs.get("on_content_delta")
            if on_content_delta is not None:
                await on_content_delta("   ")
            return LLMResponse(content="final done", tool_calls=[])

        loop.provider.chat_stream_with_retry = _chat_stream_with_retry
        msg = InboundMessage(
            channel="feishu",
            sender_id="user1",
            chat_id="chat1",
            content="你好",
            metadata={"_wants_stream": True},
        )

        await loop._dispatch(msg)
        outputs = [await asyncio.wait_for(bus.consume_outbound(), timeout=1.0) for _ in range(bus.outbound_size)]
        final_reply = outputs[-1]

        assert final_reply.content == "final done"
        assert "_streamed" not in (final_reply.metadata or {})

    asyncio.run(run())


def test_final_visible_stream_segment_preserves_streamed_marker(tmp_path: Path) -> None:
    async def run() -> None:
        loop, bus = _make_loop(tmp_path)
        call_count = {"n": 0}

        async def _chat_stream_with_retry(**kwargs):
            call_count["n"] += 1
            on_content_delta = kwargs.get("on_content_delta")
            if call_count["n"] == 1:
                if on_content_delta is not None:
                    await on_content_delta("thinking")
                return LLMResponse(
                    content="thinking",
                    tool_calls=[ToolCallRequest(id="call_1", name="list_dir", arguments={"path": "."})],
                )
            if on_content_delta is not None:
                await on_content_delta("final")
            return LLMResponse(content="final done", tool_calls=[])

        loop.provider.chat_stream_with_retry = _chat_stream_with_retry
        loop.tools.execute = AsyncMock(return_value="tool result")
        msg = InboundMessage(
            channel="feishu",
            sender_id="user1",
            chat_id="chat1",
            content="你好",
            metadata={"_wants_stream": True},
        )

        await loop._dispatch(msg)
        outputs = [await asyncio.wait_for(bus.consume_outbound(), timeout=1.0) for _ in range(bus.outbound_size)]
        final_reply = outputs[-1]

        assert final_reply.content == "final done"
        assert final_reply.metadata["_streamed"] is True

    asyncio.run(run())


def test_workspace_harness_runtime_metadata_prefers_durable_auto_from_store(tmp_path: Path) -> None:
    loop, _bus = _make_loop(tmp_path)
    (tmp_path / "harnesses").mkdir(parents=True)
    (tmp_path / "harnesses" / "control.json").write_text(
        '{"active_harness_id":"har_0001","updated_at":""}', encoding="utf-8"
    )
    (tmp_path / "harnesses" / "index.json").write_text(
        '{"harnesses":{"har_0001":{"id":"har_0001","type":"feature","status":"active","phase":"executing","awaiting_user":false,"blocked":false,"auto":true}}}',
        encoding="utf-8",
    )
    msg = InboundMessage(
        channel="feishu",
        sender_id="user1",
        chat_id="chat1",
        content="/harness",
        metadata={"workspace_agent_cmd": "harness"},
    )

    runtime_meta = loop._extract_runtime_metadata(msg)

    assert runtime_meta["has_active_harness"] is True
    assert runtime_meta["active_harness"]["auto"] is True


def test_workspace_harness_runtime_metadata_falls_back_to_durable_auto_false_when_flag_absent(
    tmp_path: Path,
) -> None:
    loop, _bus = _make_loop(tmp_path)
    (tmp_path / "harnesses").mkdir(parents=True)
    (tmp_path / "harnesses" / "control.json").write_text(
        '{"active_harness_id":"har_0001","updated_at":""}', encoding="utf-8"
    )
    (tmp_path / "harnesses" / "index.json").write_text(
        '{"harnesses":{"har_0001":{"id":"har_0001","type":"feature","status":"active","phase":"executing","awaiting_user":false,"blocked":false}}}',
        encoding="utf-8",
    )
    msg = InboundMessage(
        channel="feishu",
        sender_id="user1",
        chat_id="chat1",
        content="/harness",
        metadata={"workspace_agent_cmd": "harness"},
    )

    runtime_meta = loop._extract_runtime_metadata(msg)

    assert runtime_meta["has_active_harness"] is True
    assert runtime_meta["active_harness"]["auto"] is False


def test_workspace_harness_runtime_metadata_exposes_main_harness_and_next_runnable_child(
    tmp_path: Path,
) -> None:
    loop, _bus = _make_loop(tmp_path)
    (tmp_path / "harnesses").mkdir(parents=True)
    (tmp_path / "harnesses" / "control.json").write_text(
        '{"active_harness_id":"har_0001","updated_at":""}', encoding="utf-8"
    )
    (tmp_path / "harnesses" / "index.json").write_text(
        '{"harnesses":{'
        '"har_0001":{"id":"har_0001","type":"project","status":"completed","phase":"completed","awaiting_user":false,"blocked":false,"auto":true,"queue_order":1},'
        '"har_0002":{"id":"har_0002","type":"feature","parent_id":"har_0001","status":"planning","phase":"planning","awaiting_user":false,"blocked":false,"auto":false,"queue_order":2},'
        '"har_0003":{"id":"har_0003","type":"feature","parent_id":"har_0001","status":"planning","phase":"planning","awaiting_user":false,"blocked":false,"auto":false,"queue_order":3}'
        "}}",
        encoding="utf-8",
    )
    msg = InboundMessage(
        channel="feishu",
        sender_id="user1",
        chat_id="chat1",
        content="/harness auto",
        metadata={"workspace_agent_cmd": "harness", "workspace_harness_auto": True},
    )

    runtime_meta = loop._extract_runtime_metadata(msg)

    assert runtime_meta["has_active_harness"] is True
    assert runtime_meta["active_harness"]["id"] == "har_0001"
    assert runtime_meta["main_harness"]["id"] == "har_0001"
    assert runtime_meta["main_harness"]["has_open_children"] is True
    assert runtime_meta["next_runnable_child"]["id"] == "har_0002"
    assert runtime_meta.get("stop_gate_child") is None


def test_workspace_harness_runtime_metadata_includes_subagent_policy_fields(tmp_path: Path) -> None:
    loop, _bus = _make_loop(tmp_path)
    (tmp_path / "harnesses").mkdir(parents=True)
    (tmp_path / "harnesses" / "control.json").write_text(
        '{"active_harness_id":"har_0001","updated_at":""}', encoding="utf-8"
    )
    (tmp_path / "harnesses" / "index.json").write_text(
        '{"harnesses":{"har_0001":{"id":"har_0001","type":"feature","status":"active","phase":"executing","awaiting_user":false,"blocked":false,"auto":true,"executor_mode":"auto","delegation_level":"required","risk_level":"sensitive","subagent_allowed":true,"subagent_profile":"orchestrator","runner":"subagent"}}}',
        encoding="utf-8",
    )
    msg = InboundMessage(
        channel="feishu",
        sender_id="user1",
        chat_id="chat1",
        content="/harness",
        metadata={"workspace_agent_cmd": "harness"},
    )

    runtime_meta = loop._extract_runtime_metadata(msg)

    assert runtime_meta["has_active_harness"] is True
    assert runtime_meta["active_harness"]["executor_mode"] == "auto"
    assert runtime_meta["active_harness"]["delegation_level"] == "required"
    assert runtime_meta["active_harness"]["risk_level"] == "sensitive"
    assert runtime_meta["active_harness"]["subagent_allowed"] is True
    assert runtime_meta["active_harness"]["subagent_profile"] == "orchestrator"
    assert runtime_meta["active_harness"]["runner"] == "subagent"


def test_workspace_harness_turn_sets_spawn_context_with_computed_runtime_policy(
    tmp_path: Path,
) -> None:
    async def run() -> None:
        loop, _bus = _make_loop(tmp_path)
        (tmp_path / "harnesses").mkdir(parents=True)
        (tmp_path / "harnesses" / "control.json").write_text(
            '{"active_harness_id":"har_0001","updated_at":""}', encoding="utf-8"
        )
        (tmp_path / "harnesses" / "index.json").write_text(
            '{"harnesses":{"har_0001":{"id":"har_0001","type":"feature","status":"active","phase":"executing","awaiting_user":false,"blocked":false,"auto":false,"executor_mode":"main","subagent_allowed":false,"runner":"main"}}}',
            encoding="utf-8",
        )
        msg = InboundMessage(
            channel="feishu",
            sender_id="user1",
            chat_id="chat1",
            content="/harness",
            metadata={
                "workspace_agent_cmd": "harness",
                "workspace_agent_input": "prepared",
                "workspace_harness_id": "har_0001",
            },
        )

        loop._run_pre_reply_consolidation = AsyncMock(return_value=True)
        loop._select_history_for_reply = MagicMock(return_value=[])
        loop._save_turn = MagicMock()
        loop.sessions.save = MagicMock()
        loop._schedule_background = lambda coro: coro.close()
        loop.tools.get = MagicMock(side_effect=lambda name: loop.tools._tools.get(name))

        async def fake_run_agent_loop(messages, **kwargs):
            spawn_tool = loop.tools.get("spawn")
            assert spawn_tool is not None
            assert spawn_tool._metadata["workspace_agent_cmd"] == "harness"
            assert spawn_tool._metadata["workspace_harness_id"] == "har_0001"
            assert (
                spawn_tool._metadata["workspace_runtime"]["active_harness"]["subagent_allowed"]
                is False
            )
            return "done", [], list(messages) + [{"role": "assistant", "content": "done"}]

        loop._run_agent_loop = fake_run_agent_loop  # type: ignore[method-assign]

        result = await loop._process_message(msg)

        assert result is not None
        assert result.content == "done"

    asyncio.run(run())


def test_workspace_harness_auto_reentry_continues_when_project_completed_but_next_child_exists(
    tmp_path: Path,
) -> None:
    loop, _bus = _make_loop(tmp_path)
    (tmp_path / "harnesses").mkdir(parents=True)
    (tmp_path / "harnesses" / "control.json").write_text(
        '{"active_harness_id":"har_0001","updated_at":""}', encoding="utf-8"
    )
    (tmp_path / "harnesses" / "index.json").write_text(
        '{"harnesses":{'
        '"har_0001":{"id":"har_0001","type":"project","status":"completed","phase":"completed","awaiting_user":false,"blocked":false,"auto":true,"queue_order":1},'
        '"har_0002":{"id":"har_0002","type":"feature","parent_id":"har_0001","status":"planning","phase":"planning","awaiting_user":false,"blocked":false,"auto":false,"queue_order":2}'
        "}}",
        encoding="utf-8",
    )
    msg = InboundMessage(
        channel="feishu",
        sender_id="user1",
        chat_id="chat1",
        content="/harness auto",
        metadata={"workspace_agent_cmd": "harness", "workspace_harness_auto": True},
    )

    decision = loop._decide_harness_auto_reentry(msg)

    assert decision["should_fire"] is True
    assert decision["reason"] == "continue"


def test_workspace_harness_auto_reentry_stops_on_stop_gate_child_even_if_project_has_more_queue(
    tmp_path: Path,
) -> None:
    loop, _bus = _make_loop(tmp_path)
    (tmp_path / "harnesses").mkdir(parents=True)
    (tmp_path / "harnesses" / "control.json").write_text(
        '{"active_harness_id":"har_0001","updated_at":""}', encoding="utf-8"
    )
    (tmp_path / "harnesses" / "index.json").write_text(
        '{"harnesses":{'
        '"har_0001":{"id":"har_0001","type":"project","status":"active","phase":"planning","awaiting_user":false,"blocked":false,"auto":true,"queue_order":1},'
        '"har_0002":{"id":"har_0002","type":"feature","parent_id":"har_0001","status":"blocked","phase":"blocked","awaiting_user":false,"blocked":true,"auto":false,"queue_order":2}'
        "}}",
        encoding="utf-8",
    )
    msg = InboundMessage(
        channel="feishu",
        sender_id="user1",
        chat_id="chat1",
        content="/harness auto",
        metadata={"workspace_agent_cmd": "harness", "workspace_harness_auto": True},
    )

    decision = loop._decide_harness_auto_reentry(msg)

    assert decision["should_fire"] is False
    assert decision["reason"] == "blocked"


def test_workspace_harness_auto_continue_decision_comes_from_service_not_workspace_projection(
    tmp_path: Path,
) -> None:
    loop, _bus = _make_loop(tmp_path)
    service = HarnessService.for_workspace(tmp_path)
    service.handle_command(
        "/harness 修复 interrupt 的真实接线",
        session_key="feishu:c1",
        sender_id="u1",
    )
    msg = InboundMessage(
        channel="feishu",
        sender_id="u1",
        chat_id="c1",
        content="/harness auto",
        metadata={
            "workspace_agent_cmd": "harness",
            "workspace_harness_auto": True,
            "workspace_runtime": {"has_active_harness": False},
        },
    )

    decision = loop._decide_harness_auto_reentry(msg)

    assert decision["should_fire"] is True
    assert decision["reason"] == "continue"


def test_workspace_harness_auto_continue_ignores_stale_workspace_projection_without_store_state(
    tmp_path: Path,
) -> None:
    loop, _bus = _make_loop(tmp_path)
    msg = InboundMessage(
        channel="feishu",
        sender_id="u1",
        chat_id="c1",
        content="/harness auto",
        metadata={
            "workspace_agent_cmd": "harness",
            "workspace_harness_auto": True,
            "workspace_runtime": {
                "has_active_harness": True,
                "active_harness": {
                    "id": "har_stale",
                    "status": "active",
                    "phase": "executing",
                    "awaiting_user": False,
                    "blocked": False,
                    "auto": True,
                },
            },
        },
    )

    decision = loop._decide_harness_auto_reentry(msg)

    assert decision["should_fire"] is False
    assert decision["reason"] == "no_active_harness"


def test_workspace_harness_runtime_metadata_prefers_queue_order_over_updated_at_for_next_child(
    tmp_path: Path,
) -> None:
    loop, _bus = _make_loop(tmp_path)
    (tmp_path / "harnesses").mkdir(parents=True)
    (tmp_path / "harnesses" / "control.json").write_text(
        '{"active_harness_id":"har_0001","updated_at":""}', encoding="utf-8"
    )
    (tmp_path / "harnesses" / "index.json").write_text(
        '{"harnesses":{'
        '"har_0001":{"id":"har_0001","type":"project","status":"active","phase":"planning","awaiting_user":false,"blocked":false,"auto":true,"queue_order":1},'
        '"har_0002":{"id":"har_0002","type":"feature","parent_id":"har_0001","status":"planning","phase":"planning","awaiting_user":false,"blocked":false,"auto":false,"queue_order":2,"updated_at":"2026-04-06T01:00:00"},'
        '"har_0003":{"id":"har_0003","type":"feature","parent_id":"har_0001","status":"planning","phase":"planning","awaiting_user":false,"blocked":false,"auto":false,"queue_order":3,"updated_at":"2026-04-06T09:00:00"}'
        "}}",
        encoding="utf-8",
    )
    msg = InboundMessage(
        channel="feishu",
        sender_id="user1",
        chat_id="chat1",
        content="/harness auto",
        metadata={"workspace_agent_cmd": "harness", "workspace_harness_auto": True},
    )

    runtime_meta = loop._extract_runtime_metadata(msg)

    assert runtime_meta["next_runnable_child"]["id"] == "har_0002"


def test_workspace_harness_postprocess_uses_service_apply_closeout(tmp_path: Path) -> None:
    loop, _bus = _make_loop(tmp_path)
    service = HarnessService.for_workspace(tmp_path)
    service.handle_command(
        "/harness 修复 interrupt 的真实接线",
        session_key="feishu:c1",
        sender_id="u1",
    )
    msg = InboundMessage(
        channel="feishu",
        sender_id="u1",
        chat_id="c1",
        content="/harness",
        metadata={"workspace_agent_cmd": "harness"},
    )
    update = """```json
    {"harness": {"status": "completed", "phase": "completed", "summary": "done", "verification_status": "passed", "verification_summary": "focused tests passed", "git_delivery_status": "no_commit_required", "git_delivery_summary": "analysis-only"}}
    ```"""

    processed = loop._postprocess_workspace_agent_output(msg, update)

    assert "focused tests passed" in processed


def test_workspace_harness_auto_continue_follow_up_uses_service_origin_sender(
    tmp_path: Path,
) -> None:
    async def run() -> None:
        loop, bus = _make_loop(tmp_path)
        service = HarnessService.for_workspace(tmp_path)
        service.handle_command(
            "/harness 修复 interrupt 的真实接线",
            session_key="feishu:c1",
            sender_id="user1",
        )
        msg = InboundMessage(
            channel="feishu",
            sender_id="system",
            chat_id="c1",
            content="/harness auto",
            metadata={
                "workspace_agent_cmd": "harness",
                "workspace_harness_auto": True,
                "_completion_notice_mention_user_id": "user1",
            },
        )

        await loop._schedule_harness_auto_continue(msg)

        follow_up = await asyncio.wait_for(bus.consume_inbound(), timeout=1.0)

        assert follow_up.sender_id == "system"
        assert follow_up.metadata["_origin_sender_id"] == "user1"

    asyncio.run(run())


def test_stream_completion_notice_skips_when_harness_auto_will_continue(tmp_path: Path) -> None:
    loop, _bus = _make_loop(tmp_path)
    loop.channels_config = ChannelsConfig.model_validate(
        {
            "feishu": {
                "enabled": True,
                "streaming": True,
                "streamingCompletionNoticeEnabled": True,
                "streamingCompletionNoticeText": "✅ 回复完成",
                "streamingCompletionNoticeMentionUser": True,
            }
        }
    )
    msg = InboundMessage(
        channel="feishu",
        sender_id="user1",
        chat_id="chat1",
        content="/harness auto",
        metadata={"workspace_agent_cmd": "harness", "workspace_harness_auto": True},
    )
    response = OutboundMessage(
        channel="feishu", chat_id="chat1", content="done", metadata={"_streamed": True}
    )

    with patch.object(AgentLoop, "_should_schedule_harness_auto_continue", return_value=True):
        loop._maybe_mark_stream_completion_notice(
            msg,
            response,
            stream_started_at=1.0,
            stream_finished_at=2.0,
            stream_chunk_count=2,
            stream_char_count=20,
        )

    assert "_completion_notice" not in response.metadata
    assert "_completion_notice_mention_user_id" not in response.metadata


def test_stream_completion_notice_marks_harness_auto_stop_with_mention(tmp_path: Path) -> None:
    loop, _bus = _make_loop(tmp_path)
    loop.channels_config = ChannelsConfig.model_validate(
        {
            "feishu": {
                "enabled": True,
                "streaming": True,
                "streamingCompletionNoticeEnabled": True,
                "streamingCompletionNoticeText": "✅ 回复完成",
                "streamingCompletionNoticeMentionUser": True,
            }
        }
    )
    msg = InboundMessage(
        channel="feishu",
        sender_id="user1",
        chat_id="chat1",
        content="/harness auto",
        metadata={"workspace_agent_cmd": "harness", "workspace_harness_auto": True},
    )
    response = OutboundMessage(
        channel="feishu", chat_id="chat1", content="done", metadata={"_streamed": True}
    )

    with patch.object(AgentLoop, "_should_schedule_harness_auto_continue", return_value=False):
        loop._maybe_mark_stream_completion_notice(
            msg,
            response,
            stream_started_at=1.0,
            stream_finished_at=2.0,
            stream_chunk_count=2,
            stream_char_count=20,
        )

    assert response.metadata["_completion_notice"] is True
    assert response.metadata["_completion_notice_text"] == "✅ 回复完成"
    assert response.metadata["_completion_notice_mention_user_id"] == "user1"


def test_stream_completion_notice_on_system_harness_auto_stop_mentions_origin_user(
    tmp_path: Path,
) -> None:
    loop, _bus = _make_loop(tmp_path)
    loop.channels_config = ChannelsConfig.model_validate(
        {
            "feishu": {
                "enabled": True,
                "streaming": True,
                "streamingCompletionNoticeEnabled": True,
                "streamingCompletionNoticeText": "✅ 回复完成",
                "streamingCompletionNoticeMentionUser": True,
            }
        }
    )
    msg = InboundMessage(
        channel="feishu",
        sender_id="system",
        chat_id="chat1",
        content="/harness auto",
        metadata={
            "workspace_agent_cmd": "harness",
            "workspace_harness_auto": True,
            "_auto_continue": True,
            "_origin_sender_id": "user1",
        },
    )
    response = OutboundMessage(
        channel="feishu", chat_id="chat1", content="done", metadata={"_streamed": True}
    )

    with patch.object(AgentLoop, "_should_schedule_harness_auto_continue", return_value=False):
        loop._maybe_mark_stream_completion_notice(
            msg,
            response,
            stream_started_at=1.0,
            stream_finished_at=2.0,
            stream_chunk_count=2,
            stream_char_count=20,
        )

    assert response.metadata["_completion_notice"] is True
    assert response.metadata["_completion_notice_mention_user_id"] == "user1"


def test_stream_completion_notice_skips_short_plain_reply(tmp_path: Path) -> None:
    loop, _bus = _make_loop(tmp_path)
    loop.channels_config = ChannelsConfig.model_validate(
        {
            "feishu": {
                "enabled": True,
                "streaming": True,
                "streamingCompletionNoticeEnabled": True,
                "streamingCompletionNoticeText": "✅ 回复完成",
            }
        }
    )
    msg = InboundMessage(
        channel="feishu",
        sender_id="user1",
        chat_id="chat1",
        content="hi",
        metadata={},
    )
    response = OutboundMessage(
        channel="feishu", chat_id="chat1", content="短回复", metadata={"_streamed": True}
    )

    loop._maybe_mark_stream_completion_notice(
        msg,
        response,
        stream_started_at=1.0,
        stream_finished_at=2.0,
        stream_chunk_count=2,
        stream_char_count=20,
    )

    assert "_completion_notice" not in response.metadata


def test_stream_completion_notice_marks_long_reply_even_without_workflow(tmp_path: Path) -> None:
    loop, _bus = _make_loop(tmp_path)
    loop.channels_config = ChannelsConfig.model_validate(
        {
            "feishu": {
                "enabled": True,
                "streaming": True,
                "streamingCompletionNoticeEnabled": True,
                "streamingCompletionNoticeText": "✅ 回复完成",
            }
        }
    )
    msg = InboundMessage(
        channel="feishu",
        sender_id="user1",
        chat_id="chat1",
        content="解释一下",
        metadata={},
    )
    response = OutboundMessage(
        channel="feishu",
        chat_id="chat1",
        content=("很长的解释" * 200),
        metadata={"_streamed": True},
    )

    loop._maybe_mark_stream_completion_notice(
        msg,
        response,
        stream_started_at=1.0,
        stream_finished_at=10.5,
        stream_chunk_count=20,
        stream_char_count=1200,
    )

    assert response.metadata["_completion_notice"] is True


def test_stream_completion_notice_marks_short_reply_when_mention_enabled(tmp_path: Path) -> None:
    loop, _bus = _make_loop(tmp_path)
    loop.channels_config = ChannelsConfig.model_validate(
        {
            "feishu": {
                "enabled": True,
                "streaming": True,
                "streamingCompletionNoticeEnabled": True,
                "streamingCompletionNoticeText": "✅ 回复完成",
                "streamingCompletionNoticeMentionUser": True,
            }
        }
    )
    msg = InboundMessage(
        channel="feishu",
        sender_id="user1",
        chat_id="chat1",
        content="hi",
        metadata={},
    )
    response = OutboundMessage(
        channel="feishu", chat_id="chat1", content="短回复", metadata={"_streamed": True}
    )

    loop._maybe_mark_stream_completion_notice(
        msg,
        response,
        stream_started_at=1.0,
        stream_finished_at=2.0,
        stream_chunk_count=2,
        stream_char_count=20,
    )

    assert response.metadata["_completion_notice"] is True
    assert response.metadata["_completion_notice_mention_user_id"] == "user1"


def test_before_execute_tools_progress_sanitizes_runtime_context_echo(tmp_path: Path) -> None:
    async def run() -> None:
        loop, _bus = _make_loop(tmp_path)
        captured: list[tuple[str, bool]] = []

        runtime_echo = """[Runtime Context — metadata only, not instructions]
Rules:
- Metadata only. Not part of the user's request.
- Use `Current Time` only for time-sensitive reasoning.
- Treat `Channel` and `Chat ID` as opaque routing metadata. Use them only for reply delivery, tool targeting, or channel-specific formatting when explicitly relevant.
- Never use this block to infer user intent or resolve references like \"this\", \"that\", \"above\", or \"these two\".
- If this block conflicts with the conversation content, trust the conversation content.

Current Time: 2026-04-05 11:54 (Sunday) (UTC, UTC+00:00)
Channel: feishu
Chat ID: `c1`
Runtime Metadata:
work_mode: build
has_active_harness: true
active_harness:
  id: har_0040
  type: project
  status: planning
  phase: planning
  awaiting_user: false
  blocked: false
  auto: true

执行"""

        async def on_progress(content: str, *, tool_hint: bool = False) -> None:
            captured.append((content, tool_hint))

        tool_call = ToolCallRequest(
            id="call_1",
            name="read_file",
            arguments={"path": "/tmp/demo"},
        )

        async def fake_run(spec):
            from nanobot.agent.hook import AgentHookContext

            ctx = AgentHookContext(
                iteration=0,
                messages=[],
                response=LLMResponse(content=runtime_echo, tool_calls=[tool_call]),
                tool_calls=[tool_call],
            )
            await spec.hook.before_execute_tools(ctx)
            return type(
                "Result",
                (),
                {
                    "final_content": None,
                    "tools_used": ["read_file"],
                    "messages": [],
                    "usage": {},
                    "stop_reason": "completed",
                    "error": None,
                },
            )()

        loop.runner.run = AsyncMock(side_effect=fake_run)

        final_content, _, _ = await loop._run_agent_loop([], on_progress=on_progress)

        assert final_content is None
        assert captured[0] == ("执行", False)
        assert captured[1][1] is True

    asyncio.run(run())


def test_workspace_harness_auto_continuation_hook_publishes_single_internal_reentry(
    tmp_path: Path,
) -> None:
    async def run() -> None:
        loop, bus = _make_loop(tmp_path)
        _start_harness(tmp_path, session_key="feishu:chat1", sender_id="user1")
        msg = InboundMessage(
            channel="feishu",
            sender_id="user1",
            chat_id="chat1",
            content="/harness auto",
            metadata={
                "workspace_agent_cmd": "harness",
                "workspace_harness_auto": True,
                "workspace_agent_input": "prepared once",
                "workspace_runtime": {
                    "has_active_harness": True,
                    "active_harness": {
                        "id": "har_0038",
                        "status": "active",
                        "phase": "executing",
                        "awaiting_user": False,
                        "blocked": False,
                        "auto": True,
                    },
                },
            },
        )

        loop._run_pre_reply_consolidation = AsyncMock(return_value=True)
        loop._select_history_for_reply = MagicMock(return_value=[])
        loop._save_turn = MagicMock()
        loop.sessions.save = MagicMock()
        loop._schedule_background = lambda coro: coro.close()
        loop.tools.get = MagicMock(return_value=None)
        loop._run_agent_loop = AsyncMock(
            return_value=("done", [], [{"role": "assistant", "content": "done"}])
        )

        with (
            patch.object(AgentLoop, "_postprocess_workspace_agent_output", return_value="done"),
            patch.object(AgentLoop, "_should_schedule_harness_auto_continue", return_value=True),
        ):
            result = await loop._process_message(msg)

        assert result is not None
        follow_up = await asyncio.wait_for(bus.consume_inbound(), timeout=1.0)
        assert follow_up.metadata["_auto_continue"] is True
        assert follow_up.metadata["_origin_sender_id"] == "user1"
        assert follow_up.metadata["workspace_agent_cmd"] == "harness"
        assert follow_up.metadata["workspace_harness_auto"] is True
        assert follow_up.session_key == msg.session_key

    asyncio.run(run())


def test_workspace_harness_auto_continuation_hook_allows_chained_system_reentry(
    tmp_path: Path,
) -> None:
    async def run() -> None:
        loop, bus = _make_loop(tmp_path)
        _start_harness(tmp_path, session_key="feishu:chat1", sender_id="user1")
        msg = InboundMessage(
            channel="feishu",
            sender_id="system",
            chat_id="chat1",
            content="/harness auto",
            metadata={
                "workspace_agent_cmd": "harness",
                "workspace_harness_auto": True,
                "workspace_agent_input": "prepared twice",
                "_auto_continue": True,
                "_origin_sender_id": "user1",
                "workspace_runtime": {
                    "has_active_harness": True,
                    "active_harness": {
                        "id": "har_0038",
                        "status": "active",
                        "phase": "executing",
                        "awaiting_user": False,
                        "blocked": False,
                        "auto": True,
                    },
                },
            },
        )

        loop._run_pre_reply_consolidation = AsyncMock(return_value=True)
        loop._select_history_for_reply = MagicMock(return_value=[])
        loop._save_turn = MagicMock()
        loop.sessions.save = MagicMock()
        loop._schedule_background = lambda coro: coro.close()
        loop.tools.get = MagicMock(return_value=None)
        loop._run_agent_loop = AsyncMock(
            return_value=("done", [], [{"role": "assistant", "content": "done"}])
        )

        with (
            patch.object(AgentLoop, "_postprocess_workspace_agent_output", return_value="done"),
            patch.object(AgentLoop, "_should_schedule_harness_auto_continue", return_value=True),
        ):
            result = await loop._process_message(msg)

        assert result is not None
        follow_up = await asyncio.wait_for(bus.consume_inbound(), timeout=1.0)
        assert follow_up.sender_id == "system"
        assert follow_up.metadata["_auto_continue"] is True
        assert follow_up.metadata["_origin_sender_id"] == "user1"
        assert follow_up.metadata["workspace_harness_auto"] is True
        assert follow_up.session_key == msg.session_key

    asyncio.run(run())


def test_workspace_harness_auto_continuation_hook_skips_when_user_decision_needed(
    tmp_path: Path,
) -> None:
    async def run() -> None:
        loop, bus = _make_loop(tmp_path)
        msg = InboundMessage(
            channel="feishu",
            sender_id="user1",
            chat_id="chat1",
            content="/harness auto",
            metadata={
                "workspace_agent_cmd": "harness",
                "workspace_harness_auto": True,
                "workspace_agent_input": "prepared once",
            },
        )

        loop._run_pre_reply_consolidation = AsyncMock(return_value=True)
        loop._select_history_for_reply = MagicMock(return_value=[])
        loop._save_turn = MagicMock()
        loop.sessions.save = MagicMock()
        loop._schedule_background = lambda coro: coro.close()
        loop.tools.get = MagicMock(return_value=None)
        loop._run_agent_loop = AsyncMock(
            return_value=("done", [], [{"role": "assistant", "content": "done"}])
        )

        with (
            patch.object(AgentLoop, "_postprocess_workspace_agent_output", return_value="done"),
            patch.object(AgentLoop, "_should_schedule_harness_auto_continue", return_value=False),
        ):
            result = await loop._process_message(msg)

        assert result is not None
        assert bus.inbound_size == 0

    asyncio.run(run())


def test_workspace_harness_auto_keeps_running_in_verify_phase_by_scheduling_follow_up(
    tmp_path: Path,
) -> None:
    async def run() -> None:
        loop, bus = _make_loop(tmp_path)
        _start_harness(
            tmp_path,
            session_key="telegram:chat1",
            sender_id="user1",
            phase="verify",
        )
        msg = InboundMessage(
            channel="telegram",
            sender_id="user1",
            chat_id="chat1",
            content="/harness auto",
            metadata={
                "workspace_agent_cmd": "harness",
                "workspace_agent_input": "prepared iteration 1",
                "workspace_harness_auto": True,
                "workspace_runtime": {
                    "has_active_harness": True,
                    "active_harness": {
                        "id": "har_0001",
                        "status": "active",
                        "phase": "verify",
                        "awaiting_user": False,
                        "blocked": False,
                        "auto": True,
                    },
                },
            },
        )

        loop._run_agent_loop = AsyncMock(
            return_value=("iteration-1", [], [{"role": "assistant", "content": "iteration-1"}])
        )
        loop._run_pre_reply_consolidation = AsyncMock(return_value=True)
        loop._select_history_for_reply = MagicMock(return_value=[])
        loop.context.build_messages = MagicMock(return_value=[])
        loop._save_turn = MagicMock()
        loop.sessions.save = MagicMock()
        loop._schedule_background = lambda coro: coro.close()
        loop.tools.get = MagicMock(return_value=None)

        with patch.object(
            AgentLoop, "_postprocess_workspace_agent_output", return_value="iteration-1-post"
        ):
            result = await loop._process_message(msg)

        assert result is not None
        assert result.content == "iteration-1-post"
        follow_up = await asyncio.wait_for(bus.consume_inbound(), timeout=1.0)
        assert follow_up.metadata["_auto_continue"] is True
        assert follow_up.metadata["workspace_harness_auto"] is True

    asyncio.run(run())


def test_workspace_harness_auto_skips_pre_reply_consolidation(tmp_path: Path) -> None:
    async def run() -> None:
        loop, _bus = _make_loop(tmp_path)
        msg = InboundMessage(
            channel="telegram",
            sender_id="user1",
            chat_id="chat1",
            content="/harness auto",
            metadata={
                "workspace_agent_cmd": "harness",
                "workspace_agent_input": "prepared iteration 1",
                "workspace_harness_auto": True,
                "workspace_runtime": {
                    "has_active_harness": True,
                    "active_harness": {
                        "id": "har_0001",
                        "status": "active",
                        "phase": "executing",
                        "awaiting_user": False,
                        "blocked": False,
                        "auto": True,
                    },
                },
            },
        )

        loop._run_pre_reply_consolidation = AsyncMock(return_value=True)
        loop._run_agent_loop = AsyncMock(
            return_value=("iteration-1", [], [{"role": "assistant", "content": "iteration-1"}])
        )
        loop._select_history_for_reply = MagicMock(return_value=[])
        loop.context.build_messages = MagicMock(return_value=[])
        loop._save_turn = MagicMock()
        loop.sessions.save = MagicMock()
        loop._schedule_background = lambda coro: coro.close()
        loop.tools.get = MagicMock(return_value=None)

        with patch.object(
            AgentLoop, "_postprocess_workspace_agent_output", return_value="iteration-1-post"
        ):
            result = await loop._process_message(msg)

        assert result is not None
        assert result.content == "iteration-1-post"
        loop._run_pre_reply_consolidation.assert_not_awaited()

    asyncio.run(run())

    async def run() -> None:
        loop, bus = _make_loop(tmp_path)
        _start_harness(tmp_path, session_key="telegram:chat1", sender_id="user1")
        msg = InboundMessage(
            channel="telegram",
            sender_id="user1",
            chat_id="chat1",
            content="/harness auto",
            metadata={
                "workspace_agent_cmd": "harness",
                "workspace_agent_input": "prepared iteration 1",
                "workspace_harness_auto": True,
                "workspace_runtime": {
                    "has_active_harness": True,
                    "active_harness": {
                        "id": "har_0001",
                        "status": "active",
                        "phase": "executing",
                        "awaiting_user": False,
                        "blocked": False,
                        "auto": True,
                    },
                },
            },
        )

        loop._run_agent_loop = AsyncMock(
            return_value=("iteration-1", [], [{"role": "assistant", "content": "iteration-1"}])
        )
        loop._run_pre_reply_consolidation = AsyncMock(return_value=True)
        loop._select_history_for_reply = MagicMock(return_value=[])
        loop.context.build_messages = MagicMock(return_value=[])
        loop._save_turn = MagicMock()
        loop.sessions.save = MagicMock()
        loop._schedule_background = lambda coro: coro.close()
        loop.tools.get = MagicMock(return_value=None)

        with (
            patch.object(
                AgentLoop, "_postprocess_workspace_agent_output", return_value="iteration-1-post"
            ),
            patch("nanobot.command.workspace_bridge._prepare_agent_input") as mock_prepare,
        ):
            result = await loop._process_message(msg)

        assert result is not None
        assert result.content == "iteration-1-post"
        assert loop._run_agent_loop.await_count == 1
        mock_prepare.assert_not_called()
        follow_up = await asyncio.wait_for(bus.consume_inbound(), timeout=1.0)
        assert follow_up.metadata["_auto_continue"] is True

    asyncio.run(run())


def test_workspace_harness_auto_skips_follow_up_when_harness_requests_user_decision(
    tmp_path: Path,
) -> None:
    async def run() -> None:
        loop, bus = _make_loop(tmp_path)
        msg = InboundMessage(
            channel="telegram",
            sender_id="user1",
            chat_id="chat1",
            content="/harness auto",
            metadata={
                "workspace_agent_cmd": "harness",
                "workspace_agent_input": "prepared iteration 1",
                "workspace_harness_auto": True,
                "workspace_runtime": {
                    "has_active_harness": True,
                    "active_harness": {
                        "id": "har_0001",
                        "status": "awaiting_decision",
                        "phase": "planning",
                        "awaiting_user": True,
                        "blocked": False,
                        "auto": True,
                    },
                },
            },
        )

        loop._run_agent_loop = AsyncMock(
            return_value=("iteration-1", [], [{"role": "assistant", "content": "iteration-1"}])
        )
        loop._run_pre_reply_consolidation = AsyncMock(return_value=True)
        loop._select_history_for_reply = MagicMock(return_value=[])
        loop.context.build_messages = MagicMock(return_value=[])
        loop._save_turn = MagicMock()
        loop.sessions.save = MagicMock()
        loop._schedule_background = lambda coro: coro.close()
        loop.tools.get = MagicMock(return_value=None)

        with patch.object(
            AgentLoop, "_postprocess_workspace_agent_output", return_value="iteration-1-post"
        ):
            result = await loop._process_message(msg)

        assert result is not None
        assert result.content == "iteration-1-post"
        assert bus.inbound_size == 0

    asyncio.run(run())


def test_subagent_system_message_preserves_harness_postprocess_and_auto_follow_up(
    tmp_path: Path,
) -> None:
    async def run() -> None:
        loop, bus = _make_loop(tmp_path)
        _start_harness(tmp_path, session_key="system:feishu:chat1", sender_id="user1")
        msg = InboundMessage(
            channel="system",
            sender_id="subagent",
            chat_id="feishu:chat1",
            content="[Subagent result]",
            metadata={
                "workspace_agent_cmd": "harness",
                "workspace_harness_auto": True,
                "_origin_sender_id": "user1",
                "workspace_runtime": {
                    "has_active_harness": True,
                    "active_harness": {
                        "id": "har_0001",
                        "status": "active",
                        "phase": "executing",
                        "awaiting_user": False,
                        "blocked": False,
                        "auto": True,
                    },
                },
            },
        )

        loop._run_pre_reply_consolidation = AsyncMock(return_value=True)
        loop._save_turn = MagicMock()
        loop.sessions.save = MagicMock()
        loop._schedule_background = lambda coro: coro.close()
        loop._run_agent_loop = AsyncMock(
            return_value=(
                "raw-subagent-summary",
                [],
                [{"role": "assistant", "content": "raw-subagent-summary"}],
            )
        )

        with (
            patch.object(
                AgentLoop,
                "_postprocess_workspace_agent_output",
                return_value="postprocessed-subagent-summary",
            ) as mock_post,
            patch.object(AgentLoop, "_should_schedule_harness_auto_continue", return_value=True),
        ):
            result = await loop._process_message(msg)

        assert result is not None
        assert result.channel == "feishu"
        assert result.chat_id == "chat1"
        assert result.content == "postprocessed-subagent-summary"
        mock_post.assert_called_once()
        follow_up = await asyncio.wait_for(bus.consume_inbound(), timeout=1.0)
        assert follow_up.metadata["workspace_agent_cmd"] == "harness"
        assert follow_up.metadata["workspace_harness_auto"] is True
        assert follow_up.metadata["_origin_sender_id"] == "user1"

    asyncio.run(run())


def test_subagent_system_message_sets_spawn_context_with_runtime_policy(tmp_path: Path) -> None:
    async def run() -> None:
        loop, _bus = _make_loop(tmp_path)
        msg = InboundMessage(
            channel="system",
            sender_id="subagent",
            chat_id="feishu:chat1",
            content="[Subagent result]",
            metadata={
                "workspace_agent_cmd": "harness",
                "workspace_runtime": {
                    "has_active_harness": True,
                    "active_harness": {
                        "id": "har_0001",
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

        loop._run_pre_reply_consolidation = AsyncMock(return_value=True)
        loop._save_turn = MagicMock()
        loop.sessions.save = MagicMock()
        loop._schedule_background = lambda coro: coro.close()
        loop.tools.get = MagicMock(side_effect=lambda name: loop.tools._tools.get(name))

        async def fake_run_agent_loop(messages, **kwargs):
            spawn_tool = loop.tools.get("spawn")
            assert spawn_tool is not None
            assert spawn_tool._metadata["workspace_agent_cmd"] == "harness"
            assert (
                spawn_tool._metadata["workspace_runtime"]["active_harness"]["subagent_allowed"]
                is False
            )
            return "done", [], list(messages) + [{"role": "assistant", "content": "done"}]

        loop._run_agent_loop = fake_run_agent_loop  # type: ignore[method-assign]

        result = await loop._process_message(msg)

        assert result is not None
        assert result.content == "done"

    asyncio.run(run())


def test_workspace_agent_summary_uses_prepared_input_but_persists_raw_slash_command(
    tmp_path: Path,
) -> None:
    async def run() -> None:
        loop, _bus = _make_loop(tmp_path)
        msg = InboundMessage(
            channel="telegram",
            sender_id="user1",
            chat_id="chat1",
            content="/小结 今日测试",
            metadata={
                "workspace_agent_cmd": "小结",
                "workspace_agent_input": "你正在执行 /小结 workflow。只输出正文。",
            },
        )

        captured_messages = []

        async def _run_agent_loop(messages, **kwargs):
            captured_messages[:] = messages
            return (
                "生成好的小结",
                [],
                list(messages) + [{"role": "assistant", "content": "生成好的小结"}],
            )

        loop._run_agent_loop = _run_agent_loop  # type: ignore[method-assign]
        loop._run_pre_reply_consolidation = AsyncMock(return_value=True)
        loop._select_history_for_reply = MagicMock(return_value=[])
        loop._save_turn = MagicMock()
        loop.sessions.save = MagicMock()
        loop._schedule_background = lambda coro: coro.close()
        loop.tools.get = MagicMock(return_value=None)

        result = await loop._process_message(msg)

        assert result is not None
        assert captured_messages[-1]["role"] == "user"
        assert "你正在执行 /小结 workflow。只输出正文。" in captured_messages[-1]["content"]

        saved_messages = loop._save_turn.call_args.args[1]
        current_user_message = saved_messages[1]
        assert current_user_message["role"] == "user"
        assert "/小结 今日测试" in current_user_message["content"]
        assert "你正在执行 /小结 workflow。只输出正文。" not in current_user_message["content"]

    asyncio.run(run())
