import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from nanobot.agent.loop import AgentLoop
from nanobot.bus.events import InboundMessage, OutboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.config.schema import ChannelsConfig
from nanobot.harness.service import HarnessApplyResult, HarnessAutoContinueDecision, HarnessService
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


def test_workspace_agent_diagnose_emits_progress_before_agent_run(tmp_path: Path) -> None:
    async def run() -> None:
        loop, bus = _make_loop(tmp_path)
        msg = InboundMessage(
            channel="telegram",
            sender_id="user1",
            chat_id="chat1",
            content="/诊断 登录失败",
            metadata={"workspace_agent_cmd": "诊断"},
        )

        result = await loop._process_message(msg)
        progress = await asyncio.wait_for(bus.consume_outbound(), timeout=1.0)

        assert progress.content == "正在诊断问题…"
        assert progress.metadata["_progress"] is True
        assert result is not None
        assert result.content == "done"

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
        '{"harnesses":{"har_0001":{"id":"har_0001","type":"feature","status":"active","phase":"executing","awaiting_user":false,"blocked":false,"auto":true,"executor_mode":"auto","subagent_allowed":true,"runner":"subagent"}}}',
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
    assert runtime_meta["active_harness"]["subagent_allowed"] is True
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


def test_dispatch_uses_transport_session_key_for_system_message_without_override(
    tmp_path: Path,
) -> None:
    async def run() -> None:
        loop, _bus = _make_loop(tmp_path)
        msg = InboundMessage(
            channel="system",
            sender_id="subagent",
            chat_id="feishu:chat1",
            content="[Subagent result]",
        )

        loop._process_message = AsyncMock(return_value=None)

        await loop._dispatch(msg)

        assert "feishu:chat1" in loop._session_locks
        assert "system:feishu:chat1" not in loop._session_locks

    asyncio.run(run())


def test_run_tracks_system_followup_under_transport_session_key_without_override(
    tmp_path: Path,
) -> None:
    async def run() -> None:
        loop, bus = _make_loop(tmp_path)
        started = asyncio.Event()
        release = asyncio.Event()

        async def fake_dispatch(_msg) -> None:
            started.set()
            await release.wait()

        loop._dispatch = fake_dispatch  # type: ignore[method-assign]
        await bus.publish_inbound(
            InboundMessage(
                channel="system",
                sender_id="subagent",
                chat_id="feishu:chat1",
                content="[Subagent result]",
            )
        )

        loop._running = True
        run_task = asyncio.create_task(loop.run())
        try:
            await asyncio.wait_for(started.wait(), timeout=1.0)
            assert "feishu:chat1" in loop._active_tasks
            assert "system:feishu:chat1" not in loop._active_tasks
        finally:
            release.set()
            loop._running = False
            run_task.cancel()
            try:
                await run_task
            except asyncio.CancelledError:
                pass

    asyncio.run(run())


def test_system_message_without_override_uses_transport_session_for_harness_helpers(
    tmp_path: Path,
) -> None:
    async def run() -> None:
        loop, bus = _make_loop(tmp_path)
        service = MagicMock()
        service.runtime_metadata.return_value = {"has_active_harness": False}
        service.apply_agent_update.return_value = HarnessApplyResult(
            final_content="postprocessed-subagent-summary",
            closeout_required=False,
            closeout_summary="",
        )
        service.decide_auto_continue.return_value = HarnessAutoContinueDecision(
            should_fire=True,
            reason="active",
            origin_sender_id="user1",
        )
        service.build_auto_continue_metadata.return_value = {
            "workspace_agent_cmd": "harness",
            "workspace_harness_auto": True,
            "_auto_continue": True,
            "_origin_sender_id": "user1",
        }

        msg = InboundMessage(
            channel="system",
            sender_id="subagent",
            chat_id="feishu:chat1",
            content="[Subagent result]",
            metadata={
                "workspace_agent_cmd": "harness",
                "workspace_harness_auto": True,
            },
        )

        loop._maybe_run_pre_reply_consolidation = AsyncMock(return_value=True)
        loop._save_turn = MagicMock()
        loop.sessions.save = MagicMock()
        loop._schedule_background = lambda coro: coro.close()
        loop.tools.get = MagicMock(return_value=None)
        loop._run_agent_loop = AsyncMock(
            return_value=(
                "raw-subagent-summary",
                [],
                [{"role": "assistant", "content": "raw-subagent-summary"}],
            )
        )

        with patch("nanobot.agent.loop.HarnessService.for_workspace", return_value=service):
            result = await loop._process_message(msg)

        assert result is not None
        assert result.content == "postprocessed-subagent-summary"
        assert service.runtime_metadata.call_args.kwargs["session_key"] == "feishu:chat1"
        assert service.apply_agent_update.call_args.kwargs["session_key"] == "feishu:chat1"
        assert (
            service.build_auto_continue_metadata.call_args.kwargs["session_key"] == "feishu:chat1"
        )
        assert all(
            call.kwargs["session_key"] == "feishu:chat1"
            for call in service.decide_auto_continue.call_args_list
        )

        follow_up = await asyncio.wait_for(bus.consume_inbound(), timeout=1.0)
        assert follow_up.session_key == "feishu:chat1"

    asyncio.run(run())


