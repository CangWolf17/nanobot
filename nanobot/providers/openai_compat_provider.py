"""OpenAI-compatible provider for all non-Anthropic LLM APIs."""

from __future__ import annotations

import asyncio
import hashlib
import os
import secrets
import string
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

import json_repair
from openai import AsyncOpenAI

from nanobot.providers.base import LLMProvider, LLMResponse, ToolCallRequest

if TYPE_CHECKING:
    from nanobot.providers.registry import ProviderSpec

_ALLOWED_MSG_KEYS = frozenset({
    "role", "content", "tool_calls", "tool_call_id", "name",
    "reasoning_content", "extra_content",
})
_ALNUM = string.ascii_letters + string.digits

_STANDARD_TC_KEYS = frozenset({"id", "type", "index", "function"})
_STANDARD_FN_KEYS = frozenset({"name", "arguments"})
_DEFAULT_OPENROUTER_HEADERS = {
    "HTTP-Referer": "https://github.com/HKUDS/nanobot",
    "X-OpenRouter-Title": "nanobot",
    "X-OpenRouter-Categories": "cli-agent,personal-agent",
}
_RESPONSES_FAILURE_THRESHOLD = 3
_RESPONSES_PROBE_INTERVAL_S = 300  # 5 minutes


async def _next_with_timeout(stream_iter: Any, timeout_s: float) -> Any:
    task = asyncio.create_task(stream_iter.__anext__())
    try:
        return await asyncio.wait_for(task, timeout=timeout_s)
    except Exception:
        if not task.done():
            task.cancel()
            try:
                await task
            except Exception:
                pass
        raise


def _short_tool_id() -> str:
    """9-char alphanumeric ID compatible with all providers (incl. Mistral)."""
    return "".join(secrets.choice(_ALNUM) for _ in range(9))


def _get(obj: Any, key: str) -> Any:
    """Get a value from dict or object attribute, returning None if absent."""
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)


def _coerce_dict(value: Any) -> dict[str, Any] | None:
    """Try to coerce *value* to a dict; return None if not possible or empty."""
    if value is None:
        return None
    if isinstance(value, dict):
        return value if value else None
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        dumped = model_dump()
        if isinstance(dumped, dict) and dumped:
            return dumped
    return None


def _extract_tc_extras(tc: Any) -> tuple[
    dict[str, Any] | None,
    dict[str, Any] | None,
    dict[str, Any] | None,
]:
    """Extract (extra_content, provider_specific_fields, fn_provider_specific_fields).

    Works for both SDK objects and dicts.  Captures Gemini ``extra_content``
    verbatim and any non-standard keys on the tool-call / function.
    """
    extra_content = _coerce_dict(_get(tc, "extra_content"))

    tc_dict = _coerce_dict(tc)
    prov = None
    fn_prov = None
    if tc_dict is not None:
        leftover = {k: v for k, v in tc_dict.items()
                    if k not in _STANDARD_TC_KEYS and k != "extra_content" and v is not None}
        if leftover:
            prov = leftover
        fn = _coerce_dict(tc_dict.get("function"))
        if fn is not None:
            fn_leftover = {k: v for k, v in fn.items()
                          if k not in _STANDARD_FN_KEYS and v is not None}
            if fn_leftover:
                fn_prov = fn_leftover
    else:
        prov = _coerce_dict(_get(tc, "provider_specific_fields"))
        fn_obj = _get(tc, "function")
        if fn_obj is not None:
            fn_prov = _coerce_dict(_get(fn_obj, "provider_specific_fields"))

    return extra_content, prov, fn_prov


def _uses_openrouter_attribution(spec: "ProviderSpec | None", api_base: str | None) -> bool:
    """Apply Nanobot attribution headers to OpenRouter requests by default."""
    if spec and spec.name == "openrouter":
        return True
    return bool(api_base and "openrouter" in api_base.lower())


def _is_direct_openai_base(api_base: str | None) -> bool:
    """Return True for direct OpenAI endpoints, not generic OpenAI-compatible gateways."""
    if not api_base:
        return True
    normalized = api_base.strip().lower().rstrip("/")
    return "api.openai.com" in normalized and "openrouter" not in normalized


