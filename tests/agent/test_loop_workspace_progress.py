from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import asyncio
import subprocess

from nanobot.agent.loop import AgentLoop
from nanobot.bus.events import InboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.providers.base import LLMResponse


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


def test_workspace_agent_simplify_emits_progress_before_agent_run(tmp_path: Path) -> None:
    async def run() -> None:
        loop, bus = _make_loop(tmp_path)
        msg = InboundMessage(
            channel="telegram",
            sender_id="user1",
            chat_id="chat1",
            content="/simplify scripts/router.py",
            metadata={"workspace_agent_cmd": "simplify"},
        )

        result = await loop._process_message(msg)
        progress = await asyncio.wait_for(bus.consume_outbound(), timeout=1.0)

        assert progress.content == "正在生成简化方案…"
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


def test_workspace_agent_summary_uses_prepared_input_but_persists_raw_slash_command(tmp_path: Path) -> None:
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
            return "done", [], list(messages) + [{"role": "assistant", "content": "done"}]

        loop._run_agent_loop = _run_agent_loop  # type: ignore[method-assign]

        result = await loop._process_message(msg)

        assert result is not None
        assert result.content == "done"
        assert captured_messages[-1]["role"] == "user"
        assert "/小结 workflow" in captured_messages[-1]["content"]
        session = loop.sessions.get_or_create("telegram:chat1")
        assert session.messages[-2]["role"] == "user"
        assert session.messages[-2]["content"] == "/小结 今日测试"
        assert session.messages[-1]["role"] == "assistant"
        assert session.messages[-1]["content"] == "done"

    asyncio.run(run())


def test_workspace_agent_postprocess_uses_router_for_insight_output(tmp_path: Path) -> None:
    msg = InboundMessage(
        channel="feishu",
        sender_id="user1",
        chat_id="chat1",
        content="/感悟 hello",
        metadata={"workspace_agent_cmd": "感悟"},
    )
    output = "已记下。\n\n文件：`/home/admin/obsidian-vault/感悟/2026-03-29.md`"

    completed = subprocess.CompletedProcess(
        args=["router.py", "--postprocess-agent", "感悟"],
        returncode=0,
        stdout=output + "\n\n[感悟] 已自动同步到 vault\n",
        stderr="",
    )

    with (
        patch("nanobot.agent.loop.Path.home", return_value=tmp_path),
        patch("nanobot.agent.loop.subprocess.run", return_value=completed) as mock_run,
    ):
        router_path = tmp_path / ".nanobot" / "workspace" / "scripts"
        router_path.mkdir(parents=True)
        (router_path / "router.py").write_text("#!/bin/sh\n", encoding="utf-8")

        result = AgentLoop._postprocess_workspace_agent_output(msg, output)

    assert result.endswith("[感悟] 已自动同步到 vault")
    mock_run.assert_called_once()
    called_args, called_kwargs = mock_run.call_args
    assert called_args[0][-2:] == ["--postprocess-agent", "感悟"]
    assert called_kwargs["input"] == output


def test_workspace_agent_postprocess_returns_original_when_router_output_blank(
    tmp_path: Path,
) -> None:
    msg = InboundMessage(
        channel="feishu",
        sender_id="user1",
        chat_id="chat1",
        content="/感悟 hello",
        metadata={"workspace_agent_cmd": "感悟"},
    )
    output = "已记下。\n\n文件：`/home/admin/obsidian-vault/感悟/2026-03-29.md`"

    completed = subprocess.CompletedProcess(
        args=["router.py", "--postprocess-agent", "感悟"],
        returncode=0,
        stdout="\n",
        stderr="",
    )

    with (
        patch("nanobot.agent.loop.Path.home", return_value=tmp_path),
        patch("nanobot.agent.loop.subprocess.run", return_value=completed),
    ):
        router_path = tmp_path / ".nanobot" / "workspace" / "scripts"
        router_path.mkdir(parents=True)
        (router_path / "router.py").write_text("#!/bin/sh\n", encoding="utf-8")

        result = AgentLoop._postprocess_workspace_agent_output(msg, output)

    assert result == output


def test_workspace_plan_command_passes_work_mode_into_context_builder(tmp_path: Path) -> None:
    async def run() -> None:
        loop, _bus = _make_loop(tmp_path)
        msg = InboundMessage(
            channel="telegram",
            sender_id="user1",
            chat_id="chat1",
            content="/plan exec",
            metadata={"workspace_agent_cmd": "plan-exec", "workspace_work_mode": "build"},
        )

        with patch.object(loop.context, "build_messages", wraps=loop.context.build_messages) as mock_build:
            result = await loop._process_message(msg)

        assert result is not None
        assert result.content.startswith("done")
        assert mock_build.call_args.kwargs["workspace_work_mode"] == "build"

    asyncio.run(run())
