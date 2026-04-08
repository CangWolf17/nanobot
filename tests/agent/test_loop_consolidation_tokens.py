import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

import nanobot.agent.memory as memory_module
from nanobot.agent.loop import AgentLoop
from nanobot.bus.queue import MessageBus
from nanobot.providers.base import LLMResponse


def _make_loop(tmp_path, *, estimated_tokens: int, context_window_tokens: int) -> AgentLoop:
    from nanobot.providers.base import GenerationSettings
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    provider.generation = GenerationSettings(max_tokens=0)
    provider.estimate_prompt_tokens.return_value = (estimated_tokens, "test-counter")
    _response = LLMResponse(content="ok", tool_calls=[])
    provider.chat_with_retry = AsyncMock(return_value=_response)
    provider.chat_stream_with_retry = AsyncMock(return_value=_response)

    loop = AgentLoop(
        bus=MessageBus(),
        provider=provider,
        workspace=tmp_path,
        model="test-model",
        context_window_tokens=context_window_tokens,
    )
    loop.tools.get_definitions = MagicMock(return_value=[])
    loop.memory_consolidator._SAFETY_BUFFER = 0
    return loop


def test_prompt_estimate_includes_session_compact_state(tmp_path) -> None:
    loop = _make_loop(tmp_path, estimated_tokens=100, context_window_tokens=200)
    seen: dict[str, str | None] = {}

    def _build_messages(*, history, current_message, channel=None, chat_id=None, compact_state=None, **kwargs):
        seen["compact_state"] = compact_state
        return [{"role": "system", "content": compact_state or ""}]

    session = loop.sessions.get_or_create("cli:test")
    session.metadata["compact_state"] = "## Current Task\nResume state"
    loop.memory_consolidator._build_messages = _build_messages  # type: ignore[method-assign]

    loop.memory_consolidator.estimate_session_prompt_tokens(session)

    assert seen["compact_state"] == "## Current Task\nResume state"


def test_prompt_estimate_omits_session_compact_state_when_disabled(tmp_path) -> None:
    loop = _make_loop(tmp_path, estimated_tokens=100, context_window_tokens=200)
    seen: dict[str, str | None] = {}

    def _build_messages(*, history, current_message, channel=None, chat_id=None, compact_state=None, **kwargs):
        seen["compact_state"] = compact_state
        return [{"role": "system", "content": compact_state or ""}]

    session = loop.sessions.get_or_create("cli:test")
    session.metadata["compact_state"] = "## Current Task\nResume state"
    loop.memory_config.compact_state_enabled = False
    loop.memory_consolidator._build_messages = _build_messages  # type: ignore[method-assign]

    loop.memory_consolidator.estimate_session_prompt_tokens(session)

    assert seen["compact_state"] is None


@pytest.mark.asyncio
async def test_process_direct_omits_session_compact_state_when_disabled(tmp_path) -> None:
    loop = _make_loop(tmp_path, estimated_tokens=100, context_window_tokens=200)
    session = loop.sessions.get_or_create("cli:test")
    session.metadata["compact_state"] = "## Current Task\nResume state"

    seen: dict[str, str | None] = {}
    real_build_messages = loop.context.build_messages

    def _build_messages(*args, **kwargs):
        seen["compact_state"] = kwargs.get("compact_state")
        return real_build_messages(*args, **kwargs)

    loop.memory_config.compact_state_enabled = False
    loop.context.build_messages = _build_messages  # type: ignore[method-assign]

    await loop.process_direct("hello", session_key="cli:test")

    assert seen["compact_state"] is None


@pytest.mark.asyncio
async def test_prompt_below_threshold_does_not_consolidate(tmp_path) -> None:
    loop = _make_loop(tmp_path, estimated_tokens=100, context_window_tokens=200)
    loop.memory_consolidator.consolidate_messages = AsyncMock(return_value=True)  # type: ignore[method-assign]

    await loop.process_direct("hello", session_key="cli:test")

    loop.memory_consolidator.consolidate_messages.assert_not_awaited()


@pytest.mark.asyncio
async def test_prompt_above_threshold_triggers_consolidation(tmp_path, monkeypatch) -> None:
    loop = _make_loop(tmp_path, estimated_tokens=1000, context_window_tokens=200)
    loop.memory_consolidator.consolidate_messages = AsyncMock(return_value=True)  # type: ignore[method-assign]
    session = loop.sessions.get_or_create("cli:test")
    session.messages = [
        {"role": "user", "content": "u1", "timestamp": "2026-01-01T00:00:00"},
        {"role": "assistant", "content": "a1", "timestamp": "2026-01-01T00:00:01"},
        {"role": "user", "content": "u2", "timestamp": "2026-01-01T00:00:02"},
    ]
    loop.sessions.save(session)
    monkeypatch.setattr(memory_module, "estimate_message_tokens", lambda _message: 500)

    await loop.process_direct("hello", session_key="cli:test")

    assert loop.memory_consolidator.consolidate_messages.await_count >= 1


