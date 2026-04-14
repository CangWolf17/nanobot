from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nanobot.agent.compact_state import CompactStateManager
from nanobot.agent.context import ContextBuilder
from nanobot.agent.memory import Consolidator
from nanobot.bus.queue import MessageBus
from nanobot.config.schema import AgentDefaults
from nanobot.providers.base import LLMProvider, LLMResponse, ToolCallRequest
from nanobot.session.manager import Session

_MAX_TOOL_RESULT_CHARS = AgentDefaults().max_tool_result_chars


class ScriptedProvider(LLMProvider):
    def __init__(self, responses: list[LLMResponse]):
        super().__init__()
        self._responses = list(responses)
        self.calls = 0
        self.last_kwargs: dict[str, object] = {}

    async def chat(self, *args, **kwargs) -> LLMResponse:
        self.calls += 1
        self.last_kwargs = kwargs
        return self._responses.pop(0)

    def get_default_model(self) -> str:
        return "test-model"


def _compact_response(compact_state: str) -> LLMResponse:
    return LLMResponse(
        content=None,
        tool_calls=[
            ToolCallRequest(
                id="call_1",
                name="save_compact_state",
                arguments={"compact_state": compact_state},
            )
        ],
    )


def _session_with_messages() -> Session:
    return Session(
        key="cli:test",
        messages=[
            {"role": "user", "content": "u1", "timestamp": "2026-01-01T00:00:00"},
            {"role": "assistant", "content": "a1", "timestamp": "2026-01-01T00:00:01"},
            {"role": "user", "content": "u2", "timestamp": "2026-01-01T00:00:02"},
            {"role": "assistant", "content": "a2", "timestamp": "2026-01-01T00:00:03"},
        ],
        last_consolidated=4,
    )


@pytest.mark.asyncio
async def test_compact_state_sync_updates_session_metadata_and_offset() -> None:
    session = _session_with_messages()
    provider = ScriptedProvider([_compact_response("## Current Task\nResume work")])
    manager = CompactStateManager(provider=provider, model="test-model")

    synced = await manager.sync_session(session)

    assert synced is True
    assert session.metadata["compact_state"] == "## Current Task\nResume work"
    assert session.metadata["compact_state_offset"] == 4
    assert provider.calls == 1


@pytest.mark.asyncio
async def test_compact_state_sync_is_incremental() -> None:
    session = _session_with_messages()
    session.metadata["compact_state"] = "## Existing State\nCarry this forward"
    session.metadata["compact_state_offset"] = 2
    provider = ScriptedProvider([_compact_response("## Updated State\nContinue from u2/a2")])
    manager = CompactStateManager(provider=provider, model="test-model")

    synced = await manager.sync_session(session)
    prompt = provider.last_kwargs["messages"][1]["content"]

    assert synced is True
    assert "Carry this forward" in prompt
    assert "u2" in prompt and "a2" in prompt
    assert "u1" not in prompt and "a1" not in prompt
    assert session.metadata["compact_state_offset"] == 4


@pytest.mark.asyncio
async def test_compact_state_prompt_includes_runtime_context_exclusion_instruction() -> None:
    session = _session_with_messages()
    provider = AsyncMock()
    provider.chat_with_retry = AsyncMock(return_value=_compact_response("## Current Task\nResume work"))
    manager = CompactStateManager(provider=provider, model="test-model")

    synced = await manager.sync_session(session)

    assert synced is True
    messages = provider.chat_with_retry.await_args.kwargs["messages"]
    system_prompt = messages[0]["content"]
    user_prompt = messages[1]["content"]
    assert "Do not copy or surface runtime context / metadata blocks into the saved state or visible result." in system_prompt
    assert "If archived content contains runtime context or metadata blocks, ignore them unless they materially change the task state." in user_prompt
    assert "At the very end of the compact_state, append: `Note: runtime context is auxiliary metadata and may be unrelated to the actual problem.`" in user_prompt


def test_context_builder_includes_compact_state_separately_from_memory(tmp_path: Path) -> None:
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()
    (memory_dir / "MEMORY.md").write_text("User prefers focused verification.\n", encoding="utf-8")

    builder = ContextBuilder(tmp_path)
    prompt = builder.build_system_prompt(compact_state="## Current Task\nFinish runtime follow-ups")

    assert "# Memory" in prompt
    assert "User prefers focused verification." in prompt
    assert "# Session Compact State" in prompt
    assert "Finish runtime follow-ups" in prompt


def test_consolidator_prompt_estimation_includes_compact_state() -> None:
    provider = MagicMock()
    build_messages = MagicMock(return_value=[])
    sessions = MagicMock()
    session = Session(key="cli:test", metadata={"compact_state": "## Resume\ncontinue"})

    consolidator = Consolidator(
        store=MagicMock(),
        provider=provider,
        model="test-model",
        sessions=sessions,
        context_window_tokens=1000,
        build_messages=build_messages,
        get_tool_definitions=MagicMock(return_value=[]),
        max_completion_tokens=100,
    )

    with patch("nanobot.agent.memory.estimate_prompt_tokens_chain", return_value=(42, "mock")):
        estimated, source = consolidator.estimate_session_prompt_tokens(session)

    assert (estimated, source) == (42, "mock")
    assert build_messages.call_args.kwargs["compact_state"] == "## Resume\ncontinue"


@pytest.mark.asyncio
async def test_loop_passes_session_compact_state_into_context_build(tmp_path: Path) -> None:
    from nanobot.agent.loop import AgentLoop

    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    provider.chat_with_retry = AsyncMock(return_value=LLMResponse(content="done", tool_calls=[]))
    provider.chat_stream_with_retry = AsyncMock(return_value=LLMResponse(content="done", tool_calls=[]))

    loop = AgentLoop(
        bus=MessageBus(),
        provider=provider,
        workspace=tmp_path,
        model="test-model",
        max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
    )
    loop.consolidator.maybe_consolidate_by_tokens = AsyncMock(return_value=None)  # type: ignore[method-assign]
    loop.context.build_messages = MagicMock(
        return_value=[
            {"role": "system", "content": "system"},
            {"role": "user", "content": "hello"},
        ]
    )

    session = loop.sessions.get_or_create("cli:test")
    session.metadata["compact_state"] = "## Resume\ncontinue"

    await loop.process_direct("hello", session_key="cli:test")

    assert loop.context.build_messages.call_args.kwargs["compact_state"] == "## Resume\ncontinue"