class OpenAICompatProvider(LLMProvider):
    """Unified provider for all OpenAI-compatible APIs.

    Receives a resolved ``ProviderSpec`` from the caller — no internal
    registry lookups needed.
    """

    def __init__(
        self,
        api_key: str | None = None,
        api_base: str | None = None,
        default_model: str = "gpt-4o",
        extra_headers: dict[str, str] | None = None,
        spec: ProviderSpec | None = None,
    ):
        super().__init__(api_key, api_base)
        self.default_model = default_model
        self.extra_headers = extra_headers or {}
        self._spec = spec

        if api_key and spec and spec.env_key:
            self._setup_env(api_key, api_base)

        effective_base = api_base or (spec.default_api_base if spec else None) or None
        self._effective_base = effective_base
        default_headers = {"x-session-affinity": uuid.uuid4().hex}
        if _uses_openrouter_attribution(spec, effective_base):
            default_headers.update(_DEFAULT_OPENROUTER_HEADERS)
        if extra_headers:
            default_headers.update(extra_headers)

        self._client = AsyncOpenAI(
            api_key=api_key or "no-key",
            base_url=effective_base,
            default_headers=default_headers,
            timeout=60.0,
            max_retries=0,
        )
        self._responses_failures: dict[str, int] = {}
        self._responses_tripped_at: dict[str, float] = {}

    def _setup_env(self, api_key: str, api_base: str | None) -> None:
        """Set environment variables based on provider spec."""
        spec = self._spec
        if not spec or not spec.env_key:
            return
        if spec.is_gateway:
            os.environ[spec.env_key] = api_key
        else:
            os.environ.setdefault(spec.env_key, api_key)
        effective_base = api_base or spec.default_api_base
        for env_name, env_val in spec.env_extras:
            resolved = env_val.replace("{api_key}", api_key).replace("{api_base}", effective_base)
            os.environ.setdefault(env_name, resolved)

    @classmethod
    def _apply_cache_control(
        cls,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]] | None]:
        """Inject cache_control markers for prompt caching."""
        cache_marker = {"type": "ephemeral"}
        new_messages = list(messages)

        def _mark(msg: dict[str, Any]) -> dict[str, Any]:
            content = msg.get("content")
            if isinstance(content, str):
                return {**msg, "content": [
                    {"type": "text", "text": content, "cache_control": cache_marker},
                ]}
            if isinstance(content, list) and content:
                nc = list(content)
                nc[-1] = {**nc[-1], "cache_control": cache_marker}
                return {**msg, "content": nc}
            return msg

        if new_messages and new_messages[0].get("role") == "system":
            new_messages[0] = _mark(new_messages[0])
        if len(new_messages) >= 3:
            new_messages[-2] = _mark(new_messages[-2])

        new_tools = tools
        if tools:
            new_tools = list(tools)
            for idx in cls._tool_cache_marker_indices(new_tools):
                new_tools[idx] = {**new_tools[idx], "cache_control": cache_marker}
        return new_messages, new_tools

    @staticmethod
    def _normalize_tool_call_id(tool_call_id: Any) -> Any:
        """Normalize to a provider-safe 9-char alphanumeric form."""
        if not isinstance(tool_call_id, str):
            return tool_call_id
        if len(tool_call_id) == 9 and tool_call_id.isalnum():
            return tool_call_id
        return hashlib.sha1(tool_call_id.encode()).hexdigest()[:9]

    def _sanitize_messages(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Strip non-standard keys, normalize tool_call IDs."""
        messages = LLMProvider._enforce_role_alternation(messages)
        sanitized = LLMProvider._sanitize_request_messages(messages, _ALLOWED_MSG_KEYS)
        id_map: dict[str, str] = {}

        def map_id(value: Any) -> Any:
            if not isinstance(value, str):
                return value
            return id_map.setdefault(value, self._normalize_tool_call_id(value))

        for clean in sanitized:
            if clean.get("role") == "assistant" and clean.get("tool_calls"):
                clean["content"] = None
            if isinstance(clean.get("tool_calls"), list):
                normalized = []
                for tc in clean["tool_calls"]:
                    if not isinstance(tc, dict):
                        normalized.append(tc)
                        continue
                    tc_clean = dict(tc)
                    tc_clean["id"] = map_id(tc_clean.get("id"))
                    normalized.append(tc_clean)
                clean["tool_calls"] = normalized
            if "tool_call_id" in clean and clean["tool_call_id"]:
                clean["tool_call_id"] = map_id(clean["tool_call_id"])
        return sanitized

    # ------------------------------------------------------------------
    # Build kwargs
    # ------------------------------------------------------------------

    def _build_kwargs(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        model: str | None,
        max_tokens: int,
        temperature: float,
        reasoning_effort: str | None,
        tool_choice: str | dict[str, Any] | None,
    ) -> dict[str, Any]:
        model_name = model or self.default_model
        spec = self._spec

        if spec and spec.supports_prompt_caching:
            messages, tools = self._apply_cache_control(messages, tools)

        if spec and spec.strip_model_prefix:
            model_name = model_name.split("/")[-1]

        kwargs: dict[str, Any] = {
            "model": model_name,
            "messages": self._sanitize_messages(self._sanitize_empty_content(messages)),
        }

        if self._supports_temperature(model_name, reasoning_effort):
            kwargs["temperature"] = temperature

        if spec and getattr(spec, "supports_max_completion_tokens", False):
            kwargs["max_completion_tokens"] = max(1, max_tokens)
        else:
            kwargs["max_tokens"] = max(1, max_tokens)

        if spec:
            model_lower = model_name.lower()
            for pattern, overrides in spec.model_overrides:
                if pattern in model_lower:
                    kwargs.update(overrides)
                    break

        if reasoning_effort:
            kwargs["reasoning_effort"] = reasoning_effort

        extra_body = self._reasoning_extra_body(reasoning_effort)
        if extra_body:
            kwargs["extra_body"] = extra_body

        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = tool_choice or "auto"

        return kwargs

    @staticmethod
    def _supports_temperature(
        model_name: str,
        reasoning_effort: str | None = None,
    ) -> bool:
        if reasoning_effort:
            return False
        model_lower = model_name.lower()
        return not any(token in model_lower for token in ("gpt-5", "o1", "o3", "o4"))

    def _should_use_responses_api(
        self,
        model_name: str,
        reasoning_effort: str | None = None,
    ) -> bool:
        if getattr(self._spec, "name", None) != "openai":
            return False
        if not _is_direct_openai_base(getattr(self, "_effective_base", None)):
            return False
        model_lower = model_name.lower()
        normalized_reasoning = str(reasoning_effort or "").strip().lower()
        wants_responses = bool(normalized_reasoning) or any(
            token in model_lower for token in ("gpt-5", "o1", "o3", "o4")
        )
        if not wants_responses:
            return False
        key = self._responses_circuit_key(model_name, reasoning_effort)
        failures = self._responses_failures.get(key, 0)
        if failures < _RESPONSES_FAILURE_THRESHOLD:
            return True
        tripped_at = self._responses_tripped_at.get(key, 0.0)
        return (time.monotonic() - tripped_at) >= _RESPONSES_PROBE_INTERVAL_S

    @staticmethod
    def _responses_circuit_key(model_name: str | None, reasoning_effort: str | None) -> str:
        normalized_model = str(model_name or "").strip().lower()
        normalized_reasoning = str(reasoning_effort or "").strip().lower()
        return f"{normalized_model}:{normalized_reasoning}"

    def _record_responses_failure(self, model_name: str | None, reasoning_effort: str | None) -> None:
        key = self._responses_circuit_key(model_name or self.default_model, reasoning_effort)
        failures = self._responses_failures.get(key, 0) + 1
        self._responses_failures[key] = failures
        if failures >= _RESPONSES_FAILURE_THRESHOLD:
            self._responses_tripped_at[key] = time.monotonic()

    def _record_responses_success(self, model_name: str | None, reasoning_effort: str | None) -> None:
        key = self._responses_circuit_key(model_name or self.default_model, reasoning_effort)
        self._responses_failures.pop(key, None)
        self._responses_tripped_at.pop(key, None)

    @classmethod
    def _extract_content_and_reasoning(cls, payload: Any) -> tuple[str | None, str | None]:
        """Extract visible content and hidden reasoning with StepFun-style fallback."""
        reasoning_fallback = cls._extract_text_content(_get(payload, "reasoning"))
        content = cls._extract_text_content(_get(payload, "content")) or reasoning_fallback
        reasoning = cls._extract_text_content(_get(payload, "reasoning_content")) or reasoning_fallback
        return content, reasoning

    @classmethod
    def _extract_reasoning_delta(cls, payload: Any) -> str | None:
        """Extract only reasoning text from streaming delta payloads."""
        return (
            cls._extract_text_content(_get(payload, "reasoning_content"))
            or cls._extract_text_content(_get(payload, "reasoning"))
        )

    @classmethod
    def _extract_error_metadata(
        cls,
        e: Exception,
        *,
        payload: Any,
        headers: Any,
        response: Any,
    ) -> dict[str, Any]:
        status_code = getattr(e, "status_code", None)
        if status_code is None and response is not None:
            status_code = getattr(response, "status_code", None)

        should_retry: bool | None = None
        if headers is not None:
            raw = headers.get("x-should-retry")
            if isinstance(raw, str):
                lowered = raw.strip().lower()
                if lowered == "true":
                    should_retry = True
                elif lowered == "false":
                    should_retry = False

        error_kind: str | None = None
        error_name = e.__class__.__name__.lower()
        if "timeout" in error_name:
            error_kind = "timeout"
        elif "connection" in error_name:
            error_kind = "connection"

        error_type, error_code = LLMProvider._extract_error_type_code(payload)
        return {
            "error_status_code": int(status_code) if status_code is not None else None,
            "error_kind": error_kind,
            "error_type": error_type,
            "error_code": error_code,
            "error_should_retry": should_retry,
        }

    @staticmethod
    def _should_fallback_from_responses(exc: Exception) -> bool:
        status_code = getattr(exc, "status_code", None)
        if status_code is None:
            status_code = getattr(getattr(exc, "response", None), "status_code", None)
        if status_code == 404:
            return True
        body = str(getattr(getattr(exc, "response", None), "text", "") or exc)
        return status_code == 400 and "Unknown parameter" in body

    def _build_responses_kwargs(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        model: str | None,
        max_tokens: int,
        reasoning_effort: str | None,
        tool_choice: str | dict[str, Any] | None,
        *,
        stream: bool = False,
    ) -> dict[str, Any]:
        model_name = model or self.default_model
        kwargs: dict[str, Any] = {
            "model": model_name,
            "input": self._sanitize_messages(self._sanitize_empty_content(messages)),
            "max_output_tokens": max(1, max_tokens),
        }
        if reasoning_effort:
            kwargs["reasoning"] = {"effort": reasoning_effort}
            kwargs["include"] = ["reasoning.encrypted_content"]
        if tools:
            kwargs["tools"] = tools
            if tool_choice is not None:
                kwargs["tool_choice"] = tool_choice
        if stream:
            kwargs["stream"] = True
        return kwargs

    def _reasoning_extra_body(self, reasoning_effort: str | None) -> dict[str, Any] | None:
        if not reasoning_effort:
            return None
        provider_name = getattr(self._spec, "name", None)
        if provider_name == "dashscope":
            return {"enable_thinking": reasoning_effort != "minimal"}
        if provider_name in {"volcengine", "byteplus"}:
            return {"thinking": {"type": "disabled" if reasoning_effort == "minimal" else "enabled"}}
        return None

    # ------------------------------------------------------------------
    # Response parsing
    # ------------------------------------------------------------------

    @staticmethod
    def _maybe_mapping(value: Any) -> dict[str, Any] | None:
        if isinstance(value, dict):
            return value
        model_dump = getattr(value, "model_dump", None)
        if callable(model_dump):
            dumped = model_dump()
            if isinstance(dumped, dict):
                return dumped
        return None

    @classmethod
    def _extract_text_content(cls, value: Any) -> str | None:
        if value is None:
            return None
        if isinstance(value, str):
            return value
        if isinstance(value, list):
            parts: list[str] = []
            for item in value:
                item_map = cls._maybe_mapping(item)
                if item_map:
                    text = item_map.get("text")
                    if isinstance(text, str):
                        parts.append(text)
                        continue
                text = getattr(item, "text", None)
                if isinstance(text, str):
                    parts.append(text)
                    continue
                if isinstance(item, str):
                    parts.append(item)
            return "".join(parts) or None
        return str(value)

    @classmethod
    def _extract_usage(cls, response: Any) -> dict[str, int]:
        usage_obj = None
        response_map = cls._maybe_mapping(response)
        if response_map is not None:
            usage_obj = response_map.get("usage")
        elif hasattr(response, "usage") and response.usage:
            usage_obj = response.usage

        usage_map = cls._maybe_mapping(usage_obj)
        if usage_map is not None:
            usage: dict[str, int] = {
                "prompt_tokens": int(usage_map.get("prompt_tokens") or 0),
                "completion_tokens": int(usage_map.get("completion_tokens") or 0),
                "total_tokens": int(usage_map.get("total_tokens") or 0),
            }
            prompt_details = cls._maybe_mapping(usage_map.get("prompt_tokens_details")) or {}
            cached_tokens = (
                prompt_details.get("cached_tokens")
                or usage_map.get("prompt_cache_hit_tokens")
                or usage_map.get("cached_tokens")
                or 0
            )
            if cached_tokens:
                usage["cached_tokens"] = int(cached_tokens)
            return usage

        if usage_obj:
            usage = {
                "prompt_tokens": int(getattr(usage_obj, "prompt_tokens", 0) or 0),
                "completion_tokens": int(getattr(usage_obj, "completion_tokens", 0) or 0),
                "total_tokens": int(getattr(usage_obj, "total_tokens", 0) or 0),
            }
            prompt_details = getattr(usage_obj, "prompt_tokens_details", None)
            cached_tokens = (
                getattr(prompt_details, "cached_tokens", 0) if prompt_details is not None else 0
            ) or getattr(usage_obj, "prompt_cache_hit_tokens", 0) or getattr(usage_obj, "cached_tokens", 0) or 0
            if cached_tokens:
                usage["cached_tokens"] = int(cached_tokens)
            return usage
        return {}

    @classmethod
    def _parse_responses_api(cls, response: Any) -> LLMResponse:
        response_map = cls._maybe_mapping(response) or {}
        output = response_map.get("output") or []
        content_parts: list[str] = []
        for item in output:
            item_map = cls._maybe_mapping(item) or {}
            if item_map.get("type") != "message":
                continue
            for part in item_map.get("content") or []:
                part_map = cls._maybe_mapping(part) or {}
                if part_map.get("type") == "output_text":
                    text = part_map.get("text")
                    if isinstance(text, str):
                        content_parts.append(text)
        usage_map = cls._maybe_mapping(response_map.get("usage")) or {}
        usage = {
            "prompt_tokens": int(usage_map.get("input_tokens") or 0),
            "completion_tokens": int(usage_map.get("output_tokens") or 0),
            "total_tokens": int(usage_map.get("total_tokens") or 0),
        }
        status = str(response_map.get("status") or "").strip().lower()
        finish_reason = "stop" if status in {"completed", "succeeded"} else (status or None)
        return LLMResponse(
            content="".join(content_parts) or None,
            finish_reason=finish_reason,
            usage=usage,
        )

    @classmethod
    async def _parse_responses_stream(
        cls,
        stream: Any,
        on_content_delta: Callable[[str], Awaitable[None]] | None = None,
        idle_timeout_s: int = 90,
    ) -> LLMResponse:
        content_parts: list[str] = []
        finish_reason: str | None = None
        usage: dict[str, int] = {}
        stream_iter = stream.__aiter__()
        effective_timeout_s = max(float(idle_timeout_s), 0.001)
        while True:
            try:
                event = await _next_with_timeout(stream_iter, effective_timeout_s)
            except StopAsyncIteration:
                break
            event_type = getattr(event, "type", "")
            if event_type == "response.output_text.delta":
                text = getattr(event, "delta", None)
                if isinstance(text, str):
                    content_parts.append(text)
                    if on_content_delta:
                        await on_content_delta(text)
                continue
            if event_type == "response.completed":
                response = getattr(event, "response", None)
                response_map = cls._maybe_mapping(response)
                if response_map is not None:
                    usage_map = cls._maybe_mapping(response_map.get("usage")) or {}
                    usage = {
                        "prompt_tokens": int(usage_map.get("input_tokens") or 0),
                        "completion_tokens": int(usage_map.get("output_tokens") or 0),
                        "total_tokens": int(usage_map.get("total_tokens") or 0),
                    }
                    status = str(response_map.get("status") or "").strip().lower()
                else:
                    raw_usage = getattr(response, "usage", None)
                    usage = {
                        "prompt_tokens": int(getattr(raw_usage, "input_tokens", 0) or 0),
                        "completion_tokens": int(getattr(raw_usage, "output_tokens", 0) or 0),
                        "total_tokens": int(getattr(raw_usage, "total_tokens", 0) or 0),
                    }
                    status = str(getattr(response, "status", "") or "").strip().lower()
                finish_reason = "stop" if status in {"completed", "succeeded"} else (status or None)
        return LLMResponse(
            content="".join(content_parts) or None,
            finish_reason=finish_reason,
            usage=usage,
        )

    def _parse(self, response: Any) -> LLMResponse:
        if isinstance(response, str):
            return LLMResponse(content=response, finish_reason="stop")

        response_map = self._maybe_mapping(response)
        if response_map is not None:
            choices = response_map.get("choices") or []
            if not choices:
                content = self._extract_text_content(
                    response_map.get("content") or response_map.get("output_text")
                )
                if content is not None:
                    return LLMResponse(
                        content=content,
                        finish_reason=str(response_map.get("finish_reason") or "stop"),
                        usage=self._extract_usage(response_map),
                    )
                return LLMResponse(content="Error: API returned empty choices.", finish_reason="error")

            choice0 = self._maybe_mapping(choices[0]) or {}
            msg0 = self._maybe_mapping(choice0.get("message")) or {}
            content, reasoning_content = self._extract_content_and_reasoning(msg0)
            finish_reason = str(choice0.get("finish_reason") or "stop")

            raw_tool_calls: list[Any] = []
            for ch in choices:
                ch_map = self._maybe_mapping(ch) or {}
                m = self._maybe_mapping(ch_map.get("message")) or {}
                tool_calls = m.get("tool_calls")
                if isinstance(tool_calls, list) and tool_calls:
                    raw_tool_calls.extend(tool_calls)
                    if ch_map.get("finish_reason") in ("tool_calls", "stop"):
                        finish_reason = str(ch_map["finish_reason"])
                candidate_content, candidate_reasoning = self._extract_content_and_reasoning(m)
                if not content:
                    content = candidate_content
                if not reasoning_content:
                    reasoning_content = candidate_reasoning

            parsed_tool_calls = []
            for tc in raw_tool_calls:
                tc_map = self._maybe_mapping(tc) or {}
                fn = self._maybe_mapping(tc_map.get("function")) or {}
                args = fn.get("arguments", {})
                if isinstance(args, str):
                    args = json_repair.loads(args)
                ec, prov, fn_prov = _extract_tc_extras(tc)
                parsed_tool_calls.append(ToolCallRequest(
                    id=_short_tool_id(),
                    name=str(fn.get("name") or ""),
                    arguments=args if isinstance(args, dict) else {},
                    extra_content=ec,
                    provider_specific_fields=prov,
                    function_provider_specific_fields=fn_prov,
                ))

            return LLMResponse(
                content=content,
                tool_calls=parsed_tool_calls,
                finish_reason=finish_reason,
                usage=self._extract_usage(response_map),
                reasoning_content=reasoning_content if isinstance(reasoning_content, str) else None,
            )

        if not response.choices:
            return LLMResponse(content="Error: API returned empty choices.", finish_reason="error")

        choice = response.choices[0]
        msg = choice.message
        content, reasoning_content = self._extract_content_and_reasoning(msg)
        finish_reason = choice.finish_reason

        raw_tool_calls: list[Any] = []
        for ch in response.choices:
            m = ch.message
            if hasattr(m, "tool_calls") and m.tool_calls:
                raw_tool_calls.extend(m.tool_calls)
                if ch.finish_reason in ("tool_calls", "stop"):
                    finish_reason = ch.finish_reason
            candidate_content, _ = self._extract_content_and_reasoning(m)
            if not content:
                content = candidate_content

        tool_calls = []
        for tc in raw_tool_calls:
            args = tc.function.arguments
            if isinstance(args, str):
                args = json_repair.loads(args)
            ec, prov, fn_prov = _extract_tc_extras(tc)
            tool_calls.append(ToolCallRequest(
                id=_short_tool_id(),
                name=tc.function.name,
                arguments=args,
                extra_content=ec,
                provider_specific_fields=prov,
                function_provider_specific_fields=fn_prov,
            ))

        return LLMResponse(
            content=content,
            tool_calls=tool_calls,
            finish_reason=finish_reason or "stop",
            usage=self._extract_usage(response),
            reasoning_content=reasoning_content,
        )

    @classmethod
    def _parse_chunks(cls, chunks: list[Any]) -> LLMResponse:
        content_parts: list[str] = []
        reasoning_parts: list[str] = []
        tc_bufs: dict[int, dict[str, Any]] = {}
        finish_reason = "stop"
        usage: dict[str, int] = {}

        def _accum_tc(tc: Any, idx_hint: int) -> None:
            """Accumulate one streaming tool-call delta into *tc_bufs*."""
            tc_index: int = _get(tc, "index") if _get(tc, "index") is not None else idx_hint
            buf = tc_bufs.setdefault(tc_index, {
                "id": "", "name": "", "arguments": "",
                "extra_content": None, "prov": None, "fn_prov": None,
            })
            tc_id = _get(tc, "id")
            if tc_id:
                buf["id"] = str(tc_id)
            fn = _get(tc, "function")
            if fn is not None:
                fn_name = _get(fn, "name")
                if fn_name:
                    buf["name"] = str(fn_name)
                fn_args = _get(fn, "arguments")
                if fn_args:
                    buf["arguments"] += str(fn_args)
            ec, prov, fn_prov = _extract_tc_extras(tc)
            if ec:
                buf["extra_content"] = ec
            if prov:
                buf["prov"] = prov
            if fn_prov:
                buf["fn_prov"] = fn_prov

        for chunk in chunks:
            if isinstance(chunk, str):
                content_parts.append(chunk)
                continue

            chunk_map = cls._maybe_mapping(chunk)
            if chunk_map is not None:
                choices = chunk_map.get("choices") or []
                if not choices:
                    usage = cls._extract_usage(chunk_map) or usage
                    text = cls._extract_text_content(
                        chunk_map.get("content") or chunk_map.get("output_text")
                    )
                    if text:
                        content_parts.append(text)
                    continue
                choice = cls._maybe_mapping(choices[0]) or {}
                if choice.get("finish_reason"):
                    finish_reason = str(choice["finish_reason"])
                delta = cls._maybe_mapping(choice.get("delta")) or {}
                text = cls._extract_text_content(delta.get("content"))
                if text:
                    content_parts.append(text)
                if reasoning := cls._extract_reasoning_delta(delta):
                    reasoning_parts.append(reasoning)
                for idx, tc in enumerate(delta.get("tool_calls") or []):
                    _accum_tc(tc, idx)
                usage = cls._extract_usage(chunk_map) or usage
                continue

            if not chunk.choices:
                usage = cls._extract_usage(chunk) or usage
                continue
            choice = chunk.choices[0]
            if choice.finish_reason:
                finish_reason = choice.finish_reason
            delta = choice.delta
            if delta and delta.content:
                content_parts.append(delta.content)
            if delta and (reasoning := cls._extract_reasoning_delta(delta)):
                reasoning_parts.append(reasoning)
            for tc in (delta.tool_calls or []) if delta else []:
                _accum_tc(tc, getattr(tc, "index", 0))

        return LLMResponse(
            content="".join(content_parts) or None,
            tool_calls=[
                ToolCallRequest(
                    id=b["id"] or _short_tool_id(),
                    name=b["name"],
                    arguments=json_repair.loads(b["arguments"]) if b["arguments"] else {},
                    extra_content=b.get("extra_content"),
                    provider_specific_fields=b.get("prov"),
                    function_provider_specific_fields=b.get("fn_prov"),
                )
                for b in tc_bufs.values()
            ],
            finish_reason=finish_reason,
            usage=usage,
            reasoning_content="".join(reasoning_parts) or None,
        )

    @classmethod
    def _handle_error(cls, e: Exception) -> LLMResponse:
        response = getattr(e, "response", None)
        headers = getattr(response, "headers", None)
        payload = (
            getattr(e, "body", None)
            or getattr(e, "doc", None)
            or getattr(response, "text", None)
        )
        if payload is None and response is not None:
            response_json = getattr(response, "json", None)
            if callable(response_json):
                try:
                    payload = response_json()
                except Exception:
                    payload = None
        payload_text = payload if isinstance(payload, str) else str(payload) if payload is not None else ""
        msg = f"Error: {payload_text.strip()[:500]}" if payload_text.strip() else f"Error calling LLM: {e}"
        retry_after = cls._extract_retry_after_from_headers(headers)
        if retry_after is None:
            retry_after = LLMProvider._extract_retry_after(msg)

        return LLMResponse(
            content=msg,
            finish_reason="error",
            retry_after=retry_after,
            error_retry_after_s=retry_after,
            **cls._extract_error_metadata(
                e,
                payload=payload,
                headers=headers,
                response=response,
            ),
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        reasoning_effort: str | None = None,
        tool_choice: str | dict[str, Any] | None = None,
    ) -> LLMResponse:
        model_name = model or self.default_model
        if self._should_use_responses_api(model_name, reasoning_effort):
            responses_kwargs = self._build_responses_kwargs(
                messages,
                tools,
                model_name,
                max_tokens,
                reasoning_effort,
                tool_choice,
            )
            try:
                response = self._parse_responses_api(
                    await self._client.responses.create(**responses_kwargs)
                )
                self._record_responses_success(model_name, reasoning_effort)
                return response
            except Exception as e:
                self._record_responses_failure(model_name, reasoning_effort)
                if not self._should_fallback_from_responses(e):
                    return self._handle_error(e)
        kwargs = self._build_kwargs(
            messages, tools, model_name, max_tokens, temperature,
            reasoning_effort, tool_choice,
        )
        try:
            return self._parse(await self._client.chat.completions.create(**kwargs))
        except Exception as e:
            return self._handle_error(e)

    async def chat_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        reasoning_effort: str | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        on_content_delta: Callable[[str], Awaitable[None]] | None = None,
    ) -> LLMResponse:
        model_name = model or self.default_model
        idle_timeout_s = int(os.environ.get("NANOBOT_STREAM_IDLE_TIMEOUT_S", "90"))
        if self._should_use_responses_api(model_name, reasoning_effort):
            responses_kwargs = self._build_responses_kwargs(
                messages,
                tools,
                model_name,
                max_tokens,
                reasoning_effort,
                tool_choice,
                stream=True,
            )
            try:
                stream = await self._client.responses.create(**responses_kwargs)
                response = await self._parse_responses_stream(
                    stream,
                    on_content_delta,
                    idle_timeout_s=idle_timeout_s,
                )
                self._record_responses_success(model_name, reasoning_effort)
                return response
            except asyncio.TimeoutError:
                self._record_responses_failure(model_name, reasoning_effort)
                return LLMResponse(
                    content=(
                        f"Error calling LLM: stream stalled for more than "
                        f"{idle_timeout_s} seconds"
                    ),
                    finish_reason="error",
                    error_kind="timeout",
                )
            except Exception as e:
                self._record_responses_failure(model_name, reasoning_effort)
                if not self._should_fallback_from_responses(e):
                    return self._handle_error(e)
        kwargs = self._build_kwargs(
            messages, tools, model_name, max_tokens, temperature,
            reasoning_effort, tool_choice,
        )
        kwargs["stream"] = True
        kwargs["stream_options"] = {"include_usage": True}
        try:
            stream = await self._client.chat.completions.create(**kwargs)
            chunks: list[Any] = []
            stream_iter = stream.__aiter__()
            effective_timeout_s = max(float(idle_timeout_s), 0.001)
            while True:
                try:
                    chunk = await _next_with_timeout(stream_iter, effective_timeout_s)
                except StopAsyncIteration:
                    break
                chunks.append(chunk)
                if on_content_delta and chunk.choices:
                    text = getattr(chunk.choices[0].delta, "content", None)
                    if text:
                        await on_content_delta(text)
            return self._parse_chunks(chunks)
        except asyncio.TimeoutError:
            return LLMResponse(
                content=(
                    f"Error calling LLM: stream stalled for more than "
                    f"{idle_timeout_s} seconds"
                ),
                finish_reason="error",
                error_kind="timeout",
            )
        except Exception as e:
            return self._handle_error(e)

    def get_default_model(self) -> str:
        return self.default_model
