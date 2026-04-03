import asyncio

import pytest

from nanobot.providers.base import GenerationSettings, LLMProvider, LLMResponse


class ScriptedProvider(LLMProvider):
    def __init__(self, responses):
        super().__init__()
        self._responses = list(responses)
        self.calls = 0
        self.last_kwargs: dict = {}

    async def chat(self, *args, **kwargs) -> LLMResponse:
        self.calls += 1
        self.last_kwargs = kwargs
        response = self._responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response

    def get_default_model(self) -> str:
        return "test-model"


@pytest.mark.asyncio
async def test_chat_with_retry_retries_transient_error_then_succeeds(monkeypatch) -> None:
    provider = ScriptedProvider(
        [
            LLMResponse(content="429 rate limit", finish_reason="error"),
            LLMResponse(content="ok"),
        ]
    )
    delays: list[int] = []

    async def _fake_sleep(delay: int) -> None:
        delays.append(delay)

    monkeypatch.setattr("nanobot.providers.base.asyncio.sleep", _fake_sleep)

    response = await provider.chat_with_retry(messages=[{"role": "user", "content": "hello"}])

    assert response.finish_reason == "stop"
    assert response.content == "ok"
    assert provider.calls == 2
    assert delays == [1]


@pytest.mark.asyncio
async def test_chat_with_retry_reports_retry_callback(monkeypatch) -> None:
    provider = ScriptedProvider(
        [
            LLMResponse(content="429 rate limit", finish_reason="error"),
            LLMResponse(content="ok"),
        ]
    )
    retries: list[tuple[int, int, int, str | None]] = []

    async def _fake_sleep(delay: int) -> None:
        return None

    async def _on_retry(*, attempt: int, max_retries: int, delay: int, error: str | None) -> None:
        retries.append((attempt, max_retries, delay, error))

    monkeypatch.setattr("nanobot.providers.base.asyncio.sleep", _fake_sleep)

    response = await provider.chat_with_retry(
        messages=[{"role": "user", "content": "hello"}],
        on_retry=_on_retry,
    )

    assert response.content == "ok"
    assert retries == [(1, 3, 1, "429 rate limit")]


@pytest.mark.asyncio
async def test_chat_with_retry_retries_empty_success_then_succeeds(monkeypatch) -> None:
    provider = ScriptedProvider(
        [
            LLMResponse(content=None),
            LLMResponse(content="ok"),
        ]
    )
    delays: list[int] = []
    retries: list[tuple[int, int, int, str | None]] = []

    async def _fake_sleep(delay: int) -> None:
        delays.append(delay)

    async def _on_retry(*, attempt: int, max_retries: int, delay: int, error: str | None) -> None:
        retries.append((attempt, max_retries, delay, error))

    monkeypatch.setattr("nanobot.providers.base.asyncio.sleep", _fake_sleep)

    response = await provider.chat_with_retry(
        messages=[{"role": "user", "content": "hello"}],
        on_retry=_on_retry,
    )

    assert response.finish_reason == "stop"
    assert response.content == "ok"
    assert provider.calls == 2
    assert delays == [1]
    assert retries == [(1, 3, 1, "empty model response")]


@pytest.mark.asyncio
async def test_chat_with_retry_does_not_retry_non_transient_error(monkeypatch) -> None:
    provider = ScriptedProvider(
        [
            LLMResponse(content="401 unauthorized", finish_reason="error"),
        ]
    )
    delays: list[int] = []

    async def _fake_sleep(delay: int) -> None:
        delays.append(delay)

    monkeypatch.setattr("nanobot.providers.base.asyncio.sleep", _fake_sleep)

    response = await provider.chat_with_retry(messages=[{"role": "user", "content": "hello"}])

    assert response.content == "401 unauthorized"
    assert provider.calls == 1
    assert delays == []


@pytest.mark.asyncio
async def test_chat_with_retry_returns_final_error_after_retries(monkeypatch) -> None:
    provider = ScriptedProvider(
        [
            LLMResponse(content="429 rate limit a", finish_reason="error"),
            LLMResponse(content="429 rate limit b", finish_reason="error"),
            LLMResponse(content="429 rate limit c", finish_reason="error"),
            LLMResponse(content="503 final server error", finish_reason="error"),
        ]
    )
    delays: list[int] = []

    async def _fake_sleep(delay: int) -> None:
        delays.append(delay)

    monkeypatch.setattr("nanobot.providers.base.asyncio.sleep", _fake_sleep)

    response = await provider.chat_with_retry(messages=[{"role": "user", "content": "hello"}])

    assert response.content == "503 final server error"
    assert provider.calls == 4
    assert delays == [1, 2, 4]


@pytest.mark.asyncio
async def test_chat_with_retry_preserves_cancelled_error() -> None:
    provider = ScriptedProvider([asyncio.CancelledError()])

    with pytest.raises(asyncio.CancelledError):
        await provider.chat_with_retry(messages=[{"role": "user", "content": "hello"}])


@pytest.mark.asyncio
async def test_chat_with_retry_uses_provider_generation_defaults() -> None:
    """When callers omit generation params, provider.generation defaults are used."""
    provider = ScriptedProvider([LLMResponse(content="ok")])
    provider.generation = GenerationSettings(
        temperature=0.2, max_tokens=321, reasoning_effort="high"
    )

    await provider.chat_with_retry(messages=[{"role": "user", "content": "hello"}])

    assert provider.last_kwargs["temperature"] == 0.2
    assert provider.last_kwargs["max_tokens"] == 321
    assert provider.last_kwargs["reasoning_effort"] == "high"


