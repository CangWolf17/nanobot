from __future__ import annotations

from pathlib import Path

import pytest

from nanobot.agent.compact_state import CompactStateManager
from nanobot.agent.context import ContextBuilder
from nanobot.providers.base import LLMProvider, LLMResponse, ToolCallRequest
from nanobot.session.manager import Session


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