def test_subagent_system_message_sets_spawn_context_with_derived_runtime_policy(
    tmp_path: Path,
) -> None:
    async def run() -> None:
        loop, _bus = _make_loop(tmp_path)
        service = MagicMock()
        service.runtime_metadata.return_value = {
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
        }

        msg = InboundMessage(
            channel="system",
            sender_id="subagent",
            chat_id="feishu:chat1",
            content="[Subagent result]",
            metadata={"workspace_agent_cmd": "harness"},
        )

        loop._maybe_run_pre_reply_consolidation = AsyncMock(return_value=True)
        loop._save_turn = MagicMock()
        loop.sessions.save = MagicMock()
        loop._schedule_background = lambda coro: coro.close()
        loop.tools.get = MagicMock(side_effect=lambda name: loop.tools._tools.get(name))

        async def fake_run_agent_loop(messages, **kwargs):
            spawn_tool = loop.tools.get("spawn")
            assert spawn_tool is not None
            assert (
                spawn_tool._metadata["workspace_runtime"]["active_harness"]["subagent_allowed"]
                is False
            )
            return "done", [], [{"role": "assistant", "content": "done"}]

        loop._run_agent_loop = fake_run_agent_loop  # type: ignore[method-assign]

        with patch("nanobot.agent.loop.HarnessService.for_workspace", return_value=service):
            result = await loop._process_message(msg)

        assert result is not None

    asyncio.run(run())


def test_normal_message_background_consolidation_receives_runtime_metadata(
    tmp_path: Path,
) -> None:
    async def run() -> None:
        loop, _bus = _make_loop(tmp_path)
        msg = InboundMessage(
            channel="feishu",
            sender_id="user1",
            chat_id="chat1",
            content="hello",
            metadata={"workspace_runtime": {"work_mode": "build", "has_active_harness": False}},
        )

        loop._maybe_run_pre_reply_consolidation = AsyncMock(return_value=True)
        loop._save_turn = MagicMock()
        loop.sessions.save = MagicMock()
        loop.tools.get = MagicMock(return_value=None)

        seen: dict[str, object] = {}
        scheduled: list[asyncio.Task] = []

        async def fake_run_background(session, runtime_metadata=None):
            seen["runtime_metadata"] = runtime_metadata

        def schedule_background(coro):
            scheduled.append(asyncio.create_task(coro))

        loop._run_background_consolidation = fake_run_background  # type: ignore[method-assign]
        loop._schedule_background = schedule_background
        loop._run_agent_loop = AsyncMock(
            return_value=(
                "done",
                [],
                [{"role": "assistant", "content": "done"}],
            )
        )

        result = await loop._process_message(msg)
        assert result is not None
        await scheduled[0]

        assert seen["runtime_metadata"] == {"work_mode": "build", "has_active_harness": False}

    asyncio.run(run())