@pytest.mark.asyncio
async def test_chat_with_retry_explicit_override_beats_defaults() -> None:
    """Explicit kwargs should override provider.generation defaults."""
    provider = ScriptedProvider([LLMResponse(content="ok")])
    provider.generation = GenerationSettings(
        temperature=0.2, max_tokens=321, reasoning_effort="high"
    )

    await provider.chat_with_retry(
        messages=[{"role": "user", "content": "hello"}],
        temperature=0.9,
        max_tokens=9999,
        reasoning_effort="low",
    )

    assert provider.last_kwargs["temperature"] == 0.9
    assert provider.last_kwargs["max_tokens"] == 9999
    assert provider.last_kwargs["reasoning_effort"] == "low"


# ---------------------------------------------------------------------------
# Image fallback tests
# ---------------------------------------------------------------------------

_IMAGE_MSG = [
    {
        "role": "user",
        "content": [
            {"type": "text", "text": "describe this"},
            {
                "type": "image_url",
                "image_url": {"url": "data:image/png;base64,abc"},
                "_meta": {"path": "/media/test.png"},
            },
        ],
    },
]

_IMAGE_MSG_NO_META = [
    {
        "role": "user",
        "content": [
            {"type": "text", "text": "describe this"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}},
        ],
    },
]


@pytest.mark.asyncio
async def test_non_transient_error_with_images_retries_without_images() -> None:
    """Any non-transient error retries once with images stripped when images are present."""
    provider = ScriptedProvider(
        [
            LLMResponse(content="API调用参数有误,请检查文档", finish_reason="error"),
            LLMResponse(content="ok, no image"),
        ]
    )

    response = await provider.chat_with_retry(messages=_IMAGE_MSG)

    assert response.content == "ok, no image"
    assert provider.calls == 2
    msgs_on_retry = provider.last_kwargs["messages"]
    for msg in msgs_on_retry:
        content = msg.get("content")
        if isinstance(content, list):
            assert all(b.get("type") != "image_url" for b in content)
            assert any("[image: /media/test.png]" in (b.get("text") or "") for b in content)


@pytest.mark.asyncio
async def test_non_transient_error_without_images_no_retry() -> None:
    """Non-transient errors without image content are returned immediately."""
    provider = ScriptedProvider(
        [
            LLMResponse(content="401 unauthorized", finish_reason="error"),
        ]
    )

    response = await provider.chat_with_retry(
        messages=[{"role": "user", "content": "hello"}],
    )

    assert provider.calls == 1
    assert response.finish_reason == "error"


@pytest.mark.asyncio
async def test_image_fallback_returns_error_on_second_failure() -> None:
    """If the image-stripped retry also fails, return that error."""
    provider = ScriptedProvider(
        [
            LLMResponse(content="some model error", finish_reason="error"),
            LLMResponse(content="still failing", finish_reason="error"),
        ]
    )

    response = await provider.chat_with_retry(messages=_IMAGE_MSG)

    assert provider.calls == 2
    assert response.content == "still failing"
    assert response.finish_reason == "error"


@pytest.mark.asyncio
async def test_chat_stream_with_retry_buffers_failed_attempt_deltas(monkeypatch) -> None:
    from nanobot.providers.base import LLMProvider

    class ScriptedStreamingProvider(LLMProvider):
        def __init__(self):
            super().__init__()
            self.calls = 0

        async def chat(self, *args, **kwargs) -> LLMResponse:
            raise NotImplementedError

        async def chat_stream(self, *args, on_content_delta=None, **kwargs) -> LLMResponse:
            self.calls += 1
            if self.calls == 1:
                if on_content_delta:
                    await on_content_delta("partial leaked text ")
                return LLMResponse(
                    content="Error calling LLM: Request timed out.", finish_reason="error"
                )
            if on_content_delta:
                await on_content_delta("Hello")
            return LLMResponse(content="Hello", finish_reason="stop")

        def get_default_model(self) -> str:
            return "test-model"

    provider = ScriptedStreamingProvider()
    deltas: list[str] = []

    async def _capture_delta(delta: str) -> None:
        deltas.append(delta)

    async def _fake_sleep(delay: int) -> None:
        return None

    monkeypatch.setattr("nanobot.providers.base.asyncio.sleep", _fake_sleep)

    response = await provider.chat_stream_with_retry(
        messages=[{"role": "user", "content": "hello"}],
        on_content_delta=_capture_delta,
    )

    assert provider.calls == 2
    assert response.content == "Hello"
    assert deltas == ["Hello"]


@pytest.mark.asyncio
async def test_image_fallback_without_meta_uses_default_placeholder() -> None:
    """When _meta is absent, fallback placeholder is '[image omitted]'."""
    provider = ScriptedProvider(
        [
            LLMResponse(content="error", finish_reason="error"),
            LLMResponse(content="ok"),
        ]
    )

    response = await provider.chat_with_retry(messages=_IMAGE_MSG_NO_META)

    assert response.content == "ok"
    assert provider.calls == 2
    msgs_on_retry = provider.last_kwargs["messages"]
    for msg in msgs_on_retry:
        content = msg.get("content")
        if isinstance(content, list):
            assert any("[image omitted]" in (b.get("text") or "") for b in content)