@pytest.mark.asyncio
async def test_prompt_above_threshold_archives_until_next_user_boundary(tmp_path, monkeypatch) -> None:
    loop = _make_loop(tmp_path, estimated_tokens=1000, context_window_tokens=200)
    loop.memory_consolidator.consolidate_messages = AsyncMock(return_value=True)  # type: ignore[method-assign]

    session = loop.sessions.get_or_create("cli:test")
    session.messages = [
        {"role": "user", "content": "u1", "timestamp": "2026-01-01T00:00:00"},
        {"role": "assistant", "content": "a1", "timestamp": "2026-01-01T00:00:01"},
        {"role": "user", "content": "u2", "timestamp": "2026-01-01T00:00:02"},
        {"role": "assistant", "content": "a2", "timestamp": "2026-01-01T00:00:03"},
        {"role": "user", "content": "u3", "timestamp": "2026-01-01T00:00:04"},
    ]
    loop.sessions.save(session)

    token_map = {"u1": 120, "a1": 120, "u2": 120, "a2": 120, "u3": 120}
    monkeypatch.setattr(memory_module, "estimate_message_tokens", lambda message: token_map[message["content"]])

    await loop.memory_consolidator.maybe_consolidate_by_tokens(session)

    archived_chunk = loop.memory_consolidator.consolidate_messages.await_args.args[0]
    assert [message["content"] for message in archived_chunk] == ["u1", "a1", "u2", "a2"]
    assert session.last_consolidated == 4


@pytest.mark.asyncio
async def test_consolidation_loops_until_target_met(tmp_path, monkeypatch) -> None:
    """Verify maybe_consolidate_by_tokens keeps looping until under threshold."""
    loop = _make_loop(tmp_path, estimated_tokens=0, context_window_tokens=200)
    loop.memory_consolidator.consolidate_messages = AsyncMock(return_value=True)  # type: ignore[method-assign]

    session = loop.sessions.get_or_create("cli:test")
    session.messages = [
        {"role": "user", "content": "u1", "timestamp": "2026-01-01T00:00:00"},
        {"role": "assistant", "content": "a1", "timestamp": "2026-01-01T00:00:01"},
        {"role": "user", "content": "u2", "timestamp": "2026-01-01T00:00:02"},
        {"role": "assistant", "content": "a2", "timestamp": "2026-01-01T00:00:03"},
        {"role": "user", "content": "u3", "timestamp": "2026-01-01T00:00:04"},
        {"role": "assistant", "content": "a3", "timestamp": "2026-01-01T00:00:05"},
        {"role": "user", "content": "u4", "timestamp": "2026-01-01T00:00:06"},
    ]
    loop.sessions.save(session)

    call_count = [0]
    def mock_estimate(_session):
        call_count[0] += 1
        if call_count[0] == 1:
            return (500, "test")
        if call_count[0] == 2:
            return (300, "test")
        return (80, "test")

    loop.memory_consolidator.estimate_session_prompt_tokens = mock_estimate  # type: ignore[method-assign]
    monkeypatch.setattr(memory_module, "estimate_message_tokens", lambda _m: 100)

    await loop.memory_consolidator.maybe_consolidate_by_tokens(session)

    assert loop.memory_consolidator.consolidate_messages.await_count == 2
    assert session.last_consolidated == 6


