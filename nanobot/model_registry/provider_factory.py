"""Provider construction for resolved registry specs."""

from __future__ import annotations

from typing import Any

from nanobot.config.schema import Config
from nanobot.model_registry.schema import ResolvedModelSpec
from nanobot.providers.base import GenerationSettings, LLMProvider
from nanobot.providers.registry import ProviderSpec, find_by_name, normalize_lookup_name

_ADAPTER_NAMES = {
    normalize_lookup_name(name): name
    for name in ("openai_compat", "anthropic", "azure_openai", "openai_responses", "openai_codex")
}


def _provider_config(config: Config, ref: str) -> Any:
    return getattr(config.providers, ref, None)


def _merged_headers(route_headers: dict[str, str], provider_headers: Any) -> dict[str, str]:
    merged: dict[str, str] = {}
    if isinstance(provider_headers, dict):
        merged.update({str(key): str(value) for key, value in provider_headers.items()})
    merged.update(route_headers)
    return merged


def _effective_api_base(
    spec: ProviderSpec, provider_cfg: Any, route_api_base: str | None
) -> str | None:
    config_api_base = getattr(provider_cfg, "api_base", None) if provider_cfg is not None else None
    return route_api_base or config_api_base or spec.default_api_base or None


def _require_api_key(spec: ProviderSpec, provider_cfg: Any, adapter: str) -> str | None:
    api_key = getattr(provider_cfg, "api_key", None) if provider_cfg is not None else None
    if api_key:
        return api_key
    if spec.is_oauth or spec.is_local or spec.is_direct:
        return api_key
    raise ValueError(f"Adapter '{adapter}' requires an API key for provider '{spec.name}'.")


def build_provider_from_spec(spec: ResolvedModelSpec, config: Config) -> tuple[LLMProvider, str]:
    """Construct a provider instance from a resolved registry spec."""

    route_provider_spec = find_by_name(spec.config_provider_ref)
    if route_provider_spec is None:
        raise ValueError(f"Unknown provider config '{spec.config_provider_ref}'")

    provider_cfg = _provider_config(config, route_provider_spec.name)
    api_key = _require_api_key(route_provider_spec, provider_cfg, spec.adapter)
    extra_headers = _merged_headers(
        spec.extra_headers, getattr(provider_cfg, "extra_headers", None)
    )
    api_base = _effective_api_base(route_provider_spec, provider_cfg, spec.api_base)

    adapter = _ADAPTER_NAMES.get(normalize_lookup_name(spec.adapter), spec.adapter.casefold())

    if adapter == "openai_compat":
        from nanobot.providers.openai_compat_provider import OpenAICompatProvider

        provider = OpenAICompatProvider(
            api_key=api_key,
            api_base=api_base,
            default_model=spec.provider_model,
            extra_headers=extra_headers or None,
            spec=route_provider_spec,
        )
    elif adapter == "anthropic":
        from nanobot.providers.anthropic_provider import AnthropicProvider

        provider = AnthropicProvider(
            api_key=api_key,
            api_base=api_base,
            default_model=spec.provider_model,
            extra_headers=extra_headers or None,
        )
    elif adapter == "azure_openai":
        from nanobot.providers.azure_openai_provider import AzureOpenAIProvider

        if not api_key:
            raise ValueError("Azure OpenAI requires an api_key.")
        if not api_base:
            raise ValueError("Azure OpenAI requires an api_base.")
        provider = AzureOpenAIProvider(
            api_key=api_key,
            api_base=api_base,
            default_model=spec.provider_model,
        )
    elif adapter == "openai_responses":
        from nanobot.providers.openai_responses_provider import OpenAIResponsesProvider

        provider = OpenAIResponsesProvider(
            api_key=api_key,
            api_base=api_base,
            default_model=spec.provider_model,
            extra_headers=extra_headers or None,
        )
    elif adapter == "openai_codex":
        from nanobot.providers.openai_codex_provider import OpenAICodexProvider

        provider = OpenAICodexProvider(default_model=spec.provider_model)
    else:
        raise ValueError(f"Unsupported adapter '{spec.adapter}'")

    defaults = config.agents.defaults
    provider.generation = GenerationSettings(
        temperature=defaults.temperature,
        max_tokens=defaults.max_tokens,
        reasoning_effort=defaults.reasoning_effort,
    )
    if spec.runtime_defaults:
        provider.generation = GenerationSettings(
            temperature=(
                spec.runtime_defaults.get("temperature")
                if spec.runtime_defaults.get("temperature") is not None
                else provider.generation.temperature
            ),
            max_tokens=(
                int(spec.runtime_defaults.get("max_tokens"))
                if spec.runtime_defaults.get("max_tokens") is not None
                else provider.generation.max_tokens
            ),
            reasoning_effort=(
                str(spec.runtime_defaults.get("reasoning_effort"))
                if spec.runtime_defaults.get("reasoning_effort") is not None
                else provider.generation.reasoning_effort
            ),
        )
    return provider, spec.provider_model


__all__ = ["build_provider_from_spec"]
