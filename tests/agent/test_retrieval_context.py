from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from nanobot.agent.loop import AgentLoop
from nanobot.agent.retrieval import RetrievalRequest, normalize_retrieval_context
from nanobot.bus.queue import MessageBus
from nanobot.providers.base import LLMResponse


class _StaticRetrievalProvider:
    def __init__(self, value: str) -> None:
        self.value = value
        self.calls: list[RetrievalRequest] = []

    async def build_context(self, request: RetrievalRequest) -> str | None:
        self.calls.append(request)
        return self.value


class _BrokenRetrievalProvider:
    async def build_context(self, request: RetrievalRequest) -> str | None:  # noqa: ARG002
        raise RuntimeError("retrieval exploded")


def _make_loop(
    tmp_path: Path,
    *,
    retrieval_provider=None,
) -> AgentLoop:
    bus = MessageBus()
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    provider.generation.max_tokens = 4096
    provider.chat_with_retry = AsyncMock(return_value=LLMResponse(content="done", tool_calls=[]))
    provider.chat_stream_with_retry = AsyncMock(
        return_value=LLMResponse(content="done", tool_calls=[])
    )
    loop = AgentLoop(
        bus=bus,
        provider=provider,
        workspace=tmp_path,
        model="test-model",
        retrieval_provider=retrieval_provider,
    )
    loop.tools.get_definitions = MagicMock(return_value=[])
    loop.memory_consolidator.maybe_consolidate_by_tokens = AsyncMock(return_value=None)
    loop.commands.dispatch = AsyncMock(return_value=None)
    return loop


@pytest.mark.asyncio
async def test_process_direct_omits_retrieval_context_when_disabled(tmp_path: Path) -> None:
    loop = _make_loop(tmp_path)
    seen: dict[str, str | None] = {}
    real_build_messages = loop.context.build_messages

    def _build_messages(*args, **kwargs):
        seen["retrieval_context"] = kwargs.get("retrieval_context")
        return real_build_messages(*args, **kwargs)

    loop.context.build_messages = _build_messages  # type: ignore[method-assign]

    await loop.process_direct(
        "hello",
        session_key="cli:test",
        metadata={"retrieval_context": "Should stay disabled."},
    )

    assert seen["retrieval_context"] is None


@pytest.mark.asyncio
async def test_process_direct_includes_metadata_retrieval_context_when_enabled(tmp_path: Path) -> None:
    loop = _make_loop(tmp_path)
    loop.memory_config.retrieval_enabled = True
    seen: dict[str, str | None] = {}
    real_build_messages = loop.context.build_messages

    def _build_messages(*args, **kwargs):
        seen["retrieval_context"] = kwargs.get("retrieval_context")
        return real_build_messages(*args, **kwargs)

    loop.context.build_messages = _build_messages  # type: ignore[method-assign]

    await loop.process_direct(
        "hello",
        session_key="cli:test",
        metadata={"retrieval_context": "Archived fact:\n\nPhase 2 owns the retrieval seam."},
    )

    assert seen["retrieval_context"] == "Archived fact:\nPhase 2 owns the retrieval seam."


@pytest.mark.asyncio
async def test_process_direct_invokes_custom_retrieval_provider_when_enabled(tmp_path: Path) -> None:
    retrieval = _StaticRetrievalProvider("Remember: retrieval adapters stay outside core.")
    loop = _make_loop(tmp_path, retrieval_provider=retrieval)
    loop.memory_config.retrieval_enabled = True
    seen: dict[str, str | None] = {}
    real_build_messages = loop.context.build_messages

    def _build_messages(*args, **kwargs):
        seen["retrieval_context"] = kwargs.get("retrieval_context")
        return real_build_messages(*args, **kwargs)

    loop.context.build_messages = _build_messages  # type: ignore[method-assign]

    await loop.process_direct("hello", session_key="cli:test")

    assert retrieval.calls
    assert seen["retrieval_context"] == "Remember: retrieval adapters stay outside core."


@pytest.mark.asyncio
async def test_process_direct_handles_retrieval_provider_failure_safely(tmp_path: Path) -> None:
    loop = _make_loop(tmp_path, retrieval_provider=_BrokenRetrievalProvider())
    loop.memory_config.retrieval_enabled = True
    seen: dict[str, str | None] = {}
    real_build_messages = loop.context.build_messages

    def _build_messages(*args, **kwargs):
        seen["retrieval_context"] = kwargs.get("retrieval_context")
        return real_build_messages(*args, **kwargs)

    loop.context.build_messages = _build_messages  # type: ignore[method-assign]

    response = await loop.process_direct("hello", session_key="cli:test")

    assert response is not None
    assert response.content == "done"
    assert seen["retrieval_context"] is None


def test_normalize_retrieval_context_clips_to_budget() -> None:
    text = "line one\n\nline two\n" + ("x" * 40)

    result = normalize_retrieval_context(text, max_chars=20)

    assert result is not None
    assert "line one\nline two" in result
    assert result.endswith("... (retrieval truncated)")