@pytest.mark.asyncio
async def test_consolidation_continues_below_trigger_until_half_target(tmp_path, monkeypatch) -> None:
    """Once triggered, consolidation should continue until it drops below half threshold."""
    loop = _make_loop(tmp_path, estimated_tokens=0, context_window_tokens=200)
    loop.memory_consolidator.consolidate_messages = AsyncMock(return_value=True)  # type: ignore[method-assign]

    session = loop.sessions.get_or_create("cli:test")
    session.messages = [
        {"role": "user", "content": "u1", "timestamp": "2026-01-01T00:00:00"},
        {"role": "assistant", "content": "a1", "timestamp": "2026-01-01T00:00:01"},
        {"role": "user", "content": "u2", "timestamp": "2026-01-01T00:00:02"},
        {"role": "assistant", "content": "a2", "timestamp": "2026-01-01T00:00:03"},
        {"role": "user", "content": "u3", "timestamp": "2026-01-01T00:00:04"},
        {"role": "assistant", "content": "a3", "timestamp": "2026-01-01T00:00:05"},
        {"role": "user", "content": "u4", "timestamp": "2026-01-01T00:00:06"},
    ]
    loop.sessions.save(session)

    call_count = [0]

    def mock_estimate(_session):
        call_count[0] += 1
        if call_count[0] == 1:
            return (500, "test")
        if call_count[0] == 2:
            return (150, "test")
        return (80, "test")

    loop.memory_consolidator.estimate_session_prompt_tokens = mock_estimate  # type: ignore[method-assign]
    monkeypatch.setattr(memory_module, "estimate_message_tokens", lambda _m: 100)

    await loop.memory_consolidator.maybe_consolidate_by_tokens(session)

    assert loop.memory_consolidator.consolidate_messages.await_count == 2
    assert session.last_consolidated == 6


@pytest.mark.asyncio
async def test_preflight_consolidation_before_llm_call(tmp_path, monkeypatch) -> None:
    """Verify preflight consolidation runs before the LLM call in process_direct."""
    order: list[str] = []

    loop = _make_loop(tmp_path, estimated_tokens=0, context_window_tokens=200)

    async def track_preflight(session):
        order.append("consolidate")
        return True
    loop._run_pre_reply_consolidation = track_preflight  # type: ignore[method-assign]

    async def track_llm(*args, **kwargs):
        order.append("llm")
        return LLMResponse(content="ok", tool_calls=[])
    loop.provider.chat_with_retry = track_llm
    loop.provider.chat_stream_with_retry = track_llm

    session = loop.sessions.get_or_create("cli:test")
    session.messages = [
        {"role": "user", "content": "u1", "timestamp": "2026-01-01T00:00:00"},
        {"role": "assistant", "content": "a1", "timestamp": "2026-01-01T00:00:01"},
        {"role": "user", "content": "u2", "timestamp": "2026-01-01T00:00:02"},
    ]
    loop.sessions.save(session)
    monkeypatch.setattr(memory_module, "estimate_message_tokens", lambda _m: 500)

    call_count = [0]
    def mock_estimate(_session, *, max_history_messages=0):
        call_count[0] += 1
        return (1000 if call_count[0] <= 1 else 80, "test")
    loop.memory_consolidator.estimate_session_prompt_tokens = mock_estimate  # type: ignore[method-assign]

    await loop.process_direct("hello", session_key="cli:test")

    assert "consolidate" in order
    assert "llm" in order
    assert order.index("consolidate") < order.index("llm")




@pytest.mark.asyncio
async def test_pre_reply_consolidation_skips_when_prompt_under_budget(tmp_path) -> None:
    loop = _make_loop(tmp_path, estimated_tokens=0, context_window_tokens=200)
    _session = loop.sessions.get_or_create("cli:test")

    loop._run_pre_reply_consolidation = AsyncMock(return_value=True)
    loop.memory_consolidator.is_over_budget = lambda _session, max_history_messages=0: (False, 80, "test")  # type: ignore[method-assign]

    result = await loop.process_direct("hello", session_key="cli:test")

    assert result is not None
    assert result.content == "ok"
    loop._run_pre_reply_consolidation.assert_not_awaited()


@pytest.mark.asyncio
async def test_pre_reply_consolidation_timeout_fail_open_still_replies(tmp_path) -> None:
    loop = _make_loop(tmp_path, estimated_tokens=0, context_window_tokens=200)
    loop.memory_config.pre_reply_timeout_seconds = 0.01

    async def slow_consolidation(_session):
        await asyncio.sleep(0.05)
    loop.memory_consolidator.maybe_consolidate_by_tokens = slow_consolidation  # type: ignore[method-assign]

    result = await loop.process_direct("hello", session_key="cli:test")

    assert result is not None
    assert result.content == "ok"


