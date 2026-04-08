from __future__ import annotations

import httpx
import openai
import pytest

import nanobot.providers.openai_responses_provider as openai_responses_provider_module
from nanobot.providers.base import GenerationSettings, LLMResponse
from nanobot.providers.openai_responses_provider import OpenAIResponsesProvider


@pytest.mark.asyncio
async def test_openai_responses_provider_forwards_generation_settings(monkeypatch) -> None:
    provider = OpenAIResponsesProvider(
        api_key="sk-test-openai",
        api_base="https://api.openai.com/v1",
        default_model="gpt-4.1-mini",
    )
    provider.generation = GenerationSettings(
        temperature=0.2, max_tokens=2048, reasoning_effort="high"
    )

    captured: dict[str, object] = {}

    async def fake_create(**kwargs):
        captured.update(kwargs)
        return {
            "status": "completed",
            "output": [
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": "ok"}],
                }
            ],
            "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
        }

    monkeypatch.setattr(provider._client.responses, "create", fake_create)

    response = await provider.chat(messages=[{"role": "user", "content": "hello"}], tools=None)

    assert isinstance(response, LLMResponse)
    assert response.content == "ok"
    assert captured["model"] == "gpt-4.1-mini"
    assert captured["max_output_tokens"] == 2048
    assert captured["temperature"] == 0.2
    assert captured["reasoning"] == {"effort": "high"}


@pytest.mark.asyncio
async def test_openai_responses_provider_chat_converts_sdk_errors_to_error_response(
    monkeypatch,
) -> None:
    provider = OpenAIResponsesProvider(
        api_key="sk-test-openai",
        api_base="https://api.openai.com/v1",
        default_model="gpt-4.1-mini",
    )

    async def fake_create(**kwargs):
        raise openai.APITimeoutError(
            request=httpx.Request("POST", "https://api.openai.com/v1/responses")
        )

    monkeypatch.setattr(provider._client.responses, "create", fake_create)

    response = await provider.chat(messages=[{"role": "user", "content": "hello"}], tools=None)

    assert isinstance(response, LLMResponse)
    assert response.finish_reason == "error"
    assert response.content is not None


@pytest.mark.asyncio
async def test_openai_responses_provider_chat_stream_passes_stream_flag_once(monkeypatch) -> None:
    provider = OpenAIResponsesProvider(
        api_key="sk-test-openai",
        api_base="https://api.openai.com/v1",
        default_model="gpt-4.1-mini",
    )

    class _FakeStream:
        def __aiter__(self):
            async def _gen():
                if False:
                    yield None

            return _gen()

    captured: dict[str, object] = {}

    async def fake_create(**kwargs):
        captured.update(kwargs)
        return _FakeStream()

    async def fake_consume(stream, on_content_delta=None):
        return "", [], "stop", {}, None

    monkeypatch.setattr(provider._client.responses, "create", fake_create)
    monkeypatch.setattr(openai_responses_provider_module, "consume_sdk_stream", fake_consume)

    response = await provider.chat_stream(
        messages=[{"role": "user", "content": "hello"}],
        tools=None,
        on_content_delta=lambda _: None,
    )

    assert isinstance(response, LLMResponse)
    assert captured["stream"] is True


@pytest.mark.asyncio
async def test_openai_responses_provider_chat_stream_converts_sdk_errors_to_error_response(
    monkeypatch,
) -> None:
    provider = OpenAIResponsesProvider(
        api_key="sk-test-openai",
        api_base="https://api.openai.com/v1",
        default_model="gpt-4.1-mini",
    )

    class _FakeStream:
        def __aiter__(self):
            async def _gen():
                if False:
                    yield None

            return _gen()

    async def fake_create(**kwargs):
        return _FakeStream()

    async def fake_consume(stream, on_content_delta=None):
        raise openai.APITimeoutError(
            request=httpx.Request("POST", "https://api.openai.com/v1/responses")
        )

    monkeypatch.setattr(provider._client.responses, "create", fake_create)
    monkeypatch.setattr(openai_responses_provider_module, "consume_sdk_stream", fake_consume)

    response = await provider.chat_stream(
        messages=[{"role": "user", "content": "hello"}],
        tools=None,
        on_content_delta=lambda _: None,
    )

    assert isinstance(response, LLMResponse)
    assert response.finish_reason == "error"
    assert response.content is not None
