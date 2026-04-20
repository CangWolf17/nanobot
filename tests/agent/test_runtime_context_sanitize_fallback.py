import pytest

from nanobot.agent.context import ContextBuilder
from nanobot.agent.loop import AgentLoop


def test_sanitize_visible_output_fallback_strips_runtime_context_with_compact_rules() -> None:
    text = (
        ContextBuilder._RUNTIME_CONTEXT_TAG
        + "\nRules:\n"
        + "- Metadata only. Not part of the user's request.\n"
        + "- Use `Current Time` only for time-sensitive reasoning.\n\n"
        + "Current Time: 2026-04-14 01:52 (Tuesday) (Asia/Shanghai, UTC+08:00)\n"
        + "Channel: feishu\n"
        + "Chat ID: `ou_ee2133ebc41e8b158eeaa6d90fa08dd8`\n"
        + "Runtime Metadata:\n"
        + "has_active_harness: false\n\n"
        + "嗯，优化一下"
    )

    assert AgentLoop._sanitize_visible_output(text) == "嗯，优化一下"


@pytest.mark.asyncio
async def test_stream_filter_fallback_strips_runtime_echo_when_full_block_arrives(tmp_path):
    from tests.agent.test_runner import _make_loop
    from nanobot.providers.base import LLMResponse

    loop = _make_loop(tmp_path)
    deltas: list[str] = []
    endings: list[bool] = []
    runtime_prefix = (
        ContextBuilder._RUNTIME_CONTEXT_TAG
        + "\nRules:\n"
        + "- Metadata only. Not part of the user's request.\n"
        + "- Use `Current Time` only for time-sensitive reasoning.\n\n"
        + "Current Time: 2026-04-14 01:52 (Tuesday) (Asia/Shanghai, UTC+08:00)\n"
        + "Channel: feishu\n"
        + "Chat ID: `ou_ee2133ebc41e8b158eeaa6d90fa08dd8`\n"
        + "Runtime Metadata:\n"
        + "has_active_harness: false\n\n"
    )

    async def chat_stream_with_retry(*, on_content_delta, **kwargs):
        await on_content_delta(runtime_prefix)
        await on_content_delta("嗯，优化一下")
        return LLMResponse(content=runtime_prefix + "嗯，优化一下", tool_calls=[], usage={})

    loop.provider.chat_stream_with_retry = chat_stream_with_retry

    async def on_stream(delta: str) -> None:
        deltas.append(delta)

    async def on_stream_end(*, resuming: bool = False) -> None:
        endings.append(resuming)

    final_content, _, _ = await loop._run_agent_loop([], on_stream=on_stream, on_stream_end=on_stream_end)

    assert final_content == "嗯，优化一下"
    assert "".join(deltas) == "嗯，优化一下"
    assert endings == [False]
