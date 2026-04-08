"""Tests for the shared agent runner and its integration contracts."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nanobot.providers.base import LLMResponse, ToolCallRequest


def _make_loop(tmp_path):
    from nanobot.agent.loop import AgentLoop
    from nanobot.bus.queue import MessageBus

    bus = MessageBus()
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"

    with patch("nanobot.agent.loop.ContextBuilder"), \
         patch("nanobot.agent.loop.SessionManager"), \
         patch("nanobot.agent.loop.SubagentManager") as mock_sub_mgr:
        mock_sub_mgr.return_value.cancel_by_session = AsyncMock(return_value=0)
        loop = AgentLoop(bus=bus, provider=provider, workspace=tmp_path)
    return loop


@pytest.mark.asyncio
async def test_runner_preserves_reasoning_fields_and_tool_results():
    from nanobot.agent.runner import AgentRunner, AgentRunSpec

    provider = MagicMock()
    captured_second_call: list[dict] = []
    call_count = {"n": 0}

    async def chat_with_retry(*, messages, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return LLMResponse(
                content="thinking",
                tool_calls=[ToolCallRequest(id="call_1", name="list_dir", arguments={"path": "."})],
                reasoning_content="hidden reasoning",
                thinking_blocks=[{"type": "thinking", "thinking": "step"}],
                usage={"prompt_tokens": 5, "completion_tokens": 3},
            )
        captured_second_call[:] = messages
        return LLMResponse(content="done", tool_calls=[], usage={})

    provider.chat_with_retry = chat_with_retry
    tools = MagicMock()
    tools.get_definitions.return_value = []
    tools.execute = AsyncMock(return_value="tool result")

    runner = AgentRunner(provider)
    result = await runner.run(AgentRunSpec(
        initial_messages=[
            {"role": "system", "content": "system"},
            {"role": "user", "content": "do task"},
        ],
        tools=tools,
        model="test-model",
        max_iterations=3,
    ))

    assert result.final_content == "done"
    assert result.tools_used == ["list_dir"]
    assert result.tool_events == [
        {"name": "list_dir", "status": "ok", "detail": "tool result"}
    ]

    assistant_messages = [
        msg for msg in captured_second_call
        if msg.get("role") == "assistant" and msg.get("tool_calls")
    ]
    assert len(assistant_messages) == 1
    assert assistant_messages[0]["reasoning_content"] == "hidden reasoning"
    assert assistant_messages[0]["thinking_blocks"] == [{"type": "thinking", "thinking": "step"}]
    assert any(
        msg.get("role") == "tool" and msg.get("content") == "tool result"
        for msg in captured_second_call
    )


@pytest.mark.asyncio
async def test_runner_calls_hooks_in_order():
    from nanobot.agent.hook import AgentHook, AgentHookContext
    from nanobot.agent.runner import AgentRunner, AgentRunSpec

    provider = MagicMock()
    call_count = {"n": 0}
    events: list[tuple] = []

    async def chat_with_retry(**kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return LLMResponse(
                content="thinking",
                tool_calls=[ToolCallRequest(id="call_1", name="list_dir", arguments={"path": "."})],
            )
        return LLMResponse(content="done", tool_calls=[], usage={})

    provider.chat_with_retry = chat_with_retry
    tools = MagicMock()
    tools.get_definitions.return_value = []
    tools.execute = AsyncMock(return_value="tool result")

    class RecordingHook(AgentHook):
        async def before_iteration(self, context: AgentHookContext) -> None:
            events.append(("before_iteration", context.iteration))

        async def before_execute_tools(self, context: AgentHookContext) -> None:
            events.append((
                "before_execute_tools",
                context.iteration,
                [tc.name for tc in context.tool_calls],
            ))

        async def after_iteration(self, context: AgentHookContext) -> None:
            events.append((
                "after_iteration",
                context.iteration,
                context.final_content,
                list(context.tool_results),
                list(context.tool_events),
                context.stop_reason,
            ))

        def finalize_content(self, context: AgentHookContext, content: str | None) -> str | None:
            events.append(("finalize_content", context.iteration, content))
            return content.upper() if content else content

    runner = AgentRunner(provider)
    result = await runner.run(AgentRunSpec(
        initial_messages=[],
        tools=tools,
        model="test-model",
        max_iterations=3,
        hook=RecordingHook(),
    ))

    assert result.final_content == "DONE"
    assert events == [
        ("before_iteration", 0),
        ("before_execute_tools", 0, ["list_dir"]),
        (
            "after_iteration",
            0,
            None,
            ["tool result"],
            [{"name": "list_dir", "status": "ok", "detail": "tool result"}],
            None,
        ),
        ("before_iteration", 1),
        ("finalize_content", 1, "done"),
        ("after_iteration", 1, "DONE", [], [], "completed"),
    ]


@pytest.mark.asyncio
async def test_runner_uses_timeout_message_for_provider_errors():
    from nanobot.agent.runner import AgentRunner, AgentRunSpec

    provider = MagicMock()
    provider.chat_with_retry = AsyncMock(return_value=LLMResponse(
        content="Error calling LLM: Request timed out.",
        tool_calls=[],
        finish_reason="error",
    ))
    tools = MagicMock()
    tools.get_definitions.return_value = []

    runner = AgentRunner(provider)
    result = await runner.run(AgentRunSpec(
        initial_messages=[],
        tools=tools,
        model="test-model",
        max_iterations=1,
    ))

    assert result.stop_reason == "error"
    assert result.final_content == "模型响应超时。请稍后重试，或切换模型。"
    assert result.error == "Error calling LLM: Request timed out."


@pytest.mark.asyncio
async def test_runner_uses_timeout_message_after_retries(monkeypatch):
    from nanobot.agent.runner import AgentRunner, AgentRunSpec
    from nanobot.providers.base import LLMProvider

    class RetryingProvider(LLMProvider):
        def __init__(self):
            super().__init__()
            self.calls = 0

        async def chat(self, *args, **kwargs):
            self.calls += 1
            return LLMResponse(content="Error calling LLM: Request timed out.", tool_calls=[], finish_reason="error")

        def get_default_model(self) -> str:
            return "test-model"

    provider = RetryingProvider()
    delays: list[int] = []

    async def _fake_sleep(delay: int) -> None:
        delays.append(delay)

    monkeypatch.setattr("nanobot.providers.base.asyncio.sleep", _fake_sleep)
    tools = MagicMock()
    tools.get_definitions.return_value = []

    runner = AgentRunner(provider)
    result = await runner.run(AgentRunSpec(
        initial_messages=[],
        tools=tools,
        model="test-model",
        max_iterations=1,
    ))

    assert result.stop_reason == "error"
    assert result.final_content == "模型响应超时，已自动重试 5 次仍失败。请稍后重试，或切换模型。"
    assert result.error == "Error calling LLM: Request timed out."
    assert provider.calls == 6
    assert delays == [1, 2, 4, 8, 10]


@pytest.mark.asyncio
async def test_runner_preserves_raw_error_when_error_message_disabled():
    from nanobot.agent.runner import AgentRunner, AgentRunSpec

    provider = MagicMock()
    provider.chat_with_retry = AsyncMock(return_value=LLMResponse(
        content="Error calling LLM: Request timed out.",
        tool_calls=[],
        finish_reason="error",
    ))
    tools = MagicMock()
    tools.get_definitions.return_value = []

    runner = AgentRunner(provider)
    result = await runner.run(AgentRunSpec(
        initial_messages=[],
        tools=tools,
        model="test-model",
        max_iterations=1,
        error_message=None,
    ))

    assert result.stop_reason == "error"
    assert result.final_content == "Error calling LLM: Request timed out."
    assert result.error == "Error calling LLM: Request timed out."


@pytest.mark.asyncio
async def test_runner_streaming_hook_receives_deltas_and_end_signal():
    from nanobot.agent.hook import AgentHook, AgentHookContext
    from nanobot.agent.runner import AgentRunner, AgentRunSpec

    provider = MagicMock()
    streamed: list[str] = []
    endings: list[bool] = []

    async def chat_stream_with_retry(*, on_content_delta, **kwargs):
        await on_content_delta("he")
        await on_content_delta("llo")
        return LLMResponse(content="hello", tool_calls=[], usage={})

    provider.chat_stream_with_retry = chat_stream_with_retry
    provider.chat_with_retry = AsyncMock()
    tools = MagicMock()
    tools.get_definitions.return_value = []

    class StreamingHook(AgentHook):
        def wants_streaming(self) -> bool:
            return True

        async def on_stream(self, context: AgentHookContext, delta: str) -> None:
            streamed.append(delta)

        async def on_stream_end(self, context: AgentHookContext, *, resuming: bool) -> None:
            endings.append(resuming)

    runner = AgentRunner(provider)
    result = await runner.run(AgentRunSpec(
        initial_messages=[],
        tools=tools,
        model="test-model",
        max_iterations=1,
        hook=StreamingHook(),
    ))

    assert result.final_content == "hello"
    assert streamed == ["he", "llo"]
    assert endings == [False]
    provider.chat_with_retry.assert_not_awaited()


@pytest.mark.asyncio
async def test_runner_returns_max_iterations_fallback():
    from nanobot.agent.runner import AgentRunner, AgentRunSpec

    provider = MagicMock()
    provider.chat_with_retry = AsyncMock(return_value=LLMResponse(
        content="still working",
        tool_calls=[ToolCallRequest(id="call_1", name="list_dir", arguments={"path": "."})],
    ))
    tools = MagicMock()
    tools.get_definitions.return_value = []
    tools.execute = AsyncMock(return_value="tool result")

    runner = AgentRunner(provider)
    result = await runner.run(AgentRunSpec(
        initial_messages=[],
        tools=tools,
        model="test-model",
        max_iterations=2,
    ))

    assert result.stop_reason == "max_iterations"
    assert result.final_content == (
        "I reached the maximum number of tool call iterations (2) "
        "without completing the task. You can try breaking the task into smaller steps."
    )


@pytest.mark.asyncio
async def test_runner_executes_tools_serially_when_concurrent_disabled():
    from nanobot.agent.runner import AgentRunner, AgentRunSpec

    provider = MagicMock()
    provider.chat_with_retry = AsyncMock(return_value=LLMResponse(
        content="working",
        tool_calls=[
            ToolCallRequest(id="call_1", name="list_dir", arguments={"path": "."}),
            ToolCallRequest(id="call_2", name="read_file", arguments={"path": "foo.txt"}),
        ],
    ))
    tools = MagicMock()
    tools.get_definitions.return_value = []

    order: list[str] = []

    async def execute(name, params):
        order.append(f"start:{name}")
        return f"ok:{name}"

    tools.execute = AsyncMock(side_effect=execute)

    runner = AgentRunner(provider)
    result = await runner.run(AgentRunSpec(
        initial_messages=[],
        tools=tools,
        model="test-model",
        max_iterations=1,
        concurrent_tools=False,
    ))

    assert result.stop_reason == "max_iterations"
    assert order == ["start:list_dir", "start:read_file"]


@pytest.mark.asyncio
async def test_runner_returns_structured_tool_error():
    from nanobot.agent.runner import AgentRunner, AgentRunSpec

    provider = MagicMock()
    provider.chat_with_retry = AsyncMock(return_value=LLMResponse(
        content="working",
        tool_calls=[ToolCallRequest(id="call_1", name="list_dir", arguments={})],
    ))
    tools = MagicMock()
    tools.get_definitions.return_value = []
    tools.execute = AsyncMock(side_effect=RuntimeError("boom"))

    runner = AgentRunner(provider)

    result = await runner.run(AgentRunSpec(
        initial_messages=[],
        tools=tools,
        model="test-model",
        max_iterations=2,
        fail_on_tool_error=True,
    ))

    assert result.stop_reason == "tool_error"
    assert result.error == "Error: RuntimeError: boom"
    assert result.tool_events == [
        {"name": "list_dir", "status": "error", "detail": "boom"}
    ]


@pytest.mark.asyncio
async def test_loop_disables_concurrent_tools_when_strict_dev_mode(tmp_path):
    from nanobot.agent.runner import AgentRunResult

    loop = _make_loop(tmp_path)
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

    loop.runner.run = AsyncMock(return_value=AgentRunResult(
        final_content="done",
        messages=[],
        tools_used=[],
        usage={},
    ))

    final_content, _, _ = await loop._run_agent_loop([])

    assert final_content == "done"
    spec = loop.runner.run.await_args.args[0]
    assert spec.concurrent_tools is False


    loop = _make_loop(tmp_path)
    loop.provider.chat_with_retry = AsyncMock(return_value=LLMResponse(
        content="working",
        tool_calls=[ToolCallRequest(id="call_1", name="list_dir", arguments={})],
    ))
    loop.tools.get_definitions = MagicMock(return_value=[])
    loop.tools.execute = AsyncMock(return_value="ok")
    loop.max_iterations = 2

    final_content, _, _ = await loop._run_agent_loop([])

    assert final_content == (
        "I reached the maximum number of tool call iterations (2) "
        "without completing the task. You can try breaking the task into smaller steps."
    )


@pytest.mark.asyncio
async def test_loop_stream_filter_handles_think_only_prefix_without_crashing(tmp_path):
    loop = _make_loop(tmp_path)
    deltas: list[str] = []
    endings: list[bool] = []

    async def chat_stream_with_retry(*, on_content_delta, **kwargs):
        await on_content_delta("<think>hidden")
        await on_content_delta("</think>Hello")
        return LLMResponse(content="<think>hidden</think>Hello", tool_calls=[], usage={})

    loop.provider.chat_stream_with_retry = chat_stream_with_retry

    async def on_stream(delta: str) -> None:
        deltas.append(delta)

    async def on_stream_end(*, resuming: bool = False) -> None:
        endings.append(resuming)

    final_content, _, _ = await loop._run_agent_loop(
        [],
        on_stream=on_stream,
        on_stream_end=on_stream_end,
    )

    assert final_content == "Hello"
    assert deltas == ["Hello"]
    assert endings == [False]


@pytest.mark.asyncio
async def test_loop_stream_retry_drops_failed_attempt_partial_deltas(tmp_path):
    from nanobot.agent.runner import AgentRunner
    from nanobot.providers.base import LLMProvider

    loop = _make_loop(tmp_path)
    deltas: list[str] = []
    endings: list[bool] = []

    class ScriptedStreamingProvider(LLMProvider):
        def __init__(self):
            super().__init__()
            self.calls = 0

        async def chat(self, *args, **kwargs):
            raise NotImplementedError

        async def chat_stream(self, *args, on_content_delta=None, **kwargs):
            self.calls += 1
            if self.calls == 1:
                if on_content_delta:
                    await on_content_delta("partial leaked text ")
                return LLMResponse(content="Error calling LLM: Request timed out.", tool_calls=[], finish_reason="error")
            if on_content_delta:
                await on_content_delta("Hello")
            return LLMResponse(content="Hello", tool_calls=[], usage={})

        def get_default_model(self) -> str:
            return "test-model"

    loop.provider = ScriptedStreamingProvider()
    loop.runner = AgentRunner(loop.provider)

    async def on_stream(delta: str) -> None:
        deltas.append(delta)

    async def on_stream_end(*, resuming: bool = False) -> None:
        endings.append(resuming)

    final_content, _, _ = await loop._run_agent_loop(
        [],
        on_stream=on_stream,
        on_stream_end=on_stream_end,
    )

    assert final_content == "Hello"
    assert deltas == ["Hello"]
    assert endings == [False]


@pytest.mark.asyncio
async def test_loop_reports_retry_progress_for_timeout_errors(tmp_path):
    loop = _make_loop(tmp_path)
    progress: list[str] = []

    async def chat_with_retry(*, on_retry=None, **kwargs):
        if on_retry:
            await on_retry(
                attempt=1,
                max_retries=5,
                delay=1,
                error="Error calling LLM: Request timed out.",
            )
            await on_retry(
                attempt=2,
                max_retries=5,
                delay=2,
                error="Error calling LLM: Request timed out.",
            )
        return LLMResponse(
            content="Error calling LLM: Request timed out.",
            tool_calls=[],
            finish_reason="error",
        )

    loop.provider.chat_with_retry = chat_with_retry

    async def on_progress(content: str, *, tool_hint: bool = False) -> None:
        progress.append(content)

    final_content, _, _ = await loop._run_agent_loop([], on_progress=on_progress)

    assert progress == [
        "模型响应超时，正在自动重试（1/5）…",
        "模型响应超时，正在自动重试（2/5）…",
    ]
    assert final_content == "模型响应超时，已自动重试 2 次仍失败。请稍后重试，或切换模型。"


@pytest.mark.asyncio
async def test_subagent_uses_serial_tools_and_prompt_in_strict_dev_mode(tmp_path):
    from nanobot.agent.runner import AgentRunResult
    from nanobot.agent.subagent import SubagentManager
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
    mgr._announce_result = AsyncMock()
    mgr.runner.run = AsyncMock(return_value=AgentRunResult(
        final_content="done",
        messages=[],
        tools_used=[],
        usage={},
    ))

    await mgr._run_subagent("sub-1", "do task", "label", {"channel": "test", "chat_id": "c1"})

    spec = mgr.runner.run.await_args.args[0]
    assert spec.concurrent_tools is False
    assert "## Dev Discipline" in spec.initial_messages[0]["content"]
    assert "phase: red_required" in spec.initial_messages[0]["content"]


@pytest.mark.asyncio
async def test_subagent_max_iterations_announces_existing_fallback(tmp_path, monkeypatch):
    from nanobot.agent.subagent import SubagentManager
    from nanobot.bus.queue import MessageBus

    bus = MessageBus()
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    provider.chat_with_retry = AsyncMock(return_value=LLMResponse(
        content="working",
        tool_calls=[ToolCallRequest(id="call_1", name="list_dir", arguments={})],
    ))
    mgr = SubagentManager(provider=provider, workspace=tmp_path, bus=bus)
    mgr._announce_result = AsyncMock()

    async def fake_execute(self, name, arguments):
        return "tool result"

    monkeypatch.setattr("nanobot.agent.tools.registry.ToolRegistry.execute", fake_execute)

    await mgr._run_subagent("sub-1", "do task", "label", {"channel": "test", "chat_id": "c1"})

    mgr._announce_result.assert_awaited_once()
    args = mgr._announce_result.await_args.args
    assert args[3] == "Task completed but no final response was generated."
    assert args[5] == "ok"