def test_system_message_uses_recent_history_fallback_when_preflight_not_clean(
    tmp_path: Path,
) -> None:
    async def run() -> None:
        loop, _bus = _make_loop(tmp_path)
        msg = InboundMessage(
            channel="system",
            sender_id="subagent",
            chat_id="feishu:chat1",
            content="[Subagent result]",
            metadata={"workspace_runtime": {"work_mode": "build", "has_active_harness": False}},
        )

        session = loop.sessions.get_or_create("feishu:chat1")
        session.messages = [{"role": "assistant", "content": "full-history"}]

        fallback_history = [{"role": "assistant", "content": "fallback-history"}]
        captured: dict[str, object] = {}

        loop._maybe_run_pre_reply_consolidation = AsyncMock(return_value=False)
        loop._select_history_for_reply = MagicMock(return_value=fallback_history)
        loop._save_turn = MagicMock()
        loop.sessions.save = MagicMock()
        loop._schedule_background = lambda coro: coro.close()
        loop.tools.get = MagicMock(return_value=None)

        def _build_messages(*, history, current_message, **kwargs):
            captured["history"] = history
            return list(history) + [{"role": "assistant", "content": current_message}]

        async def _run_agent_loop(messages, **kwargs):
            return "done", [], list(messages) + [{"role": "assistant", "content": "done"}]

        loop.context.build_messages = MagicMock(side_effect=_build_messages)
        loop._run_agent_loop = _run_agent_loop  # type: ignore[method-assign]

        result = await loop._process_message(msg)

        assert result is not None
        loop._select_history_for_reply.assert_called_once_with(
            session,
            preflight_ok=False,
            runtime_metadata={"work_mode": "build", "has_active_harness": False},
        )
        assert captured["history"] == fallback_history

    asyncio.run(run())


def test_system_message_background_consolidation_receives_runtime_metadata(tmp_path: Path) -> None:
    async def run() -> None:
        loop, _bus = _make_loop(tmp_path)
        msg = InboundMessage(
            channel="system",
            sender_id="subagent",
            chat_id="feishu:chat1",
            content="[Subagent result]",
            metadata={"workspace_runtime": {"work_mode": "build", "has_active_harness": False}},
        )

        loop._maybe_run_pre_reply_consolidation = AsyncMock(return_value=True)
        loop._save_turn = MagicMock()
        loop.sessions.save = MagicMock()
        loop.tools.get = MagicMock(return_value=None)

        seen: dict[str, object] = {}
        scheduled: list[asyncio.Task] = []

        async def fake_run_background(session, runtime_metadata=None):
            seen["runtime_metadata"] = runtime_metadata

        def schedule_background(coro):
            scheduled.append(asyncio.create_task(coro))

        loop._run_background_consolidation = fake_run_background  # type: ignore[method-assign]
        loop._schedule_background = schedule_background
        loop._run_agent_loop = AsyncMock(
            return_value=(
                "done",
                [],
                [{"role": "assistant", "content": "done"}],
            )
        )

        result = await loop._process_message(msg)
        assert result is not None
        await scheduled[0]

        assert seen["runtime_metadata"] == {"work_mode": "build", "has_active_harness": False}

    asyncio.run(run())


def test_system_message_prefers_session_key_override_for_session_lookup(tmp_path: Path) -> None:
    async def run() -> None:
        loop, _bus = _make_loop(tmp_path)
        seen: dict[str, str] = {}
        real_get_or_create = loop.sessions.get_or_create

        def _get_or_create(key: str):
            seen["session_key"] = key
            return real_get_or_create(key)

        loop.sessions.get_or_create = _get_or_create  # type: ignore[method-assign]
        loop._maybe_run_pre_reply_consolidation = AsyncMock(return_value=True)
        loop._save_turn = MagicMock()
        loop.sessions.save = MagicMock()
        loop._schedule_background = lambda coro: coro.close()
        loop.tools.get = MagicMock(return_value=None)
        loop._run_agent_loop = AsyncMock(
            return_value=(
                "done",
                [],
                [{"role": "assistant", "content": "done"}],
            )
        )

        msg = InboundMessage(
            channel="system",
            sender_id="subagent",
            chat_id="api:completions",
            content="[Subagent result]",
            session_key_override="api:session-one",
        )

        result = await loop._process_message(msg)

        assert result is not None
        assert seen["session_key"] == "api:session-one"

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