@pytest.mark.asyncio
async def test_pre_reply_consolidation_timeout_records_failure_count(tmp_path, monkeypatch) -> None:
    loop = _make_loop(tmp_path, estimated_tokens=0, context_window_tokens=200)
    loop.memory_config.pre_reply_timeout_seconds = 0.01

    session = loop.sessions.get_or_create("cli:test")
    session.messages = [
        {"role": "user", "content": "u1", "timestamp": "2026-01-01T00:00:00"},
        {"role": "assistant", "content": "a1", "timestamp": "2026-01-01T00:00:01"},
        {"role": "user", "content": "u2", "timestamp": "2026-01-01T00:00:02"},
    ]
    loop.sessions.save(session)

    async def hanging_archive(_messages):
        await asyncio.sleep(1)
        return True

    loop.memory_consolidator.consolidate_messages = hanging_archive  # type: ignore[method-assign]
    loop.memory_consolidator.estimate_session_prompt_tokens = lambda _session, *, max_history_messages=0: (1000, "test")  # type: ignore[method-assign]
    loop.memory_consolidator._SAFETY_BUFFER = 0
    monkeypatch.setattr(memory_module, "estimate_message_tokens", lambda _m: 500)

    result = await loop.process_direct("hello", session_key="cli:test")

    assert result is not None
    assert result.content == "ok"
    assert loop.memory_consolidator.store._consecutive_failures == 1
    assert session.last_consolidated == 0


@pytest.mark.asyncio
async def test_repeated_timeout_cancellation_degrades_to_raw_archive_and_advances_offset(tmp_path, monkeypatch) -> None:
    loop = _make_loop(tmp_path, estimated_tokens=0, context_window_tokens=200)

    session = loop.sessions.get_or_create("cli:test")
    session.messages = [
        {"role": "user", "content": "u1", "timestamp": "2026-01-01T00:00:00"},
        {"role": "assistant", "content": "a1", "timestamp": "2026-01-01T00:00:01"},
        {"role": "user", "content": "u2", "timestamp": "2026-01-01T00:00:02"},
        {"role": "assistant", "content": "a2", "timestamp": "2026-01-01T00:00:03"},
        {"role": "user", "content": "u3", "timestamp": "2026-01-01T00:00:04"},
    ]
    loop.sessions.save(session)

    async def hanging_archive(_messages):
        await asyncio.sleep(10)
        return True

    loop.memory_consolidator.consolidate_messages = hanging_archive  # type: ignore[method-assign]
    loop.memory_consolidator.estimate_session_prompt_tokens = lambda _session, *, max_history_messages=0: (1000, "test")  # type: ignore[method-assign]
    loop.memory_consolidator._SAFETY_BUFFER = 0
    monkeypatch.setattr(memory_module, "estimate_message_tokens", lambda _m: 500)

    for _ in range(loop.memory_consolidator.store._MAX_FAILURES_BEFORE_RAW_ARCHIVE):
        try:
            await asyncio.wait_for(loop.memory_consolidator.maybe_consolidate_by_tokens(session), timeout=0.01)
        except asyncio.TimeoutError:
            pytest.fail("timeout should be absorbed into consolidation failure accounting")

    assert loop.memory_consolidator.store._consecutive_failures == 0
    assert session.last_consolidated == 2
    assert loop.memory_consolidator.store.history_file.exists()
    history = loop.memory_consolidator.store.history_file.read_text()
    assert "[RAW] 2 messages" in history
    assert "u1" in history and "a1" in history


@pytest.mark.asyncio
async def test_failed_preflight_over_budget_uses_recent_history_fallback(tmp_path) -> None:
    loop = _make_loop(tmp_path, estimated_tokens=0, context_window_tokens=200)
    loop.memory_config.recent_history_fallback_messages = 2

    async def fail_preflight(_session):
        return False
    loop._run_pre_reply_consolidation = fail_preflight  # type: ignore[method-assign]

    session = loop.sessions.get_or_create("cli:test")
    session.messages = [
        {"role": "user", "content": "u1", "timestamp": "2026-01-01T00:00:00"},
        {"role": "assistant", "content": "a1", "timestamp": "2026-01-01T00:00:01"},
        {"role": "user", "content": "u2", "timestamp": "2026-01-01T00:00:02"},
        {"role": "assistant", "content": "a2", "timestamp": "2026-01-01T00:00:03"},
        {"role": "user", "content": "u3", "timestamp": "2026-01-01T00:00:04"},
    ]
    loop.sessions.save(session)

    seen = {}

    async def fake_run_agent_loop(messages, **kwargs):
        seen["messages"] = messages
        return "ok", None, messages + [{"role": "assistant", "content": "ok"}]

    loop._run_agent_loop = fake_run_agent_loop  # type: ignore[method-assign]
    loop.memory_consolidator.is_over_budget = lambda _session, max_history_messages=0: (True, 999, "test")  # type: ignore[method-assign]

    result = await loop.process_direct("hello", session_key="cli:test")

    assert result is not None
    history = seen["messages"][1:-1]
    assert [m["content"] for m in history] == ["u3"]
