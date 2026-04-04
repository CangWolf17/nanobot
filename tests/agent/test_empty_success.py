from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from nanobot.agent.runner import AgentRunSpec, AgentRunner
from nanobot.agent.tools.registry import ToolRegistry
from nanobot.api.server import handle_chat_completions
from nanobot.bus.events import OutboundMessage
from nanobot.providers.base import LLMProvider, LLMResponse


class ScriptedProvider(LLMProvider):
    def __init__(self, responses):
        super().__init__()
        self._responses = list(responses)
        self.calls = 0

    async def chat(self, *args, **kwargs) -> LLMResponse:
        self.calls += 1
        response = self._responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response

    def get_default_model(self) -> str:
        return "test-model"


class FakeRequest:
    def __init__(self, body: dict, app: dict):
        self._body = body
        self.app = app

    async def json(self):
        return self._body


@pytest.mark.asyncio
async def test_runner_treats_empty_success_as_retryable_failure(monkeypatch) -> None:
    provider = ScriptedProvider(
        [
            LLMResponse(content=None),
            LLMResponse(content=None),
            LLMResponse(content=None),
            LLMResponse(content=None),
            LLMResponse(content=None),
            LLMResponse(content=None),
        ]
    )
    delays: list[int] = []

    async def _fake_sleep(delay: int) -> None:
        delays.append(delay)

    monkeypatch.setattr("nanobot.providers.base.asyncio.sleep", _fake_sleep)

    runner = AgentRunner(provider)
    result = await runner.run(
        AgentRunSpec(
            initial_messages=[{"role": "user", "content": "hello"}],
            tools=ToolRegistry(),
            model="test-model",
            max_iterations=4,
        )
    )

    assert result.stop_reason == "error"
    assert (
        result.final_content == "模型返回了空响应，已自动重试 5 次仍失败。请稍后重试，或切换模型。"
    )
    assert provider.calls == 6
    assert delays == [1, 2, 4, 8, 10]


@pytest.mark.asyncio
async def test_api_path_uses_same_empty_success_policy() -> None:
    request = FakeRequest(
        {
            "messages": [{"role": "user", "content": "hello"}],
            "stream": False,
        },
        {
            "agent_loop": SimpleNamespace(
                process_direct=_async_return(
                    OutboundMessage(channel="api", chat_id="default", content="")
                )
            ),
            "request_timeout": 30.0,
            "model_name": "nanobot",
            "session_locks": {},
        },
    )

    response = await handle_chat_completions(request)
    payload = json.loads(response.text)
    content = payload["choices"][0]["message"]["content"]

    assert response.status == 200
    assert "模型返回了空响应" in content
    assert content != "I've completed processing but have no response to give."


def _async_return(value):
    async def _wrapped(*args, **kwargs):
        return value

    return _wrapped
