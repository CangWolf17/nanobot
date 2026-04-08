"""Generic OpenAI Responses API provider."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from openai import AsyncOpenAI

from nanobot.providers.base import LLMProvider, LLMResponse
from nanobot.providers.openai_responses import (
    consume_sdk_stream,
    convert_messages,
    convert_tools,
    parse_response_output,
)


class OpenAIResponsesProvider(LLMProvider):
    """Use a generic OpenAI-compatible Responses API endpoint."""

    def __init__(
        self,
        api_key: str | None = None,
        api_base: str | None = None,
        default_model: str = "gpt-4.1-mini",
        extra_headers: dict[str, str] | None = None,
    ):
        super().__init__(api_key, api_base)
        self.default_model = default_model
        self.extra_headers = extra_headers or {}
        self._client = AsyncOpenAI(
            api_key=api_key or "no-key",
            base_url=api_base,
            default_headers=self.extra_headers or None,
            timeout=60.0,
        )

    async def _call_responses(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        model: str | None,
        max_tokens: int,
        temperature: float,
        reasoning_effort: str | None,
        tool_choice: str | dict[str, Any] | None,
        on_content_delta: Callable[[str], Awaitable[None]] | None = None,
    ) -> LLMResponse:
        model = model or self.default_model
        system_prompt, input_items = convert_messages(messages)

        payload: dict[str, Any] = {
            "model": model,
            "input": input_items,
            "stream": on_content_delta is not None,
            "tool_choice": tool_choice or "auto",
            "parallel_tool_calls": True,
        }
        if system_prompt:
            payload["instructions"] = system_prompt
        if reasoning_effort:
            payload["reasoning"] = {"effort": reasoning_effort}
        if max_tokens > 0:
            payload["max_output_tokens"] = max_tokens
        payload["temperature"] = temperature
        if tools:
            payload["tools"] = convert_tools(tools)

        try:
            if on_content_delta is not None:
                payload["stream"] = True
                stream = await self._client.responses.create(**payload)
                content, tool_calls, finish_reason, usage, reasoning_content = await consume_sdk_stream(
                    stream,
                    on_content_delta=on_content_delta,
                )
                return LLMResponse(
                    content=content or None,
                    tool_calls=tool_calls,
                    finish_reason=finish_reason,
                    usage=usage,
                    reasoning_content=reasoning_content,
                )

            payload["stream"] = False
            response = await self._client.responses.create(**payload)
            return parse_response_output(response)
        except Exception as exc:
            return LLMResponse(
                content=f"Error calling OpenAI Responses: {exc}",
                finish_reason="error",
            )

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: Any = LLMProvider._SENTINEL,
        temperature: Any = LLMProvider._SENTINEL,
        reasoning_effort: Any = LLMProvider._SENTINEL,
        tool_choice: str | dict[str, Any] | None = None,
    ) -> LLMResponse:
        if max_tokens is LLMProvider._SENTINEL or max_tokens is None:
            max_tokens = self.generation.max_tokens
        if temperature is LLMProvider._SENTINEL or temperature is None:
            temperature = self.generation.temperature
        if reasoning_effort is LLMProvider._SENTINEL or reasoning_effort is None:
            reasoning_effort = self.generation.reasoning_effort

        return await self._call_responses(
            messages,
            tools,
            model,
            max_tokens,
            temperature,
            reasoning_effort,
            tool_choice,
        )

    async def chat_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: Any = LLMProvider._SENTINEL,
        temperature: Any = LLMProvider._SENTINEL,
        reasoning_effort: Any = LLMProvider._SENTINEL,
        tool_choice: str | dict[str, Any] | None = None,
        on_content_delta: Callable[[str], Awaitable[None]] | None = None,
    ) -> LLMResponse:
        if max_tokens is LLMProvider._SENTINEL or max_tokens is None:
            max_tokens = self.generation.max_tokens
        if temperature is LLMProvider._SENTINEL or temperature is None:
            temperature = self.generation.temperature
        if reasoning_effort is LLMProvider._SENTINEL or reasoning_effort is None:
            reasoning_effort = self.generation.reasoning_effort

        return await self._call_responses(
            messages,
            tools,
            model,
            max_tokens,
            temperature,
            reasoning_effort,
            tool_choice,
            on_content_delta=on_content_delta,
        )

    def get_default_model(self) -> str:
        return self.default_model
